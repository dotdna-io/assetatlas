# Alerts

The **Alerts** page is a read-only view of alerts that have already fired — from AssetAtlas's own rules, from Prometheus, and from custom rules. You filter, chart, and drill into them here; you don't create or configure alerts on this page (that's done with alert rules on the **Rules** page). Alerts here are read-only — there's nothing to acknowledge or resolve, and one stays until its underlying condition clears. Reach it from **Alerts** in the sidebar.

## What's on the page

- **Time range** — **24h**, **7d**, **30d**, or **Custom** (default 30d).
- **Alert distribution by rule category** — a pie over **Security**, **Compliance**, **Operational**, and **Data quality**. Click a slice to narrow the list to that category; click again to clear.
- **Timeline** — a stacked bar chart of alerts over time, coloured by severity. Click a segment to narrow the list to that severity and time bucket.
- **Search** — one box that searches both assets and alert text. Start typing to pick a specific asset (which pins the list to that asset), or press Enter to search alert titles and descriptions.
- **Severity** — filter to Critical, High, Medium, or Low.

The count on the right reads *N alerts*, or *N of total alerts* with a **Clear** button when a pie or timeline filter is active — those two chart filters narrow only the alerts already listed, while the time range, severity, and search narrow the full set.

## Reading an alert

Each row shows its severity, title, type, how many subjects it involves, and when it fired. Prometheus alerts carry a **Prometheus** badge; custom-rule alerts show a **Custom** label; AssetAtlas's own alerts show no marker.

Click a row to expand it. You'll see the description and, depending on what the alert is about:

- **Asset alerts** — an *Involved Assets* grid (with links to each asset's details and history), an *Assets over time* chart, and a **View N assets in Explorer** link that opens Explore scoped to exactly this alert's assets.
- **Data-source alerts** — the affected data sources, linking to their integration details.
- **Anomaly alerts** — an evidence panel: what was observed versus the norm, a breakdown table, a sample of affected assets, and the full technical detail.
