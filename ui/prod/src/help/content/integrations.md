# Connecting Data Sources

An integration connects AssetAtlas to one of your systems (a vendor API, a SIEM/data lake, or an uploaded file) and pulls device records from it on a schedule or on demand. The **Integrations** page lists every connection and is where you create, run, and manage them.

## How to connect a data source

The wizard opens on a connector picker, then walks you through four steps — **Basic Details**, **Connection**, **Field Mapping**, and **Review**:

- On the Integrations page, click **New Integration**. This opens the wizard.
- **Pick a connector.** Connectors are grouped into *SIEM / Datalake*, *Vendor (with direct connection)*, *Vendor (without direct connection)*, and *Custom*. Use the search box to filter. Click the tile you want.
- **Choose how to connect** (vendor connectors only). If the vendor supports it, you'll be asked *How do you want to connect?* — pick **Direct** (connect with the vendor's own API credentials) or **Via SIEM / Data Lake** (pull that vendor's data from a SIEM or data lake, then select which transport). *Manual File Upload* and *Custom HTTP API* skip this step. (Manual File Upload accepts CSV or JSON files only — not XLSX.)
- **Basic Details.** Set an **Integration Name** and a **Data Source Category** (both required). Optionally add a description, pick a logo, and use the *Enable this integration* toggle. Name, logo, and category are pre-filled from the connector where possible.
- **Connection.** Fill in the connection settings (credentials, URL, etc. — the fields depend on the connector). Click **Test Connection** to confirm AssetAtlas can reach the service; a successful test also fetches sample records and discovers their fields for the next step (skip the test and you skip that assist). Set the **Schedule** here too — a saved schedule only runs while *Enable automatic schedule* is on; with it off, the integration collects only when you click **Run Now**. You cannot advance until the test passes or *Skip connection test* is checked (manual upload and edit mode are exempt).
- **Field Mapping.** Map source fields to AssetAtlas fields. You must map at least one field, and at least one identifier — **hostname, fqdn, ip, or mac**. AssetAtlas auto-matches common field names; review and adjust. *Data Filters* and *Transformations* are optional and collapsed by default.
- **Review.** Check the summary. **Run the asset pipeline once this data source is created** is ticked here by default — leave it on to have the new source's records built into the inventory right after collection, instead of waiting for the next scheduled pipeline run (see *Asset-Generation Pipeline*). Click **Create Integration**; the integration is created and its first collection run starts automatically.

## Managing integrations

The list has a card view and a table view (toggle next to the **Integrations** heading; the choice is remembered). Both expose the same per-integration actions:

- **Run Now** — starts a collection run immediately. Disabled while a run is already in progress.
- **Enable / disable** — the toggle switch. A disabled integration is skipped by the scheduler and won't collect data.
- **Edit** — reopens the wizard against the saved integration. In edit mode you can jump straight to any step or **Skip to Review**.
- **Clone** — opens the wizard pre-filled from an existing integration as a new one (name suffixed *(Copy)*).
- **Delete** — removes the integration after a confirmation prompt. This cannot be undone.

Click an integration's name to open its details page. Use the search box to filter by name, type, or description.

## Integration groups

Above the list, the **Integration Groups** section lets you organise related connections. **Create Group** makes a group; each group card shows its name, connector type, and member count, and expands to list its members. Per group you can **Add Members**, **Rename**, and **Delete**, and remove members individually.

## Integration details

Clicking an integration's name opens its details page: a header with **Run Now**, **Edit**, and **Delete**; health, connection (with its own **Test Connection**), and schedule cards; a table of collection-run batches; a mapping preview; and a recent-errors panel. The page refreshes itself while a run is active.

## Key fields

- **Status badge** — **Running** while a collection run is active, **Error** if the last run failed, otherwise **Enabled** or **Disabled**. Running and Error take precedence over the enabled/disabled state.
- **Schedule** — derived from the cron expression and shown in plain language (e.g. *Daily at 02:00*, *Every 6 hours*). The table view shows *Not scheduled* when the schedule is turned off.
- **Last sync** — when data was last collected (relative, e.g. *2h ago*); *Never* if it has not yet run.
- **Next Run** (table view) — when the next scheduled run is due; *Not scheduled* if the schedule is off.
- **Records** — record count from the most recent batch. Shows `—` while a run is in progress with no records yet.
- **Assets** (card view) — how many assets the most recent batch contributed.
