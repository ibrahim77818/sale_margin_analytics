from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sma_cost_method = fields.Selection(
        related='company_id.sma_cost_method', readonly=False)
