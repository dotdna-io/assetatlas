# Reports & Charts

A report is a saved dashboard of charts built from your asset inventory. The **Reporting** page lists every report; each report holds one or more charts laid out on a grid.

## What's on the page

Each report is a card showing a preview of its charts, the chart count, and when it was last updated. Reports shipped with AssetAtlas are marked with an AssetAtlas badge. Clicking a card opens that report in a new tab.

- **Search** — filters reports by title and description.
- **Sort** — *Recently updated*, *Newest first*, or *Alphabetical*.
- **Create Report** — opens a new, empty report.

## Building a report

A new report opens in edit mode. An existing report is read-only until you click **Edit**.

- Set a **Report title** (required to save) and an optional **Description**.
- Click **Add Chart**. In the *Add Chart* dialog, pick a chart type from *Current State* (point-in-time charts) or *Trends Over Time* (history charts), then click **Add to Report**.
- The chart is placed on the grid and its configuration dialog opens. Set the **Chart name**, then answer the chart's questions — typically a **Dimension** (the field to break down by), an optional **Compare by** field, limits, sort order, and an optional **Filter assets** builder to scope the data. A live **Preview** updates as you change settings. Click **Save Configuration**.
- A **Data Table** chart is configured differently: scope it with filters, tick the **Visible columns** grouped by data source, and set **Rows per page**.
- Arrange the grid: drag a chart by its handle to reorder, use the resize handle to cycle through its allowed sizes, the gear to reconfigure, and the x to remove it.
- Click **Save** (or **Create** on a new report) to persist. The save control only appears once there are unsaved changes. Until then your edits are a working copy — leaving the page without saving discards them.

## Managing reports

Open a report and use the header buttons:

- **Edit** — switches the report from read-only into edit mode so you can change its title, description, and charts.
- **Clone** — copies the report and opens the copy in a new tab.
- **Delete** — removes the report after a confirmation prompt. This cannot be undone.
