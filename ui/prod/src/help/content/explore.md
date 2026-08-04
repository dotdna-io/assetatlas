# Explore

**Explore** is the searchable, filterable view of your entire asset inventory. It lists every unified asset and lets you narrow the set, choose which columns to show, pivot into a cross-tab, export to CSV, and open, edit, or bulk-recategorize individual assets. Reach it from **Explore** in the sidebar, or by drilling in from a dashboard category, the confidence overview, or an alert — those land you here with a filter already applied.

## Searching

The search box takes both free text and structured conditions. A structured condition is `Field operator value` — for example `Category = Server` or `OS Normalized ~ windows` — and space-separated conditions combine with AND. As you type, an autocomplete panel suggests fields (grouped by data source), then the operators that field allows, then live values with counts.

Operators include `=` (is), `!=` (is not), `>` `>=` `<` `<=`, `~` (contains), `^` (starts with), `$` (ends with), `IN` (any of a list), `BETWEEN`, `WITHIN` (within the last N days, on a date field), and `EMPTY` / `!EMPTY` (blank / not blank). You can also right-click any cell and choose **Add to search** or **Exclude from search** to build a query by example.

## Columns and filtering

- **Columns** (toolbar) — choose which columns to show, grouped into *Standard Fields*, *Advanced Fields*, one group per data source, *Posture*, and *Tags*. Drag headers to reorder them and drag a header's edge to resize. Your column layout is remembered on this browser only — to share one, save a view (below).
- **Column filters** — click a column header to filter it: a checklist of values with counts, or a *last N days* control on the date columns. (That date control is display-only — to actually narrow by date, use a `WITHIN` condition in the search box.)
- **Page size** — 25, 50, or 100 rows. Changing the search, sort, filters, or page size returns you to page 1.

The default columns are Hostname, IP Address, Category, Subcategory, OS Normalized, Data Source, Model, Confidence Score, and Last Seen, sorted by confidence (highest first).

## Saved views

**Saved filters** (the bookmark in the toolbar) saves the current search, column filters, sort, and column layout as a named view under *My filters*; admins can also publish a view to the shared *Library*. **Copy share link** gives a link that reopens the exact view, layout included. Loading a view whose layout differs from your current one asks whether to apply *Filters only* or the full layout.

## Pivot mode

Toggle the grid icon to cross-tabulate assets instead of listing them: choose a **Rows** dimension, optionally a **Columns** dimension, and what each cell shows — **Count**, **% of total**, **% of row**, or **% of column**. High-cardinality fields (hostname, IP, MAC, serial, FQDN) aren't offered as dimensions.

## Working with an asset

Each row's three-dot menu opens:

- **Asset Details** — the asset's full profile (new tab).
- **Asset History** — its change timeline (new tab).
- **Edit Asset** — a dialog with tabs to **Reclassify** (set category and subcategory), **Tag** the asset, **Merge** it with another asset, **Split** an over-merged asset (admins), or set a display **Label** override.

## Exporting

The **Export** menu writes CSV of the currently visible columns: **Export Full Inventory** (everything, ignoring filters), **Export Filtered Data** (the current search and filters), and **Export Pivot Table** (in pivot mode).

## Bulk actions

The menu above the row-action column acts on **every asset matching the current filter, not just the visible page**: **Recategorize matching** sets a category on all matches, and **Clear category override** resets them to the pipeline's predicted category.
