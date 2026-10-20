from odoo.tests import tagged

from .common import SaleMarginCommon


@tagged('post_install', '-at_install', 'sale_margin_analytics')
class TestMarginReport(SaleMarginCommon):

    def test_01_report_row_matches_line(self):
        order = self._create_order([{'qty': 3, 'price': 100.0, 'discount': 15.0}])
        row = self._report_row(order.order_line)
        self.assertEqual(len(row), 1)
        self.assertAlmostEqual(row.qty_ordered, 3.0)
        self.assertAlmostEqual(row.gross_amount, 300.0, places=2)
        self.assertAlmostEqual(row.discount_amount, 45.0, places=2)
        self.assertAlmostEqual(row.price_subtotal, 255.0, places=2)
        self.assertAlmostEqual(row.price_unit, 100.0, places=4)
        self.assertAlmostEqual(row.price_unit_net, 85.0, places=4)
        self.assertAlmostEqual(row.cost_unit, 60.0, places=4)
        self.assertAlmostEqual(row.cost_total, 180.0, places=2)
        self.assertAlmostEqual(row.margin, 75.0, places=2)
        self.assertAlmostEqual(row.margin_percent, 29.41, places=2)

    def test_02_report_is_in_company_currency(self):
        currency = self.env['res.currency'].create({'name': 'ZZR', 'symbol': 'R$', 'rounding': 0.01})
        self.env['res.currency.rate'].create({
            'name': '2000-01-01', 'rate': 4.0,
            'currency_id': currency.id, 'company_id': self.company.id})
        pricelist = self.env['product.pricelist'].create({'name': 'SMA ZZR', 'currency_id': currency.id})
        order = self._create_order([{'qty': 1, 'price': 400.0}], pricelist=pricelist)
        rate = order.currency_rate
        row = self._report_row(order.order_line)
        self.assertEqual(row.currency_id, self.company_currency)
        self.assertEqual(row.order_currency_id, currency)
        self.assertAlmostEqual(row.price_subtotal, 400.0 / rate, places=2)
        self.assertAlmostEqual(row.cost_total, 60.0, places=2)
        self.assertAlmostEqual(row.margin, 400.0 / rate - 60.0, places=2)

    def test_03_uom_normalised_quantity(self):
        dozen = self.env.ref('uom.product_uom_dozen')
        order = self._create_order([{'qty': 2, 'price': 1000.0, 'uom': dozen}])
        row = self._report_row(order.order_line)
        self.assertAlmostEqual(row.qty_ordered, 2.0)
        self.assertAlmostEqual(row.product_qty, 24.0)

    def test_04_excluded_lines(self):
        order = self._create_order([{'qty': 1, 'price': 100.0}], confirm=False)
        order.write({'order_line': [(0, 0, {'display_type': 'line_section', 'name': 'Section'})]})
        cancelled = self._create_order([{'qty': 1, 'price': 100.0}], confirm=False)
        cancelled.action_cancel()
        self.env.flush_all()
        rows = self.env['sale.margin.report'].search([('order_id', 'in', (order | cancelled).ids)])
        self.assertEqual(rows.line_id, order.order_line.filtered(lambda l: not l.display_type))

    def test_05_read_group_weights_ratios(self):
        """Pivot cells must show revenue-weighted margin %, not an average of line %."""
        order = self._create_order([
            {'qty': 1, 'price': 100.0},                           # 40 %
            {'qty': 1, 'price': 300.0, 'product': self.service},  # 10 %
        ])
        self.env.flush_all()
        res = self.env['sale.margin.report'].read_group(
            [('order_id', '=', order.id)],
            ['margin_percent:avg', 'cost_unit:avg', 'discount:avg'], ['order_id'])
        self.assertEqual(len(res), 1)
        self.assertAlmostEqual(res[0]['margin_percent'], 17.5, places=2)   # 70 / 400
        self.assertAlmostEqual(res[0]['cost_unit'], 165.0, places=2)       # 330 / 2
        self.assertAlmostEqual(res[0]['discount'], 0.0, places=2)

    def test_06_salesperson_sees_own_lines_only(self):
        rep = self.env['res.users'].create({
            'name': 'SMA Rep', 'login': 'sma_rep',
            'company_id': self.company.id, 'company_ids': [(6, 0, self.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('sales_team.group_sale_salesman').id,
                self.env.ref('sale_margin_analytics.group_margin_analyst').id,
            ])],
        })
        own = self._create_order([{'qty': 1, 'price': 100.0}], user=rep)
        other = self._create_order([{'qty': 1, 'price': 100.0}], user=self.env.user)
        self.env.flush_all()
        rows = self.env['sale.margin.report'].with_user(rep).search(
            [('order_id', 'in', (own | other).ids)])
        self.assertEqual(rows.order_id, own)

    def test_07_manager_sees_everything(self):
        manager = self.env['res.users'].create({
            'name': 'SMA Manager', 'login': 'sma_manager',
            'company_id': self.company.id, 'company_ids': [(6, 0, self.company.ids)],
            'groups_id': [(6, 0, [self.env.ref('sales_team.group_sale_manager').id])],
        })
        first = self._create_order([{'qty': 1, 'price': 100.0}])
        second = self._create_order([{'qty': 1, 'price': 100.0}])
        self.env.flush_all()
        rows = self.env['sale.margin.report'].with_user(manager).search(
            [('order_id', 'in', (first | second).ids)])
        self.assertEqual(rows.order_id, first | second)
