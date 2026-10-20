from collections import defaultdict

from odoo import api, fields, models
from odoo.tools import float_is_zero

COST_SOURCE_SELECTION = [
    ('standard', 'Standard Cost'),
    ('mixed', 'Actual + Standard'),
    ('actual', 'Actual (Valuation)'),
    ('none', 'Not Applicable'),
]

# Fields reset to zero on sections, notes and down-payment lines.
_ZERO_FIELDS = (
    'sma_cost_unit_company', 'sma_cost_total_company', 'sma_delivered_cost_company',
    'sma_cost_unit', 'sma_cost_total', 'sma_margin', 'sma_margin_percent',
)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    sma_company_currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', string='Company Currency')

    # --- Cost snapshot -----------------------------------------------------
    sma_std_cost_unit_company = fields.Float(
        string='Standard Unit Cost (Snapshot)', digits='Product Price',
        compute='_compute_sma_std_cost', store=True, readonly=False,
        precompute=True, copy=False,
        help="Product cost captured when the product / unit of measure was set on the "
             "line, in company currency and per line UoM. It is deliberately frozen so "
             "that later cost updates never rewrite the margin of historical orders. "
             "Use 'Refresh Standard Costs' on the order to re-capture it.")

    # --- Company currency (used by the analytical report) --------------------
    sma_cost_unit_company = fields.Float(
        string='Unit Cost (Company Currency)', digits='Product Price',
        compute='_compute_sma_costs', store=True)
    sma_cost_total_company = fields.Monetary(
        string='Total Cost (Company Currency)', currency_field='sma_company_currency_id',
        compute='_compute_sma_costs', store=True)
    sma_delivered_cost_company = fields.Monetary(
        string='Delivered Cost (Company Currency)', currency_field='sma_company_currency_id',
        compute='_compute_sma_costs', store=True,
        help="Cost of the quantities already delivered: actual valuation-layer value "
             "in 'Inventory Valuation' mode, snapshot cost x delivered qty otherwise.")

    # --- Order currency (shown on the order) --------------------------------
    sma_cost_unit = fields.Float(
        string='Unit Cost', digits='Product Price',
        compute='_compute_sma_costs', store=True)
    sma_cost_total = fields.Monetary(
        string='Total Cost', currency_field='currency_id',
        compute='_compute_sma_costs', store=True)
    sma_margin = fields.Monetary(
        string='Margin', currency_field='currency_id',
        compute='_compute_sma_costs', store=True)
    sma_margin_percent = fields.Float(
        string='Margin (%)', digits=(16, 2), group_operator='avg',
        compute='_compute_sma_costs', store=True,
        help="Margin / untaxed subtotal (after discount) x 100.")
    sma_cost_source = fields.Selection(
        COST_SOURCE_SELECTION, string='Cost Basis',
        compute='_compute_sma_costs', store=True)

    # ------------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------------
    @api.depends('product_id', 'product_uom', 'company_id')
    def _compute_sma_std_cost(self):
        for line in self:
            product = line.product_id
            if not product or line.display_type:
                line.sma_std_cost_unit_company = 0.0
                continue
            product = product.with_company(line.company_id or self.env.company)
            cost = product.standard_price
            if line.product_uom and line.product_uom != product.uom_id:
                cost = product.uom_id._compute_price(cost, line.product_uom)
            line.sma_std_cost_unit_company = cost

    @api.depends(
        'product_id', 'product_uom', 'product_uom_qty', 'qty_delivered',
        'price_subtotal', 'display_type', 'is_downpayment', 'currency_id',
        'sma_std_cost_unit_company', 'company_id.sma_cost_method',
        'order_id.currency_rate',
        'move_ids.state',
        'move_ids.stock_valuation_layer_ids.value',
        'move_ids.stock_valuation_layer_ids.quantity',
    )
    def _compute_sma_costs(self):
        # One batched aggregation for the whole recordset: no N+1 on valuation layers.
        valuation_totals = self._sma_get_valuation_totals()
        for line in self:
            if line.display_type or line.is_downpayment or not line.product_id:
                line.update(dict.fromkeys(_ZERO_FIELDS, 0.0))
                line.sma_cost_source = 'none'
                continue

            company_currency = line.company_id.currency_id or self.env.company.currency_id
            order_currency = line.currency_id or company_currency
            rate = line.order_id.currency_rate or 1.0
            qty = line.product_uom_qty
            std_unit = line.sma_std_cost_unit_company

            unit_cost = std_unit
            delivered_cost = std_unit * line.qty_delivered
            source = 'standard'

            value, svl_qty = valuation_totals.get(line._origin.id, (0.0, 0.0))
            if not float_is_zero(svl_qty, precision_digits=6):
                # Outgoing layers are negative: value / qty is the positive unit cost
                # in the product UoM; convert it to the line UoM.
                delivered_cost = -value
                actual_unit = line.product_id.uom_id._compute_price(
                    value / svl_qty, line.product_uom or line.product_id.uom_id)
                remaining = max(qty - line.qty_delivered, 0.0)
                rounding = (line.product_uom or line.product_id.uom_id).rounding
                if not qty or float_is_zero(remaining, precision_rounding=rounding):
                    unit_cost, source = actual_unit, 'actual'
                else:
                    # Delivered part at actual cost, open part at the snapshot cost.
                    unit_cost = (delivered_cost + std_unit * remaining) / qty
                    source = 'mixed'

            cost_total = order_currency.round(unit_cost * qty * rate)
            margin = line.price_subtotal - cost_total
            line.update({
                'sma_cost_unit_company': unit_cost,
                'sma_cost_total_company': company_currency.round(unit_cost * qty),
                'sma_delivered_cost_company': company_currency.round(delivered_cost),
                'sma_cost_unit': unit_cost * rate,
                'sma_cost_total': cost_total,
                'sma_margin': margin,
                'sma_margin_percent': (
                    margin / line.price_subtotal * 100.0
                    if not order_currency.is_zero(line.price_subtotal) else 0.0),
                'sma_cost_source': source,
            })

    def _sma_get_valuation_totals(self):
        """Return {sale_line_id: (sum(value), sum(quantity))} of the stock valuation
        layers of done moves, for the lines using the 'valuation' cost basis.
        Three queries per batch regardless of its size."""
        lines = self.filtered(
            lambda l: l.company_id.sma_cost_method == 'valuation'
            and l.product_id.type == 'product')
        line_ids = [line_id for line_id in lines._origin.ids if line_id]
        if not line_ids:
            return {}
        moves = self.env['stock.move'].sudo().search([
            ('sale_line_id', 'in', line_ids), ('state', '=', 'done')])
        if not moves:
            return {}
        move_to_line = {move.id: move.sale_line_id.id for move in moves}
        groups = self.env['stock.valuation.layer'].sudo().read_group(
            [('stock_move_id', 'in', moves.ids)],
            ['value:sum', 'quantity:sum'], ['stock_move_id'], lazy=False)
        totals = defaultdict(lambda: [0.0, 0.0])
        for group in groups:
            line_id = move_to_line.get(group['stock_move_id'][0])
            totals[line_id][0] += group['value'] or 0.0
            totals[line_id][1] += group['quantity'] or 0.0
        return {key: tuple(val) for key, val in totals.items()}
