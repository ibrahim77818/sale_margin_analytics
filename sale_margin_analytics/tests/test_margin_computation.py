from odoo.tests import tagged
from odoo.tools import float_round

from .common import SaleMarginCommon


@tagged('post_install', '-at_install', 'sale_margin_analytics')
class TestMarginComputation(SaleMarginCommon):

    def test_01_full_order_standard_cost(self):
        order = self._create_order([{'qty': 10, 'price': 100.0}])
        line = order.order_line
        self.assertEqual(line.sma_cost_source, 'standard')
        self.assertAlmostEqual(line.sma_cost_unit, 60.0, places=4)
        self.assertAlmostEqual(line.price_subtotal, 1000.0, places=2)
        self.assertAlmostEqual(line.sma_cost_total, 600.0, places=2)
        self.assertAlmostEqual(line.sma_margin, 400.0, places=2)
        self.assertAlmostEqual(line.sma_margin_percent, 40.0, places=2)

    def test_02_discounted_line(self):
        order = self._create_order([{'qty': 3, 'price': 100.0, 'discount': 15.0}])
        line = order.order_line
        self.assertAlmostEqual(line.price_subtotal, 255.0, places=2)
        self.assertAlmostEqual(line.sma_cost_total, 180.0, places=2)
        self.assertAlmostEqual(line.sma_margin, 75.0, places=2)
        self.assertAlmostEqual(line.sma_margin_percent, 75.0 / 255.0 * 100, places=2)

    def test_03_rounding_precision(self):
        """Odd prices, quantities and discounts must not drift by a cent."""
        self.product.standard_price = 19.99
        order = self._create_order([{'qty': 3, 'price': 33.33, 'discount': 7.5}])
        line = order.order_line
        expected_subtotal = float_round(33.33 * (1 - 0.075) * 3, precision_digits=2)
        self.assertAlmostEqual(line.price_subtotal, expected_subtotal, places=2)  # 92.49
        self.assertAlmostEqual(line.sma_cost_total, 59.97, places=2)
        self.assertAlmostEqual(line.sma_margin, expected_subtotal - 59.97, places=2)
        self.assertAlmostEqual(
            line.sma_margin_percent, (expected_subtotal - 59.97) / expected_subtotal * 100, places=2)

    def test_04_tax_excluded_does_not_affect_margin(self):
        tax = self._make_tax(15.0)
        order = self._create_order([{'qty': 1, 'price': 100.0, 'taxes': tax}])
        line = order.order_line
        self.assertAlmostEqual(line.price_total, 115.0, places=2)
        self.assertAlmostEqual(line.sma_margin, 40.0, places=2)
        self.assertAlmostEqual(line.sma_margin_percent, 40.0, places=2)

    def test_05_tax_included_price_is_netted(self):
        tax = self._make_tax(15.0, price_include=True)
        order = self._create_order([{'qty': 1, 'price': 115.0, 'taxes': tax}])
        line = order.order_line
        self.assertAlmostEqual(line.price_subtotal, 100.0, places=2)
        self.assertAlmostEqual(line.sma_margin, 40.0, places=2)
        self.assertAlmostEqual(line.sma_margin_percent, 40.0, places=2)

    def test_06_unit_of_measure_conversion(self):
        dozen = self.env.ref('uom.product_uom_dozen')
        order = self._create_order([{'qty': 2, 'price': 1000.0, 'uom': dozen}])
        line = order.order_line
        self.assertAlmostEqual(line.sma_cost_unit, 720.0, places=4)  # 60 x 12
        self.assertAlmostEqual(line.sma_cost_total, 1440.0, places=2)
        self.assertAlmostEqual(line.sma_margin, 560.0, places=2)
        self.assertAlmostEqual(line.sma_margin_percent, 28.0, places=2)

    def test_07_zero_price_line(self):
        order = self._create_order([{'qty': 1, 'price': 0.0}])
        line = order.order_line
        self.assertAlmostEqual(line.sma_margin, -60.0, places=2)
        self.assertEqual(line.sma_margin_percent, 0.0)

    def test_08_multi_currency(self):
        currency = self.env['res.currency'].create({
            'name': 'ZZM', 'symbol': 'Z$', 'rounding': 0.01})
        self.env['res.currency.rate'].create({
            'name': '2000-01-01', 'rate': 2.0,
            'currency_id': currency.id, 'company_id': self.company.id})
        pricelist = self.env['product.pricelist'].create({
            'name': 'SMA ZZM', 'currency_id': currency.id})
        order = self._create_order([{'qty': 1, 'price': 200.0}], pricelist=pricelist)
        line = order.order_line
        rate = order.currency_rate
        self.assertTrue(rate and rate != 1.0)
        self.assertAlmostEqual(line.sma_cost_unit_company, 60.0, places=4)
        self.assertAlmostEqual(line.sma_cost_unit, 60.0 * rate, places=4)
        self.assertAlmostEqual(line.sma_cost_total, currency.round(60.0 * rate), places=2)
        self.assertAlmostEqual(line.sma_margin, 200.0 - currency.round(60.0 * rate), places=2)
        # Margin % is currency independent
        self.assertAlmostEqual(
            line.sma_margin_percent, (200.0 / rate - 60.0) / (200.0 / rate) * 100, places=1)

    def test_09_standard_cost_snapshot_is_frozen(self):
        order = self._create_order([{'qty': 1, 'price': 100.0}], confirm=False)
        self.product.standard_price = 80.0
        line = order.order_line
        self.assertAlmostEqual(line.sma_cost_unit, 60.0, places=4)
        order.action_sma_refresh_costs()
        self.assertAlmostEqual(line.sma_cost_unit, 80.0, places=4)
        self.assertAlmostEqual(line.sma_margin, 20.0, places=2)

    def test_10_partial_delivery_standard_basis(self):
        self._set_stock(self.product, 10)
        order = self._create_order([{'qty': 10, 'price': 100.0}])
        self._deliver(order, 4)
        line = order.order_line
        self.assertAlmostEqual(line.qty_delivered, 4.0)
        self.assertEqual(line.sma_cost_source, 'standard')
        self.assertAlmostEqual(line.sma_delivered_cost_company, 240.0, places=2)
        self.assertAlmostEqual(line.sma_margin, 400.0, places=2)

    def test_11_partial_then_full_delivery_valuation_basis(self):
        self.company.sma_cost_method = 'valuation'
        self._set_stock(self.product, 10)                 # 10 x 60 in stock
        order = self._create_order([{'qty': 10, 'price': 100.0}])
        line = order.order_line
        self.product.standard_price = 75.0                # AVCO revaluation -> 75
        self.assertAlmostEqual(line.sma_std_cost_unit_company, 60.0, places=4)

        # Partial: 4 delivered at actual 75, 6 open at snapshot 60
        self._deliver(order, 4)
        self.assertEqual(line.sma_cost_source, 'mixed')
        self.assertAlmostEqual(line.sma_delivered_cost_company, 300.0, places=2)
        self.assertAlmostEqual(line.sma_cost_total, 660.0, places=2)
        self.assertAlmostEqual(line.sma_cost_unit, 66.0, places=4)
        self.assertAlmostEqual(line.sma_margin, 340.0, places=2)
        self.assertAlmostEqual(line.sma_margin_percent, 34.0, places=2)
        row = self._report_row(line)
        self.assertAlmostEqual(row.delivered_amount, 400.0, places=2)
        self.assertAlmostEqual(row.delivered_margin, 100.0, places=2)
        self.assertAlmostEqual(row.delivered_margin_percent, 25.0, places=2)

        # Backorder: the remaining 6 at actual 75 -> fully actual cost
        self._deliver(order, 6)
        self.assertEqual(line.sma_cost_source, 'actual')
        self.assertAlmostEqual(line.sma_cost_unit, 75.0, places=4)
        self.assertAlmostEqual(line.sma_cost_total, 750.0, places=2)
        self.assertAlmostEqual(line.sma_margin, 250.0, places=2)

    def test_12_order_totals_are_weighted(self):
        order = self._create_order([
            {'qty': 1, 'price': 100.0},                           # margin 40 (40 %)
            {'qty': 1, 'price': 300.0, 'product': self.service},  # margin 30 (10 %)
        ])
        self.assertAlmostEqual(order.sma_amount_cost, 330.0, places=2)
        self.assertAlmostEqual(order.sma_margin, 70.0, places=2)
        self.assertAlmostEqual(order.sma_margin_percent, 17.5, places=2)

    def test_13_section_and_note_lines_are_neutral(self):
        order = self._create_order([{'qty': 1, 'price': 100.0}], confirm=False)
        order.write({'order_line': [
            (0, 0, {'display_type': 'line_section', 'name': 'Hardware'}),
            (0, 0, {'display_type': 'line_note', 'name': 'Note'}),
        ]})
        sections = order.order_line.filtered('display_type')
        self.assertEqual(set(sections.mapped('sma_margin')), {0.0})
        self.assertEqual(set(sections.mapped('sma_cost_source')), {'none'})
        self.assertAlmostEqual(order.sma_margin, 40.0, places=2)

    def test_14_new_order_without_currency(self):
        """A brand-new order (no pricelist yet, so no currency) must not crash."""
        order = self.env['sale.order'].new({})
        self.assertEqual(order.sma_margin_percent, 0.0)
        self.assertEqual(order.sma_margin, 0.0)
