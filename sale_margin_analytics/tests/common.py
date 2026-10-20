from odoo.tests import Form, TransactionCase


class SaleMarginCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.company.sma_cost_method = 'standard'
        cls.company_currency = cls.company.currency_id
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1)
        cls.partner = cls.env['res.partner'].create({'name': 'SMA Customer'})
        cls.categ = cls.env['product.category'].create({
            'name': 'SMA Average Cost',
            'property_cost_method': 'average',
            'property_valuation': 'manual_periodic',
        })
        cls.product = cls.env['product.product'].create({
            'name': 'SMA Widget',
            'detailed_type': 'product',
            'categ_id': cls.categ.id,
            'standard_price': 60.0,
            'list_price': 100.0,
            'invoice_policy': 'order',
            'taxes_id': [(6, 0, [])],
        })
        cls.service = cls.env['product.product'].create({
            'name': 'SMA Installation',
            'detailed_type': 'service',
            'categ_id': cls.categ.id,
            'standard_price': 270.0,
            'list_price': 300.0,
            'taxes_id': [(6, 0, [])],
        })
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'SMA Pricelist', 'currency_id': cls.company_currency.id})
        cls.tax_group = cls.env['account.tax.group'].create({'name': 'SMA VAT'})

    # ------------------------------------------------------------------ utils
    @classmethod
    def _make_tax(cls, amount, price_include=False):
        return cls.env['account.tax'].create({
            'name': 'SMA %s%%%s' % (amount, ' incl.' if price_include else ''),
            'amount': amount,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
            'price_include': price_include,
            'tax_group_id': cls.tax_group.id,
            'company_id': cls.company.id,
        })

    @classmethod
    def _create_order(cls, lines, pricelist=None, user=None, confirm=True):
        vals = {
            'partner_id': cls.partner.id,
            'pricelist_id': (pricelist or cls.pricelist).id,
            'order_line': [],
        }
        if user:
            vals['user_id'] = user.id
        for line in lines:
            product = line.get('product', cls.product)
            line_vals = {
                'product_id': product.id,
                'product_uom_qty': line.get('qty', 1.0),
                'price_unit': line['price'],
                'discount': line.get('discount', 0.0),
                'tax_id': [(6, 0, line.get('taxes', cls.env['account.tax']).ids)],
            }
            if line.get('uom'):
                line_vals['product_uom'] = line['uom'].id
            vals['order_line'].append((0, 0, line_vals))
        order = cls.env['sale.order'].create(vals)
        if confirm:
            order.action_confirm()
        return order

    def _set_stock(self, product, qty):
        self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': product.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'inventory_quantity': qty,
        }).action_apply_inventory()

    def _deliver(self, order, qty):
        picking = order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))
        self.assertEqual(len(picking), 1, "Expected exactly one open delivery")
        picking.action_assign()
        picking.move_ids.quantity_done = qty
        res = picking.button_validate()
        if isinstance(res, dict) and res.get('res_model') == 'stock.backorder.confirmation':
            Form(self.env['stock.backorder.confirmation'].with_context(res['context'])).save().process()
        return picking

    def _report_row(self, line):
        self.env.flush_all()
        return self.env['sale.margin.report'].search([('line_id', '=', line.id)])
