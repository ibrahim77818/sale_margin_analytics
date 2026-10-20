# Sales Margin & Profitability Analytics (`sale_margin_analytics`) — Odoo 19

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

## Porting notes 16.0 → 19.0
* `attrs` → inline `invisible="…"`; `sale.order` state `done` removed (`locked` boolean).
* Settings page uses the `<app>` / `<block>` / `<setting>` layout.
* `<tree>` → `<list>`; `group_operator` → `aggregator`; storable products → `is_storable`.
* `sale.order.line`: `product_uom` → `product_uom_id`, `tax_id` → `tax_ids`.
* `groups_id` → `group_ids` (window-action binding, test users).
* `stock.valuation.layer` removed: the valuation basis now reads `stock.move.value` of done
  customer deliveries (minus customer returns). If `stock.move.value` is absent, lines fall back to
  the standard snapshot instead of failing.
* `read_group` → `formatted_read_group` override (weighted ratios, keys `field:agg`); the wizard
  uses `_read_group`; `odoo.osv.expression` no longer used.
* UoM rework: the SQL report no longer reads `uom_uom.factor`; the product-UoM quantity is the
  stored line field `sma_product_qty`. The list no longer depends on `uom.group_uom`.
* `_table_query` returns an `SQL` object; search-view `<group>` without `expand` / `string`.
* Tests: `quantity` + `picked`, tax `country_id`, `price_include_override`, `formatted_read_group`.
