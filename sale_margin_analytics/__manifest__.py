{
    'name': 'Sales Margin & Profitability Analytics',
    'version': '16.0.1.0.0',
    'category': 'Sales/Sales',
    'summary': 'Line-level cost, margin and profitability analytics with pivot, '
               'graph, modern PDF and Excel reporting',
    'description': """
Sales Margin & Profitability Analytics
======================================
* Stored, batch-computed cost and margin per sales order line
  (standard-cost snapshot or actual inventory-valuation cost).
* Analytical SQL model (pivot / graph / list) in company currency with
  weighted margin %, unit price and unit cost aggregates.
* Report wizard with multi-dimensional filters and margin thresholds
  (line or order level), producing a modern PDF and a multi-sheet Excel workbook.
""",
    'author': 'Ibrahim Yahia',
    'website': 'https://www.linkedin.com/in/ibrahim-yahia',
    'license': 'LGPL-3',
    'depends': ['sale_management', 'sale_stock', 'stock_account'],
    'external_dependencies': {'python': ['xlsxwriter']},
    'data': [
        'security/sale_margin_analytics_security.xml',
        'security/ir.model.access.csv',
        'views/sale_margin_report_views.xml',
        'views/sale_order_views.xml',
        'views/res_config_settings_views.xml',
        'wizard/sale_margin_report_wizard_views.xml',
        'report/margin_report_actions.xml',
        'report/margin_report_templates.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.report_assets_common': [
            'sale_margin_analytics/static/src/scss/margin_report.scss',
        ],
    },
    'installable': True,
    'application': False,
}
