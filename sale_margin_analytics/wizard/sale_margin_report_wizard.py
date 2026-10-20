import base64
from datetime import datetime, time

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression
from odoo.tools.misc import format_date, format_datetime

from ..report.margin_report_xlsx import MarginXlsxBuilder

GROUP_BY_FIELDS = {
    'order': 'order_id',
    'partner': 'commercial_partner_id',
    'user': 'user_id',
    'team': 'team_id',
    'category': 'categ_id',
    'product': 'product_id',
}
BAND_KEYS = ('neg', 'low', 'mid', 'high')
SUM_KEYS = ('subtotal', 'tax', 'total', 'gross', 'discount_amount',
            'cost_total', 'margin', 'delivered_margin')


def margin_band(pct):
    """Health band of a margin percentage (thresholds: 0 / 10 / 30 %)."""
    if pct < 0:
        return 'neg'
    if pct < 10:
        return 'low'
    if pct < 30:
        return 'mid'
    return 'high'


class SaleMarginReportWizard(models.TransientModel):
    _name = 'sale.margin.report.wizard'
    _description = 'Sales Margin Report Wizard'

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date_from = fields.Date(
        'From', default=lambda self: fields.Date.context_today(self).replace(month=1, day=1))
    date_to = fields.Date('To', default=fields.Date.context_today)
    state_scope = fields.Selection(
        [('confirmed', 'Confirmed Orders'), ('all', 'Quotations & Orders')],
        string='Scope', default='confirmed', required=True)

    order_ids = fields.Many2many('sale.order', string='Sales Orders')
    partner_ids = fields.Many2many('res.partner', string='Customers')
    team_ids = fields.Many2many('crm.team', string='Sales Teams')
    user_ids = fields.Many2many('res.users', string='Salespersons')
    categ_ids = fields.Many2many('product.category', string='Product Categories')
    product_ids = fields.Many2many('product.product', string='Products')

    group_by = fields.Selection(
        [('order', 'Sales Order'), ('partner', 'Customer'), ('user', 'Salesperson'),
         ('team', 'Sales Team'), ('category', 'Product Category'), ('product', 'Product')],
        string='Group By', default='order', required=True)
    include_lines = fields.Boolean(
        'Include Line Details', default=True,
        help="PDF only: print the detailed order lines under each group.")

    margin_filter_level = fields.Selection(
        [('line', 'Order Line'), ('order', 'Sales Order')],
        string='Apply Threshold On', default='line', required=True)
    use_margin_min = fields.Boolean('Minimum Margin')
    margin_min = fields.Float('Min. Margin (%)', digits=(16, 2))
    use_margin_max = fields.Boolean('Maximum Margin')
    margin_max = fields.Float('Max. Margin (%)', digits=(16, 2))

    file_data = fields.Binary('Excel File', attachment=False, readonly=True)
    file_name = fields.Char()

    # ------------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        if ctx.get('active_model') == 'sale.order' and ctx.get('active_ids'):
            orders = self.env['sale.order'].browse(ctx['active_ids'])
            res.update({
                'order_ids': [(6, 0, orders.ids)],
                'date_from': False,
                'date_to': False,
                'state_scope': 'all',
                'company_id': orders[:1].company_id.id or res.get('company_id'),
            })
        return res

    @api.constrains('date_from', 'date_to', 'use_margin_min', 'use_margin_max',
                    'margin_min', 'margin_max')
    def _check_ranges(self):
        for wiz in self:
            if wiz.date_from and wiz.date_to and wiz.date_from > wiz.date_to:
                raise ValidationError(_("The start date must be before the end date."))
            if wiz.use_margin_min and wiz.use_margin_max and wiz.margin_min > wiz.margin_max:
                raise ValidationError(_("The minimum margin cannot exceed the maximum margin."))

    # ------------------------------------------------------------------------
    # Domain
    # ------------------------------------------------------------------------
    def _to_utc(self, day, moment):
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        local = tz.localize(datetime.combine(day, moment))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    def _margin_threshold_domain(self, field_name):
        domain = []
        if self.use_margin_min:
            domain.append((field_name, '>=', self.margin_min))
        if self.use_margin_max:
            domain.append((field_name, '<=', self.margin_max))
        return domain

    def _within_threshold(self, pct):
        pct = round(pct, 2)
        if self.use_margin_min and pct < self.margin_min:
            return False
        if self.use_margin_max and pct > self.margin_max:
            return False
        return True

    def _get_domain(self):
        self.ensure_one()
        if self.order_ids.company_id - self.company_id:
            raise UserError(_("All selected sales orders must belong to %s.", self.company_id.name))
        domain = [('company_id', '=', self.company_id.id)]
        if self.state_scope == 'confirmed':
            domain.append(('state', 'in', ('sale', 'done')))
        if self.date_from:
            domain.append(('date', '>=', self._to_utc(self.date_from, time.min)))
        if self.date_to:
            domain.append(('date', '<=', self._to_utc(self.date_to, time.max)))
        criteria = (
            ('order_id', 'in', self.order_ids),
            ('commercial_partner_id', 'in', self.partner_ids.commercial_partner_id),
            ('team_id', 'in', self.team_ids),
            ('user_id', 'in', self.user_ids),
            ('categ_id', 'child_of', self.categ_ids),
            ('product_id', 'in', self.product_ids),
        )
        for field_name, operator, records in criteria:
            if records:
                domain.append((field_name, operator, records.ids))
        if self.margin_filter_level == 'line':
            domain += self._margin_threshold_domain('margin_percent')
        return domain

    def _get_effective_domain(self):
        """Domain including order-level thresholds (weighted order margin %)."""
        domain = self._get_domain()
        if self.margin_filter_level == 'order' and (self.use_margin_min or self.use_margin_max):
            groups = self.env['sale.margin.report'].read_group(
                domain, ['price_subtotal', 'margin', 'margin_percent'], ['order_id'], lazy=False)
            order_ids = [g['order_id'][0] for g in groups
                         if g['order_id'] and self._within_threshold(g['margin_percent'])]
            domain = expression.AND([domain, [('order_id', 'in', order_ids)]])
        return domain

    def _get_report_records(self):
        self.ensure_one()
        return self.env['sale.margin.report'].search(self._get_effective_domain())

    # ------------------------------------------------------------------------
    # Payload shared by the PDF and the Excel export
    # ------------------------------------------------------------------------
    def _prepare_line(self, rec, state_labels, source_labels):
        local_dt = (fields.Datetime.context_timestamp(self, rec.date).replace(tzinfo=None)
                    if rec.date else False)
        return {
            'order': rec.name,
            'order_id': rec.order_id.id,
            'date': local_dt,
            'date_str': format_date(self.env, local_dt) if local_dt else '',
            'state': state_labels.get(rec.state, ''),
            'customer': rec.commercial_partner_id.display_name or '',
            'salesperson': rec.user_id.name or '',
            'team': rec.team_id.name or '',
            'product': rec.product_id.display_name or '',
            'category': rec.categ_id.display_name or '',
            'qty': rec.qty_ordered,
            'uom': rec.line_uom_id.name or '',
            'price_unit': rec.price_unit,
            'discount': rec.discount,
            'price_unit_net': rec.price_unit_net,
            'cost_unit': rec.cost_unit,
            'subtotal': rec.price_subtotal,
            'tax': rec.price_tax,
            'total': rec.price_total,
            'gross': rec.gross_amount,
            'discount_amount': rec.discount_amount,
            'cost_total': rec.cost_total,
            'margin': rec.margin,
            'margin_pct': rec.margin_percent,
            'band': margin_band(rec.margin_percent),
            'qty_delivered': rec.qty_delivered,
            'delivered_margin': rec.delivered_margin,
            'cost_source': source_labels.get(rec.cost_source, ''),
            'cost_source_key': rec.cost_source or '',
            'order_currency': rec.order_currency_id.name or '',
        }

    @staticmethod
    def _sum_lines(lines):
        totals = {key: sum(line[key] for line in lines) for key in SUM_KEYS}
        totals['margin_pct'] = (totals['margin'] / totals['subtotal'] * 100.0
                                if totals['subtotal'] else 0.0)
        totals['band'] = margin_band(totals['margin_pct'])
        totals['orders'] = len({line['order_id'] for line in lines})
        totals['count'] = len(lines)
        return totals

    def _band_summary(self, lines):
        labels = {
            'neg': _('Loss-making (< 0%)'),
            'low': _('Low (0–10%)'),
            'mid': _('Standard (10–30%)'),
            'high': _('High (≥ 30%)'),
        }
        base = sum(abs(line['subtotal']) for line in lines)
        result = []
        for key in BAND_KEYS:
            band_lines = [line for line in lines if line['band'] == key]
            weight = sum(abs(line['subtotal']) for line in band_lines)
            result.append({
                'key': key,
                'label': labels[key],
                'count': len(band_lines),
                'revenue': sum(line['subtotal'] for line in band_lines),
                'margin': sum(line['margin'] for line in band_lines),
                'share': weight / base * 100.0 if base else 0.0,
            })
        return result

    def _group_name(self, record):
        if not record:
            return _('Undefined')
        return record.name if record._name == 'sale.order' else record.display_name

    def _group_meta(self, rec, line):
        if self.group_by != 'order':
            return ''
        parts = [rec.commercial_partner_id.display_name, rec.user_id.name, line['date_str']]
        return '  ·  '.join(part for part in parts if part)

    @staticmethod
    def _names(records, limit=4):
        names = records.mapped('display_name')
        text = ', '.join(names[:limit])
        return text + (_(' +%s more', len(names) - limit) if len(names) > limit else '')

    def _period_label(self):
        if self.date_from and self.date_to:
            return '%s – %s' % (format_date(self.env, self.date_from), format_date(self.env, self.date_to))
        if self.date_from:
            return _('From %s', format_date(self.env, self.date_from))
        if self.date_to:
            return _('Until %s', format_date(self.env, self.date_to))
        return _('All dates')

    def _selection_label(self, field_name, record=None):
        record = record or self
        selection = dict(record._fields[field_name]._description_selection(self.env))
        return selection.get(record[field_name], '')

    def _filter_summary(self):
        items = [
            (_('Period'), self._period_label()),
            (_('Scope'), self._selection_label('state_scope')),
            (_('Grouped by'), self._selection_label('group_by')),
            (_('Cost basis'), self._selection_label('sma_cost_method', self.company_id)),
        ]
        for label, records in ((_('Orders'), self.order_ids),
                               (_('Customers'), self.partner_ids),
                               (_('Teams'), self.team_ids),
                               (_('Salespersons'), self.user_ids),
                               (_('Categories'), self.categ_ids),
                               (_('Products'), self.product_ids)):
            if records:
                items.append((label, self._names(records)))
        if self.use_margin_min or self.use_margin_max:
            bounds = []
            if self.use_margin_min:
                bounds.append('≥ %.2f%%' % self.margin_min)
            if self.use_margin_max:
                bounds.append('≤ %.2f%%' % self.margin_max)
            items.append((_('Margin'), '%s (%s)' % (
                _(' and ').join(bounds), self._selection_label('margin_filter_level'))))
        return items

    def _prepare_report_payload(self):
        self.ensure_one()
        records = self._get_report_records()
        if not records:
            raise UserError(_("No sales order lines match the selected criteria."))
        Report = self.env['sale.margin.report']
        state_labels = dict(Report._fields['state']._description_selection(self.env))
        source_labels = dict(Report._fields['cost_source']._description_selection(self.env))

        lines = []
        buckets = {}
        group_field = GROUP_BY_FIELDS[self.group_by]
        for rec in records:
            line = self._prepare_line(rec, state_labels, source_labels)
            lines.append(line)
            key_record = rec[group_field]
            key = key_record.id or 0
            if key not in buckets:
                buckets[key] = {
                    'key': key,
                    'name': self._group_name(key_record),
                    'meta': self._group_meta(rec, line),
                    'lines': [],
                }
            buckets[key]['lines'].append(line)

        groups = list(buckets.values())
        totals = self._sum_lines(lines)
        for group in groups:
            group['totals'] = self._sum_lines(group['lines'])
        max_abs = max((abs(g['totals']['margin']) for g in groups), default=0.0) or 1.0
        for group in groups:
            group['bar'] = abs(group['totals']['margin']) / max_abs * 100.0
            group['share'] = (group['totals']['margin'] / totals['margin'] * 100.0
                              if totals['margin'] else 0.0)
        if self.group_by != 'order':
            groups.sort(key=lambda g: g['name'].lower())
        ranking = sorted(groups, key=lambda g: g['totals']['margin'], reverse=True)

        group_label = self._selection_label('group_by')
        return {
            'company': self.company_id,
            'currency': self.company_id.currency_id,
            'group_by': self.group_by,
            'group_label': group_label,
            'summary_title': _('Profitability by %s', group_label),
            'period_label': self._period_label(),
            'filters': self._filter_summary(),
            'cost_basis': self._selection_label('sma_cost_method', self.company_id),
            'generated_on': format_datetime(self.env, fields.Datetime.now()),
            'generated_by': self.env.user.name,
            'price_digits': self.env['decimal.precision'].precision_get('Product Price'),
            'lines': lines,
            'groups': groups,
            'ranking': ranking,
            'totals': totals,
            'bands': self._band_summary(lines),
        }

    # ------------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------------
    def action_print_pdf(self):
        self.ensure_one()
        if not self._get_report_records():
            raise UserError(_("No sales order lines match the selected criteria."))
        return self.env.ref('sale_margin_analytics.action_report_margin_pdf').report_action(self)

    def action_export_xlsx(self):
        self.ensure_one()
        payload = self._prepare_report_payload()
        content = MarginXlsxBuilder(self.env, payload).build()
        stamp = fields.Date.context_today(self).strftime('%Y%m%d')
        self.write({
            'file_data': base64.b64encode(content),
            'file_name': 'sales_margin_report_%s.xlsx' % stamp,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s/%s/file_data/%s?download=true' % (
                self._name, self.id, self.file_name),
            'target': 'self',
        }

    def action_open_analysis(self):
        self.ensure_one()
        group_field = GROUP_BY_FIELDS[self.group_by]
        return {
            'type': 'ir.actions.act_window',
            'name': _('Margin Analysis'),
            'res_model': 'sale.margin.report',
            'view_mode': 'pivot,graph,tree',
            'views': [(False, 'pivot'), (False, 'graph'), (False, 'tree')],
            'search_view_id': [self.env.ref('sale_margin_analytics.sale_margin_report_view_search').id],
            'domain': self._get_effective_domain(),
            'context': {
                'pivot_row_groupby': [group_field],
                'pivot_measures': ['price_subtotal', 'cost_total', 'margin', 'margin_percent'],
                'graph_groupbys': [group_field],
            },
            'target': 'current',
        }
