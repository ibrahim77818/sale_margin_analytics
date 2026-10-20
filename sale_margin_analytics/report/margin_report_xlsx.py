"""Excel export of the sales margin report (xlsxwriter, no extra Odoo dependency).

Workbook layout
---------------
* Summary : KPI tiles, report parameters, margin-health bands, ranking table
            and a combined margin / margin % chart.
* Report  : the same grouping as the PDF, with collapsible (outlined) line
            details and live SUBTOTAL formulas per group and grand total.
* Data    : one flat row per order line with auto-filter, frozen panes and
            filter-aware SUBTOTAL totals - ready for pivot tables / Power BI.
"""
import io

import xlsxwriter
from xlsxwriter.utility import xl_col_to_name, xl_rowcol_to_cell

from odoo import _

INK = '#0F172A'
MUTED = '#64748B'
LINE = '#E2E8F0'
SOFT = '#F8FAFC'
ACCENT = '#0D9488'
ACCENT_SOFT = '#F0FDFA'
BANDS = {
    'neg': ('#B91C1C', '#FEE2E2'),
    'low': ('#B45309', '#FEF3C7'),
    'mid': ('#1D4ED8', '#DBEAFE'),
    'high': ('#047857', '#D1FAE5'),
}


class MarginXlsxBuilder:

    def __init__(self, env, payload):
        self.env = env  # also lets odoo's _() resolve the user language
        self.p = payload
        self.currency = payload['currency']
        self.fmt = {}
        self.wb = None

    # ------------------------------------------------------------------ api
    def build(self):
        output = io.BytesIO()
        self.wb = xlsxwriter.Workbook(output, {'in_memory': True, 'remove_timezone': True})
        self.wb.set_properties({
            'title': _('Sales Margin Report'),
            'company': self.p['company'].name,
            'author': self.p['generated_by'],
        })
        self._init_formats()
        self._build_summary()
        self._build_grouped()
        self._build_data()
        self.wb.close()
        return output.getvalue()

    # -------------------------------------------------------------- formats
    def _init_formats(self):
        dp = self.currency.decimal_places
        num = '#,##0' + ('.' + '0' * dp if dp else '')
        symbol = (self.currency.symbol or self.currency.name).replace('"', '')
        money = ('"%s" %s' % (symbol, num) if self.currency.position == 'before'
                 else '%s "%s"' % (num, symbol))
        money = '%s;[Red]-%s' % (money, money)
        price = '#,##0.' + '0' * max(self.p['price_digits'], 2)
        num_formats = {
            'text': None, 'date': 'dd mmm yyyy', 'qty': '#,##0.00',
            'price': price, 'money': money, 'pct': '0.00%', 'int': '#,##0',
        }
        self.money_format = money
        base = {'font_name': 'Calibri', 'font_size': 10, 'valign': 'vcenter'}

        def add(**kw):
            return self.wb.add_format(dict(base, **kw))

        F = self.fmt
        for kind, nf in num_formats.items():
            extra = {'num_format': nf} if nf else {}
            F[kind] = add(bottom=1, bottom_color=LINE, **extra)
            F[kind + '_alt'] = add(bottom=1, bottom_color=LINE, bg_color=SOFT, **extra)
            F[kind + '_sub'] = add(bold=True, bg_color=ACCENT_SOFT, top=1, top_color=ACCENT, **extra)
            F[kind + '_total'] = add(bold=True, font_color='#FFFFFF', bg_color=INK, **extra)
            F['tile_' + kind] = add(bold=True, font_size=16, font_color=INK, bg_color=SOFT,
                                    align='left', indent=1, bottom=1, bottom_color=LINE, **extra)
        F['title'] = add(bold=True, font_size=18, font_color=INK)
        F['subtitle'] = add(font_color=MUTED)
        F['header'] = add(bold=True, font_color='#FFFFFF', bg_color=INK, text_wrap=True,
                          align='center', border=1, border_color=INK)
        F['section'] = add(bold=True, font_size=12, font_color=INK, bottom=2, bottom_color=ACCENT)
        F['tile_label'] = add(font_size=8, font_color=MUTED, bg_color=SOFT, top=5, top_color=ACCENT,
                              align='left', indent=1)
        F['kv_key'] = add(font_color=MUTED)
        F['kv_val'] = add(bold=True, font_color=INK)
        F['group'] = add(bold=True, font_size=11, font_color=INK, bg_color='#E2E8F0',
                         left=5, left_color=ACCENT)
        for key, (font, fill) in BANDS.items():
            F['band_' + key] = self.wb.add_format({'font_color': font, 'bg_color': fill, 'bold': True})
            F['band_cell_' + key] = add(font_color=font, bg_color=fill, bold=True,
                                        bottom=1, bottom_color=LINE)

    # -------------------------------------------------------------- helpers
    def _columns(self, keys=None):
        definitions = {
            'order': (_('Order'), 14, 'text'),
            'date': (_('Date'), 12, 'date'),
            'state': (_('Status'), 13, 'text'),
            'customer': (_('Customer'), 28, 'text'),
            'salesperson': (_('Salesperson'), 18, 'text'),
            'team': (_('Sales Team'), 16, 'text'),
            'product': (_('Product'), 34, 'text'),
            'category': (_('Category'), 22, 'text'),
            'qty': (_('Qty'), 9, 'qty'),
            'uom': (_('UoM'), 8, 'text'),
            'price_unit': (_('Unit Price'), 12, 'price'),
            'discount': (_('Disc. %'), 8, 'pct'),
            'price_unit_net': (_('Net Unit Price'), 13, 'price'),
            'cost_unit': (_('Unit Cost'), 12, 'price'),
            'subtotal': (_('Subtotal'), 15, 'money'),
            'tax': (_('Taxes'), 13, 'money'),
            'total': (_('Total'), 15, 'money'),
            'cost_total': (_('Total Cost'), 15, 'money'),
            'margin': (_('Margin'), 15, 'money'),
            'margin_pct': (_('Margin %'), 10, 'pct'),
            'qty_delivered': (_('Delivered Qty'), 11, 'qty'),
            'delivered_margin': (_('Delivered Margin'), 15, 'money'),
            'cost_source': (_('Cost Basis'), 17, 'text'),
            'order_currency': (_('Order Curr.'), 9, 'text'),
        }
        keys = keys or list(definitions)
        return [(key,) + definitions[key] for key in keys]

    def _write(self, ws, row, col, kind, value, variant=''):
        fmt = self.fmt[kind + variant]
        if value is None or value is False or value == '':
            ws.write_blank(row, col, None, fmt)
        elif kind == 'date':
            ws.write_datetime(row, col, value, fmt)
        elif kind == 'text':
            ws.write_string(row, col, str(value), fmt)
        elif kind == 'pct':
            ws.write_number(row, col, value / 100.0, fmt)
        else:
            ws.write_number(row, col, value, fmt)

    @staticmethod
    def _ratio(row, num_col, den_col):
        num = xl_rowcol_to_cell(row, num_col)
        den = xl_rowcol_to_cell(row, den_col)
        return '=IF(%s=0,0,%s/%s)' % (den, num, den)

    def _band_conditional(self, ws, first_row, col, last_row):
        rng = (first_row, col, last_row, col)
        F = self.fmt
        ws.conditional_format(*rng, {'type': 'cell', 'criteria': '<', 'value': 0, 'format': F['band_neg']})
        ws.conditional_format(*rng, {'type': 'cell', 'criteria': 'between', 'minimum': 0,
                                     'maximum': 0.09999, 'format': F['band_low']})
        ws.conditional_format(*rng, {'type': 'cell', 'criteria': 'between', 'minimum': 0.1,
                                     'maximum': 0.29999, 'format': F['band_mid']})
        ws.conditional_format(*rng, {'type': 'cell', 'criteria': '>=', 'value': 0.3,
                                     'format': F['band_high']})

    def _sheet_title(self, ws, title, last_col):
        ws.write(0, 0, title, self.fmt['title'])
        ws.write(1, 0, '%s  ·  %s  ·  %s %s' % (
            self.p['company'].name, self.p['period_label'], _('Currency:'), self.currency.name),
            self.fmt['subtitle'])
        ws.set_row(0, 28)

    # -------------------------------------------------------------- summary
    def _build_summary(self):
        ws = self.wb.add_worksheet(_('Summary'))
        F, t = self.fmt, self.p['totals']
        ws.hide_gridlines(2)
        ws.set_column(0, 0, 2)
        ws.set_column(1, 1, 34)
        ws.set_column(2, 8, 20)
        ws.set_column(9, 9, 3)
        ws.write(1, 1, _('Sales Margin & Profitability'), F['title'])
        ws.write(2, 1, '%s  ·  %s  ·  %s %s' % (
            self.p['company'].name, self.p['period_label'], _('Generated'), self.p['generated_on']),
            F['subtitle'])
        ws.set_row(1, 30)

        tiles = [
            (_('REVENUE (UNTAXED)'), t['subtotal'], 'money'),
            (_('TOTAL COST'), t['cost_total'], 'money'),
            (_('GROSS MARGIN'), t['margin'], 'money'),
            (_('MARGIN RATE'), t['margin_pct'] / 100.0, 'pct'),
            (_('SALES ORDERS'), t['orders'], 'int'),
            (_('ORDER LINES'), t['count'], 'int'),
        ]
        # Tiles on row 4/5, one per column B..G
        for i, (label, value, kind) in enumerate(tiles):
            col = 1 + i
            ws.write(4, col, label, F['tile_label'])
            ws.write(5, col, value, F['tile_' + kind])
        ws.set_row(4, 18)
        ws.set_row(5, 34)

        row = 7
        ws.write(row, 1, _('Report parameters'), F['section'])
        row += 1
        for key, value in self.p['filters']:
            ws.write(row, 1, key, F['kv_key'])
            ws.write(row, 2, value, F['kv_val'])
            row += 1

        # Margin health
        row += 1
        ws.write(row, 1, _('Margin health'), F['section'])
        row += 1
        for col, label in enumerate([_('Band'), _('Lines'), _('Revenue'), _('Margin'),
                                     _('Share of revenue')], start=1):
            ws.write(row, col, label, F['header'])
        row += 1
        for band in self.p['bands']:
            ws.write(row, 1, band['label'], F['band_cell_' + band['key']])
            ws.write(row, 2, band['count'], F['int'])
            ws.write(row, 3, band['revenue'], F['money'])
            ws.write(row, 4, band['margin'], F['money'])
            ws.write(row, 5, band['share'] / 100.0, F['pct'])
            row += 1

        # Ranking
        row += 1
        ws.write(row, 1, self.p['summary_title'], F['section'])
        row += 1
        header_row = row
        headers = [self.p['group_label'], _('Orders'), _('Lines'), _('Revenue'), _('Cost'),
                   _('Margin'), _('Margin %'), _('Share of margin')]
        for col, label in enumerate(headers, start=1):
            ws.write(row, col, label, F['header'])
        ws.set_row(row, 22)
        row += 1
        first = row
        ranking = self.p['ranking']
        for idx, group in enumerate(ranking):
            gt = group['totals']
            v = '_alt' if idx % 2 else ''
            ws.write_string(row, 1, group['name'], F['text' + v])
            ws.write_number(row, 2, gt['orders'], F['int' + v])
            ws.write_number(row, 3, gt['count'], F['int' + v])
            ws.write_number(row, 4, gt['subtotal'], F['money' + v])
            ws.write_number(row, 5, gt['cost_total'], F['money' + v])
            ws.write_number(row, 6, gt['margin'], F['money' + v])
            ws.write_formula(row, 7, self._ratio(row, 6, 4), F['pct' + v], gt['margin_pct'] / 100.0)
            row += 1
        last = row - 1
        total_row = row
        for idx in range(first, last + 1):
            share_value = ranking[idx - first]['share'] / 100.0
            ws.write_formula(idx, 8, '=IF(%s=0,0,%s/%s)' % (
                xl_rowcol_to_cell(total_row, 6, True, True), xl_rowcol_to_cell(idx, 6),
                xl_rowcol_to_cell(total_row, 6, True, True)),
                F['pct' + ('_alt' if (idx - first) % 2 else '')], share_value)
        ws.write(total_row, 1, _('Total'), F['text_total'])
        ws.write_formula(total_row, 2, '=%s' % t['orders'], F['int_total'], t['orders'])
        for col, key in ((3, 'count'), (4, 'subtotal'), (5, 'cost_total'), (6, 'margin')):
            letter = xl_col_to_name(col)
            ws.write_formula(total_row, col, '=SUM(%s%s:%s%s)' % (letter, first + 1, letter, last + 1),
                             F['int_total' if key == 'count' else 'money_total'], t[key])
        ws.write_formula(total_row, 7, self._ratio(total_row, 6, 4), F['pct_total'], t['margin_pct'] / 100.0)
        ws.write_formula(total_row, 8, '=SUM(I%s:I%s)' % (first + 1, last + 1), F['pct_total'],
                         1.0 if t['margin'] else 0.0)
        self._band_conditional(ws, first, 7, last)
        ws.conditional_format(first, 6, last, 6, {
            'type': 'data_bar', 'bar_color': '#5EEAD4', 'bar_solid': True,
            'data_bar_2010': True, 'bar_negative_color': '#FCA5A5'})

        # Chart: top 12 groups by margin, margin % on the secondary axis
        count = min(len(ranking), 12)
        if count:
            chart = self.wb.add_chart({'type': 'column'})
            chart.add_series({
                'name': _('Margin'),
                'categories': [ws.name, first, 1, first + count - 1, 1],
                'values': [ws.name, first, 6, first + count - 1, 6],
                'fill': {'color': ACCENT}, 'border': {'none': True}, 'gap': 70,
            })
            line = self.wb.add_chart({'type': 'line'})
            line.add_series({
                'name': _('Margin %'),
                'categories': [ws.name, first, 1, first + count - 1, 1],
                'values': [ws.name, first, 7, first + count - 1, 7],
                'y2_axis': True,
                'line': {'color': INK, 'width': 1.75},
                'marker': {'type': 'circle', 'size': 6,
                           'fill': {'color': INK}, 'border': {'color': INK}},
            })
            chart.combine(line)
            chart.set_title({'name': _('Top %s by margin', self.p['group_label']),
                             'name_font': {'size': 12, 'bold': True, 'color': INK}})
            chart.set_legend({'position': 'bottom'})
            chart.set_x_axis({'num_font': {'rotation': -35, 'size': 8}})
            chart.set_y_axis({'num_format': '#,##0', 'major_gridlines': {
                'visible': True, 'line': {'color': LINE}}})
            line.set_y2_axis({'num_format': '0%'})
            chart.set_chartarea({'border': {'color': LINE}})
            chart.set_size({'width': 760, 'height': 380})
            ws.insert_chart(header_row, 10, chart)

        ws.set_landscape()
        ws.fit_to_pages(1, 0)
        ws.activate()

    # -------------------------------------------------------------- grouped
    def _build_grouped(self):
        ws = self.wb.add_worksheet(_('Report'))
        F = self.fmt
        if self.p['group_by'] == 'order':
            keys = ['product', 'category', 'qty', 'uom', 'price_unit', 'discount', 'price_unit_net',
                    'cost_unit', 'subtotal', 'tax', 'cost_total', 'margin', 'margin_pct']
        else:
            keys = ['order', 'date', 'customer', 'product', 'qty', 'uom', 'price_unit_net',
                    'cost_unit', 'subtotal', 'tax', 'cost_total', 'margin', 'margin_pct']
        cols = self._columns(keys)
        n = len(cols)
        pos = {c[0]: i for i, c in enumerate(cols)}
        self._sheet_title(ws, _('Sales Margin Report by %s', self.p['group_label']), n - 1)
        header_row = 3
        for i, (key, label, width, kind) in enumerate(cols):
            ws.set_column(i, i, width)
            ws.write(header_row, i, label, F['header'])
        ws.set_row(header_row, 30)

        first_money = min(i for i, c in enumerate(cols) if c[3] == 'money')
        row = header_row + 1
        first_data = row
        pct_ranges = []
        for group in self.p['groups']:
            gt = group['totals']
            label = group['name'] + ('   ·   %s' % group['meta'] if group['meta'] else '')
            ws.merge_range(row, 0, row, n - 1, label, F['group'])
            ws.set_row(row, 20)
            row += 1
            start = row
            for idx, line in enumerate(group['lines']):
                variant = '_alt' if idx % 2 else ''
                for i, (key, _label, _width, kind) in enumerate(cols):
                    self._write(ws, row, i, kind, line.get(key), variant)
                ws.set_row(row, None, None, {'level': 1})
                row += 1
            end = row - 1
            pct_ranges.append((start, end))
            ws.merge_range(row, 0, row, first_money - 1,
                           '%s · %s' % (_('Subtotal'), group['name']), F['text_sub'])
            for i in range(first_money, n):
                key, _label, _width, kind = cols[i]
                letter = xl_col_to_name(i)
                if kind == 'money':
                    ws.write_formula(row, i, '=SUBTOTAL(9,%s%s:%s%s)' % (letter, start + 1, letter, end + 1),
                                     F['money_sub'], gt.get(key, 0.0))
                elif key == 'margin_pct':
                    ws.write_formula(row, i, self._ratio(row, pos['margin'], pos['subtotal']),
                                     F['pct_sub'], gt['margin_pct'] / 100.0)
                else:
                    ws.write_blank(row, i, None, F['text_sub'])
            row += 2

        t = self.p['totals']
        ws.merge_range(row, 0, row, first_money - 1, _('Grand Total'), F['text_total'])
        for i in range(first_money, n):
            key, _label, _width, kind = cols[i]
            letter = xl_col_to_name(i)
            if kind == 'money':
                # SUBTOTAL ignores the nested group SUBTOTAL rows
                ws.write_formula(row, i, '=SUBTOTAL(9,%s%s:%s%s)' % (letter, first_data + 1, letter, row),
                                 F['money_total'], t.get(key, 0.0))
            elif key == 'margin_pct':
                ws.write_formula(row, i, self._ratio(row, pos['margin'], pos['subtotal']),
                                 F['pct_total'], t['margin_pct'] / 100.0)
            else:
                ws.write_blank(row, i, None, F['text_total'])
        ws.set_row(row, 22)
        for start, end in pct_ranges:
            if end >= start:
                self._band_conditional(ws, start, pos['margin_pct'], end)
        ws.freeze_panes(header_row + 1, 0)
        ws.outline_settings(True, False, True, False)
        ws.fit_to_pages(1, 0)
        ws.set_landscape()
        ws.repeat_rows(header_row)

    # ----------------------------------------------------------------- data
    def _build_data(self):
        ws = self.wb.add_worksheet(_('Data'))
        F = self.fmt
        cols = self._columns()
        n = len(cols)
        pos = {c[0]: i for i, c in enumerate(cols)}
        lines = self.p['lines']
        self._sheet_title(ws, _('Sales Margin – Line Data'), n - 1)
        totals_row, header_row, first = 3, 4, 5
        last = first + max(len(lines), 1) - 1

        for i, (key, label, width, kind) in enumerate(cols):
            ws.set_column(i, i, width)
            ws.write(header_row, i, label, F['header'])
        ws.set_row(header_row, 30)
        for row, line in enumerate(lines, start=first):
            for i, (key, _label, _width, kind) in enumerate(cols):
                self._write(ws, row, i, kind, line.get(key))

        t = self.p['totals']
        ws.write(totals_row, 0, _('Filtered total'), F['text_total'])
        for i, (key, _label, _width, kind) in enumerate(cols):
            if i == 0:
                continue
            letter = xl_col_to_name(i)
            if kind == 'money':
                ws.write_formula(totals_row, i, '=SUBTOTAL(9,%s%s:%s%s)' % (letter, first + 1, letter, last + 1),
                                 F['money_total'], t.get(key, 0.0))
            elif key == 'margin_pct':
                ws.write_formula(totals_row, i, self._ratio(totals_row, pos['margin'], pos['subtotal']),
                                 F['pct_total'], t['margin_pct'] / 100.0)
            else:
                ws.write_blank(totals_row, i, None, F['text_total'])
        ws.set_row(totals_row, 20)

        if lines:
            ws.autofilter(header_row, 0, last, n - 1)
            self._band_conditional(ws, first, pos['margin_pct'], last)
            ws.conditional_format(first, pos['margin'], last, pos['margin'], {
                'type': 'data_bar', 'bar_color': '#5EEAD4', 'bar_solid': True,
                'data_bar_2010': True, 'bar_negative_color': '#FCA5A5'})
        ws.freeze_panes(first, 1)
