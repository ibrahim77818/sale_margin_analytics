from odoo import api, models


def fmt_num(value, digits=2):
    return '{:.{d}f}'.format(value or 0.0, d=digits)


def fmt_pct(value, digits=2):
    return fmt_num(value, digits) + '%'


class SaleMarginPdfReport(models.AbstractModel):
    _name = 'report.sale_margin_analytics.report_margin_document'
    _description = 'Sales Margin PDF Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['sale.margin.report.wizard'].browse(docids)
        return {
            'doc_ids': docids,
            'doc_model': 'sale.margin.report.wizard',
            'docs': docs,
            'payloads': {wiz.id: wiz._prepare_report_payload() for wiz in docs},
            'fmt_pct': fmt_pct,
            'fmt_num': fmt_num,
        }
