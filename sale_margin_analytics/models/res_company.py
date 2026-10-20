from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    sma_cost_method = fields.Selection(
        selection=[
            ('standard', 'Standard Cost (snapshot at order time)'),
            ('valuation', 'Inventory Valuation (actual cost of delivered goods)'),
        ],
        string='Margin Cost Basis',
        default='standard',
        required=True,
        help="Standard: the product cost captured on the order line when the product "
             "was selected.\n"
             "Inventory Valuation: delivered quantities use the real cost booked in the "
             "stock valuation layers (FIFO / AVCO); undelivered quantities fall back to "
             "the standard-cost snapshot.",
    )
