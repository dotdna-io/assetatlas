# Getting Started

Each of your existing systems — vendor tools, SIEMs, data lakes — sees your devices from its own angle, and none of them holds the whole picture. AssetAtlas pulls those partial views together and reconciles them into one coherent record per device, giving you a single, authoritative inventory in place of a dozen overlapping ones. This page is the short path from a fresh install to data on screen.

## First-run path

A new install has no assets yet. To get real data in:

- **Connect a data source.** Go to **Hub** (the Integrations page) and add a connection — a vendor API, a SIEM/data lake, or a file upload. The *Connecting Data Sources* topic walks through the wizard. The first collection run starts automatically when the integration is created.
- **Let it collect and build.** AssetAtlas pulls records from the source and the pipeline merges them into assets. This runs in the background; give it time on the first run — an empty inventory right after connecting is normal, since records become assets only once the pipeline has processed them.
- **Review the inventory.** Open **Home** (the dashboard) for the total asset count, the category breakdown (Server, Workstation, Mobile, Network, IoT, OT, Misc), a confidence overview (*Confirmed / Under-covered / Shadows* — how well each asset is backed by its sources), a trend of assets coming and going, and the latest alerts. Use **Explore** to search and filter individual assets; each row in the dashboard's *Data Sources* table links to that source's detail.
- **Set up rules and alerts as needed.** Use **Rules** to tag and classify assets automatically, and **Alerts** to be notified when conditions you define are met. These are optional and can be added once assets exist.

## Key areas

- **Home** — the dashboard: totals, category breakdown, confidence overview, activity trend, latest alerts, and your data sources at a glance.
- **Explore** — search and filter the asset inventory.
- **Reporting** — saved and generated reports over the inventory.
- **Alerts** — alerts that have fired, with filtering and drill-down.
- **Rules** — automatic tagging, classification, compliance, posture, and alert rules.
- **Hub** — every data source connection: create, run, edit, and monitor integrations.
- **Settings** — data sources beyond integrations (Enrichment Datasets, Reference Data) and system configuration.
- **Chatbot** — an assistant bubble for asking about the inventory in plain language. It appears on every page once the chatbot is enabled server-side *and* a provider is configured under **Settings → Chatbot**; until both are true there is no bubble anywhere. It can also be published to Microsoft Teams — see *Chatbot Teams*.
