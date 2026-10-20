from odoo import api, fields, models
from odoo.tools import float_is_zero


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    sma_amount_cost = fields.Monetary(
        string='Total Cost', compute='_compute_sma_totals', store=True)
    sma_margin = fields.Monetary(
        string='Margin', compute='_compute_sma_totals', store=True)
    sma_margin_percent = fields.Float(
        string='Margin (%)', digits=(16, 2), aggregator='avg',
        compute='_compute_sma_totals', store=True,
        help="Weighted order margin: total margin / total untaxed revenue x 100.")

    @api.depends('order_line.sma_cost_total', 'order_line.sma_margin',
                 'order_line.price_subtotal')
    def _compute_sma_totals(self):
        for order in self:
            lines = order.order_line.filtered(
                lambda l: not l.display_type and not l.is_downpayment)
            revenue = sum(lines.mapped('price_subtotal'))
            margin = sum(lines.mapped('sma_margin'))
            order.sma_amount_cost = sum(lines.mapped('sma_cost_total'))
            order.sma_margin = margin
            order.sma_margin_percent = (
                margin / revenue * 100.0 if not order.currency_id.is_zero(revenue) else 0.0)

    def action_sma_refresh_costs(self):
        """Re-capture the standard-cost snapshot of lines not delivered yet."""
        lines = self.order_line.filtered(
            lambda l: not l.display_type and l.product_id
            and float_is_zero(l.qty_delivered, precision_rounding=l.product_uom_id.rounding or 0.01))
        lines._compute_sma_std_cost()
        return True
