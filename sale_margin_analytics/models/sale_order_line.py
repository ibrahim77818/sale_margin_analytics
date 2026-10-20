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

    # --- Quantity in the product's reference UoM (used by the SQL report) -----
    sma_product_qty = fields.Float(
        string='Qty (Product UoM)',
        compute='_compute_sma_product_qty', store=True,
        help="Ordered quantity converted to the product's unit of measure. Stored here "
             "because v19 reworked uom.uom (no SQL-usable factor anymore).")

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
        help="Cost of the quantities already delivered: actual stock-move value in "
             "'Inventory Valuation' mode, snapshot cost x delivered qty otherwise.")

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
        string='Margin (%)', digits=(16, 2), aggregator='avg',
        compute='_compute_sma_costs', store=True,
        help="Margin / untaxed subtotal (after discount) x 100.")
    sma_cost_source = fields.Selection(
        COST_SOURCE_SELECTION, string='Cost Basis',
        compute='_compute_sma_costs', store=True)

    # ------------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------------
    @api.depends('product_id', 'product_uom_id', 'product_uom_qty')
    def _compute_sma_product_qty(self):
        for line in self:
            product_uom = line.product_id.uom_id
            if not line.product_id or not line.product_uom_id or line.product_uom_id == product_uom:
                line.sma_product_qty = line.product_uom_qty
            else:
                line.sma_product_qty = line.product_uom_id._compute_quantity(
                    line.product_uom_qty, product_uom, round=False)

    @api.depends('product_id', 'product_uom_id', 'company_id')
    def _compute_sma_std_cost(self):
        for line in self:
            product = line.product_id
            if not product or line.display_type:
                line.sma_std_cost_unit_company = 0.0
                continue
            product = product.with_company(line.company_id or self.env.company)
            cost = product.standard_price
            if line.product_uom_id and line.product_uom_id != product.uom_id:
                cost = product.uom_id._compute_price(cost, line.product_uom_id)
            line.sma_std_cost_unit_company = cost

    def _sma_cost_depends(self):
        depends = [
            'product_id', 'product_uom_id', 'product_uom_qty', 'qty_delivered',
            'price_subtotal', 'display_type', 'is_downpayment', 'currency_id',
            'sma_std_cost_unit_company', 'company_id.sma_cost_method',
            'order_id.currency_rate',
            'move_ids.state', 'move_ids.quantity',
        ]
        # v19 removed stock.valuation.layer; the valued amount lives on the move.
        if 'value' in self.env['stock.move']._fields:
            depends.append('move_ids.value')
        return depends

    @api.depends(lambda self: self._sma_cost_depends())
    def _compute_sma_costs(self):
        # One batched read for the whole recordset: no N+1 on stock moves.
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
            line_uom = line.product_uom_id or line.product_id.uom_id
            std_unit = line.sma_std_cost_unit_company

            unit_cost = std_unit
            delivered_cost = std_unit * line.qty_delivered
            source = 'standard'

            value, moved_qty = valuation_totals.get(line._origin.id, (0.0, 0.0))
            if not float_is_zero(moved_qty, precision_digits=6):
                # Outgoing moves are signed negative (value and qty), returns positive:
                # value / qty is the positive unit cost in the product UoM.
                delivered_cost = -value
                actual_unit = line.product_id.uom_id._compute_price(value / moved_qty, line_uom)
                remaining = max(qty - line.qty_delivered, 0.0)
                if not qty or float_is_zero(remaining, precision_rounding=line_uom.rounding):
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
        """Return {sale_line_id: (signed value, signed qty in product UoM)} of the done
        customer deliveries (negative) and customer returns (positive) of the lines
        using the 'valuation' cost basis.

        v19: stock.valuation.layer no longer exists, the valued amount is
        ``stock.move.value``. If that field is not available the method returns {}
        and every line falls back to the standard-cost snapshot.
        """
        Move = self.env['stock.move'].sudo()
        if 'value' not in Move._fields:
            return {}
        lines = self.filtered(
            lambda l: l.company_id.sma_cost_method == 'valuation' and l.product_id.is_storable)
        line_ids = [line_id for line_id in lines._origin.ids if line_id]
        if not line_ids:
            return {}
        moves = Move.search([('sale_line_id', 'in', line_ids), ('state', '=', 'done')])
        totals = defaultdict(lambda: [0.0, 0.0])
        for move in moves:   # batch-prefetched: a constant number of queries
            src, dest = move.location_id.usage, move.location_dest_id.usage
            if dest == 'customer' and src != 'customer':
                sign = -1.0          # delivery (also dropship: supplier -> customer)
            elif src == 'customer' and dest != 'customer':
                sign = 1.0           # customer return
            else:
                continue             # internal steps of multi-step deliveries
            move_uom = move.product_uom if 'product_uom' in move._fields else move.product_uom_id
            qty = move_uom._compute_quantity(move.quantity, move.product_id.uom_id, round=False)
            totals[move.sale_line_id.id][0] += sign * abs(move.value)
            totals[move.sale_line_id.id][1] += sign * qty
        return {key: tuple(val) for key, val in totals.items()}
