# Sales Margin & Profitability Analytics (`sale_margin_analytics`) — Odoo 17

## Architecture

```
sale.order.line  ──(stored, batch-computed)──►  cost / margin fields (order + company currency)
       │                                                  │
       │ stock.move ─► stock.valuation.layer              │
       │ (1 read_group per batch, no N+1)                 ▼
       └──────────────────────────────►  sale.margin.report  (_table_query, read-only SQL model)
                                                │  pivot · graph · list · record rules
                                                ▼
                         sale.margin.report.wizard ──► PDF (QWeb + wkhtmltopdf)
                                                   └─► Excel (xlsxwriter, 3 sheets)
                                                   └─► Pivot analysis with the same domain
```

### 1. Cost engine (`sale.order.line`)
| Field | Meaning |
|---|---|
| `sma_std_cost_unit_company` | Standard-cost **snapshot** (company currency, per line UoM). Frozen at product/UoM selection so historical margins never drift; *Refresh Standard Costs* button re-captures it for undelivered lines. |
| `sma_cost_unit(_company)`, `sma_cost_total(_company)` | Unit / total cost in order and company currency. |
| `sma_delivered_cost_company` | Cost of delivered quantities (actual SVL value in valuation mode). |
| `sma_margin`, `sma_margin_percent` | `price_subtotal − cost_total`, and margin / untaxed subtotal × 100. |
| `sma_cost_source` | `standard`, `mixed` (delivered at actual + open at snapshot), `actual`. |

**Cost basis** is a company setting (*Sales → Settings → Margin Analytics*):
* **Standard** – snapshot × qty.
* **Inventory Valuation** – delivered qty at the real FIFO/AVCO cost from `stock.valuation.layer`
  (returns net out automatically), open qty at the snapshot:
  `unit = (−Σ svl.value + snapshot × remaining_qty) / ordered_qty`.

Conversions: costs are computed in company currency and converted with the order's own
`currency_rate`, so the margin % is identical in both currencies. UoM conversions use
`uom._compute_price`. Taxes never touch the margin (`price_subtotal` is untaxed and already
net of the line discount; tax-included prices are netted by Odoo's tax engine).

### 2. Analytical model (`sale.margin.report`)
* `_table_query` SQL sub-query over `sale_order_line` (no view to maintain on upgrade).
* All amounts in company currency (`amount / order.currency_rate`), like `sale.report`.
* Excludes sections, notes, down-payments and cancelled orders.
* `read_group` override returns **revenue-weighted** ratios in every pivot/graph cell:
  margin %, delivered margin %, discount %, unit price, net price and unit cost.

### 3. Security
* Group **Sales Margin Analytics** (implied by *Sales / Administrator*) gates menus, cost
  columns on orders, the report model and the wizard.
* Record rules mirror `sale.report`: multi-company (global), own orders for
  *User: Own Documents Only*, everything for *All Documents*.

### 4. Reports
* **Wizard** (*Sales → Reporting → Margin Analytics → Margin Report*, or *Action → Margin
  Report* on selected orders): period (user time zone), scope, customers, teams,
  salespersons, categories (child_of), products, orders, min/max margin applied per **line**
  or per **order** (weighted), grouping by order/customer/salesperson/team/category/product.
* **PDF**: A4 landscape, KPI cards, margin-health bar, ranking with contribution bars,
  grouped line details with coloured margin pills, grand-total band, page X of Y.
* **Excel**: *Summary* (KPI tiles, parameters, bands, ranking, combined chart),
  *Report* (grouped, collapsible outline, live SUBTOTAL formulas),
  *Data* (flat, autofilter, filter-aware totals, conditional formatting, data bars).

## Tests
```
odoo-bin -d test_db -i sale_margin_analytics --test-tags /sale_margin_analytics --stop-after-init
```
Covers full, partial (standard and valuation basis), discounted, odd-precision, tax-excluded,
tax-included, multi-currency, UoM, zero-price, snapshot freezing, weighted pivot ratios,
excluded lines, record rules, thresholds, payload totals per grouping, Excel and PDF rendering.

## Notes / limits
* Kits (MRP BoM type *kit*): component moves carry the kit's sale line, so the valuation
  basis would mix component quantities — keep the **Standard** basis for kit products.
* Changing the company cost basis recomputes the stored cost fields of that company's lines
  (expected, can take a while on very large databases).
* Margin band thresholds (0 / 10 / 30 %) are constants in the wizard (`margin_band`).

## Porting notes 16.0 → 17.0
* `attrs` replaced by inline `invisible="…"` expressions (order lines, Profitability tab, wizard).
* `sale.order` no longer has the `done` state (locking is the boolean `locked`): removed from the
  report's status selection; "confirmed" filters now use `state = 'sale'`; the refresh button hides on
  `state == 'cancel' or locked`.
* Settings page rewritten for the v17 `<app>` / `<block>` / `<setting>` layout.
* Tests: `stock.move.quantity_done` → `quantity` + `picked`; taxes created with a `country_id`.
* Unchanged on purpose: `<tree>` views (renamed `<list>` only in 18.0), `group_operator`
  (renamed `aggregator` in 18.0), `detailed_type`, `product_uom`, `tax_id`, `stock.valuation.layer`.
