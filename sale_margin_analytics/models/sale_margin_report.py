from odoo import api, fields, models

from .sale_order_line import COST_SOURCE_SELECTION

STATE_SELECTION = [
    ('draft', 'Quotation'),
    ('sent', 'Quotation Sent'),
    ('sale', 'Sales Order'),
    ('cancel', 'Cancelled'),
]

# Ratio measures are meaningless when summed or averaged across lines.
# read_group re-derives them from their summed components:
#   measure -> (numerator, denominator, factor)
WEIGHTED_RATIOS = {
    'margin_percent': ('margin', 'price_subtotal', 100.0),
    'delivered_margin_percent': ('delivered_margin', 'delivered_amount', 100.0),
    'discount': ('discount_amount', 'gross_amount', 100.0),
    'price_unit': ('gross_amount', 'qty_ordered', 1.0),
    'price_unit_net': ('price_subtotal', 'qty_ordered', 1.0),
    'cost_unit': ('cost_total', 'qty_ordered', 1.0),
}


class SaleMarginReport(models.Model):
    """Read-only analytical model over sale.order.line.

    Implemented with ``_table_query`` (a SQL sub-query evaluated at read time):
    no view to maintain at install/upgrade, always consistent with the stored
    line figures, and fully compatible with pivot / graph / list / record rules.
    All amounts are expressed in the company currency, converted with the
    order's own ``currency_rate`` (same convention as ``sale.report``).
    """
    _name = 'sale.margin.report'
    _description = 'Sales Margin Analysis'
    _auto = False
    _rec_name = 'name'
    _order = 'date desc, order_id desc, line_id'

    # --- Dimensions -----------------------------------------------------------
    name = fields.Char('Order Reference', readonly=True)
    line_id = fields.Many2one('sale.order.line', 'Order Line', readonly=True)
    order_id = fields.Many2one('sale.order', 'Sales Order', readonly=True)
    date = fields.Datetime('Order Date', readonly=True)
    state = fields.Selection(STATE_SELECTION, 'Status', readonly=True)
    partner_id = fields.Many2one('res.partner', 'Customer (Contact)', readonly=True)
    commercial_partner_id = fields.Many2one('res.partner', 'Customer', readonly=True)
    user_id = fields.Many2one('res.users', 'Salesperson', readonly=True)
    team_id = fields.Many2one('crm.team', 'Sales Team', readonly=True)
    company_id = fields.Many2one('res.company', 'Company', readonly=True)
    currency_id = fields.Many2one('res.currency', 'Currency', readonly=True)
    order_currency_id = fields.Many2one('res.currency', 'Order Currency', readonly=True)
    product_id = fields.Many2one('product.product', 'Product', readonly=True)
    product_tmpl_id = fields.Many2one('product.template', 'Product Template', readonly=True)
    categ_id = fields.Many2one('product.category', 'Product Category', readonly=True)
    line_uom_id = fields.Many2one('uom.uom', 'Line UoM', readonly=True)
    product_uom_id = fields.Many2one('uom.uom', 'Product UoM', readonly=True)
    cost_source = fields.Selection(COST_SOURCE_SELECTION, 'Cost Basis', readonly=True)

    # --- Quantities -----------------------------------------------------------
    qty_ordered = fields.Float('Qty Ordered (Line UoM)', readonly=True)
    qty_delivered = fields.Float('Qty Delivered (Line UoM)', readonly=True)
    qty_invoiced = fields.Float('Qty Invoiced (Line UoM)', readonly=True)
    product_qty = fields.Float('Qty Ordered (Product UoM)', readonly=True)

    # --- Unit figures (weighted in read_group) -------------------------------
    discount = fields.Float('Discount (%)', digits=(16, 2), aggregator='avg', readonly=True)
    price_unit = fields.Float('Unit Price (excl. tax)', digits='Product Price',
                              aggregator='avg', readonly=True)
    price_unit_net = fields.Float('Net Unit Price', digits='Product Price',
                                  aggregator='avg', readonly=True,
                                  help="Unit price after discount, excluding taxes.")
    cost_unit = fields.Float('Unit Cost', digits='Product Price',
                             aggregator='avg', readonly=True)

    # --- Amounts (company currency) ------------------------------------------
    gross_amount = fields.Monetary('Gross Amount', readonly=True,
                                   help="Untaxed amount before discount.")
    discount_amount = fields.Monetary('Discount Amount', readonly=True)
    price_subtotal = fields.Monetary('Revenue (Untaxed)', readonly=True)
    price_tax = fields.Monetary('Taxes', readonly=True)
    price_total = fields.Monetary('Total (Tax Incl.)', readonly=True)
    cost_total = fields.Monetary('Total Cost', readonly=True)
    margin = fields.Monetary('Margin', readonly=True)
    margin_percent = fields.Float('Margin (%)', digits=(16, 2), aggregator='avg',
                                  readonly=True)
    delivered_amount = fields.Monetary('Delivered Revenue', readonly=True)
    delivered_cost = fields.Monetary('Delivered Cost', readonly=True)
    delivered_margin = fields.Monetary('Delivered Margin', readonly=True)
    delivered_margin_percent = fields.Float('Delivered Margin (%)', digits=(16, 2),
                                            aggregator='avg', readonly=True)

    # ------------------------------------------------------------------------
    @property
    def _table_query(self):
        return self._query()

    @api.model
    def _query(self):
        return """
            SELECT
                l.id                                        AS id,
                l.id                                        AS line_id,
                s.id                                        AS order_id,
                s.name                                      AS name,
                s.date_order                                AS date,
                s.state                                     AS state,
                s.partner_id                                AS partner_id,
                partner.commercial_partner_id               AS commercial_partner_id,
                s.user_id                                   AS user_id,
                s.team_id                                   AS team_id,
                s.company_id                                AS company_id,
                company.currency_id                         AS currency_id,
                s.currency_id                               AS order_currency_id,
                l.product_id                                AS product_id,
                pt.id                                       AS product_tmpl_id,
                pt.categ_id                                 AS categ_id,
                l.product_uom                               AS line_uom_id,
                pt.uom_id                                   AS product_uom_id,
                l.sma_cost_source                           AS cost_source,

                l.product_uom_qty                           AS qty_ordered,
                l.qty_delivered                             AS qty_delivered,
                l.qty_invoiced                              AS qty_invoiced,
                CASE WHEN COALESCE(line_uom.factor, 0) = 0 THEN l.product_uom_qty
                     ELSE l.product_uom_qty / line_uom.factor * COALESCE(base_uom.factor, 1.0)
                END                                         AS product_qty,

                COALESCE(l.discount, 0)                     AS discount,
                (CASE WHEN l.product_uom_qty <> 0 THEN g.gross / l.product_uom_qty
                      ELSE l.price_unit END) / r.rate       AS price_unit,
                l.price_reduce_taxexcl / r.rate             AS price_unit_net,
                l.sma_cost_unit_company                     AS cost_unit,

                g.gross / r.rate                            AS gross_amount,
                (g.gross - l.price_subtotal) / r.rate       AS discount_amount,
                l.price_subtotal / r.rate                   AS price_subtotal,
                l.price_tax / r.rate                        AS price_tax,
                l.price_total / r.rate                      AS price_total,
                l.sma_cost_total_company                    AS cost_total,
                l.price_subtotal / r.rate
                    - l.sma_cost_total_company              AS margin,
                l.sma_margin_percent                        AS margin_percent,

                d.amount                                    AS delivered_amount,
                l.sma_delivered_cost_company                AS delivered_cost,
                d.amount - l.sma_delivered_cost_company     AS delivered_margin,
                CASE WHEN d.amount = 0 THEN 0
                     ELSE (d.amount - l.sma_delivered_cost_company) / d.amount * 100.0
                END                                         AS delivered_margin_percent
            FROM sale_order_line l
            JOIN sale_order s           ON s.id = l.order_id
            JOIN res_company company    ON company.id = s.company_id
            JOIN res_partner partner    ON partner.id = s.partner_id
            JOIN product_product pp     ON pp.id = l.product_id
            JOIN product_template pt    ON pt.id = pp.product_tmpl_id
            LEFT JOIN uom_uom line_uom  ON line_uom.id = l.product_uom
            LEFT JOIN uom_uom base_uom  ON base_uom.id = pt.uom_id
            CROSS JOIN LATERAL (
                SELECT CASE WHEN COALESCE(s.currency_rate, 0) = 0 THEN 1.0
                            ELSE s.currency_rate END AS rate
            ) r
            CROSS JOIN LATERAL (
                SELECT CASE WHEN COALESCE(l.discount, 0) < 100.0
                            THEN l.price_subtotal / (1.0 - COALESCE(l.discount, 0) / 100.0)
                            ELSE l.price_unit * l.product_uom_qty END AS gross
            ) g
            CROSS JOIN LATERAL (
                SELECT COALESCE(l.price_reduce_taxexcl, 0) * COALESCE(l.qty_delivered, 0)
                       / r.rate AS amount
            ) d
            WHERE l.display_type IS NULL
              AND COALESCE(l.is_downpayment, FALSE) = FALSE
              AND s.state != 'cancel'
        """

    # ------------------------------------------------------------------------
    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None,
                   orderby=False, lazy=True):
        """Return *weighted* ratios (margin %, unit price, unit cost, discount %)
        for each pivot / graph cell instead of a plain average of line ratios."""
        requested = {spec.split(':')[0].strip() for spec in fields}
        ratios = {name: spec for name, spec in WEIGHTED_RATIOS.items() if name in requested}
        extra = {f for num, den, _factor in ratios.values() for f in (num, den)} - requested
        result = super().read_group(
            domain, list(fields) + sorted(extra), groupby,
            offset=offset, limit=limit, orderby=orderby, lazy=lazy)
        for row in result:
            for name, (num, den, factor) in ratios.items():
                denominator = row.get(den) or 0.0
                row[name] = (row.get(num) or 0.0) / denominator * factor if denominator else 0.0
        return result
