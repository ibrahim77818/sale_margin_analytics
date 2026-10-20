import base64

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import SaleMarginCommon


@tagged('post_install', '-at_install', 'sale_margin_analytics')
class TestMarginExport(SaleMarginCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.order_high = cls._create_order([{'qty': 1, 'price': 100.0}])                  # 40 %
        cls.order_mixed = cls._create_order([
            {'qty': 1, 'price': 100.0},                                                    # 40 %
            {'qty': 1, 'price': 300.0, 'product': cls.service},                            # 10 %
        ])                                                                                 # 17.5 %

    def _wizard(self, **vals):
        values = {
            'order_ids': [(6, 0, (self.order_high | self.order_mixed).ids)],
            'date_from': False,
            'date_to': False,
        }
        values.update(vals)
        return self.env['sale.margin.report.wizard'].create(values)

    def test_01_line_level_threshold(self):
        wizard = self._wizard(use_margin_min=True, margin_min=20.0)
        records = wizard._get_report_records()
        self.assertEqual(records.product_id, self.product)
        self.assertEqual(len(records), 2)

    def test_02_order_level_threshold(self):
        wizard = self._wizard(margin_filter_level='order', use_margin_min=True, margin_min=20.0)
        self.assertEqual(wizard._get_report_records().order_id, self.order_high)
        wizard.write({'use_margin_min': False, 'use_margin_max': True, 'margin_max': 20.0})
        self.assertEqual(wizard._get_report_records().order_id, self.order_mixed)

    def test_03_invalid_ranges(self):
        with self.assertRaises(ValidationError):
            self._wizard(use_margin_min=True, margin_min=30.0, use_margin_max=True, margin_max=10.0)

    def test_04_payload_totals_for_every_grouping(self):
        wizard = self._wizard()
        for group_by in ('order', 'partner', 'user', 'team', 'category', 'product'):
            wizard.group_by = group_by
            payload = wizard._prepare_report_payload()
            totals = payload['totals']
            self.assertAlmostEqual(totals['subtotal'], 500.0, places=2)
            self.assertAlmostEqual(totals['margin'], 110.0, places=2)
            self.assertAlmostEqual(totals['margin_pct'], 22.0, places=2)
            self.assertAlmostEqual(
                sum(g['totals']['margin'] for g in payload['groups']), 110.0, places=2)
            self.assertEqual(sum(b['count'] for b in payload['bands']), 3)

    def test_05_no_data_raises(self):
        wizard = self._wizard(use_margin_min=True, margin_min=99.0)
        with self.assertRaises(UserError):
            wizard.action_export_xlsx()

    def test_06_excel_export(self):
        wizard = self._wizard(group_by='partner')
        action = wizard.action_export_xlsx()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        content = base64.b64decode(wizard.file_data)
        self.assertTrue(content.startswith(b'PK'), "Not a valid xlsx (zip) file")
        self.assertTrue(wizard.file_name.endswith('.xlsx'))

    def test_07_pdf_html_rendering(self):
        wizard = self._wizard()
        html, _fmt = self.env['ir.actions.report']._render_qweb_html(
            'sale_margin_analytics.action_report_margin_pdf', wizard.ids)
        html = html.decode() if isinstance(html, bytes) else str(html)
        self.assertIn(self.order_high.name, html)
        self.assertIn(self.order_mixed.name, html)
        self.assertIn('22.00%', html)

    def test_08_open_analysis_action(self):
        wizard = self._wizard(group_by='category')
        action = wizard.action_open_analysis()
        self.assertEqual(action['res_model'], 'sale.margin.report')
        self.assertEqual(action['context']['pivot_row_groupby'], ['categ_id'])
        rows = self.env['sale.margin.report'].search(action['domain'])
        self.assertEqual(len(rows), 3)

    def test_09_defaults_from_selected_orders(self):
        wizard = self.env['sale.margin.report.wizard'].with_context(
            active_model='sale.order', active_ids=self.order_high.ids).create({})
        self.assertEqual(wizard.order_ids, self.order_high)
        self.assertFalse(wizard.date_from)
        self.assertEqual(wizard.state_scope, 'all')
