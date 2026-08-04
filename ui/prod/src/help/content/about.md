# About

A near read-only snapshot of this AssetAtlas install: which version is deployed, the runtime it's on, subsystem health, and storage usage — the page to check when reporting a problem or before an upgrade. The only control is an admin update-check (below); the figures refresh automatically every 30 seconds.

## What it shows

- **Application** — installed **Version** and **Build Date**, process **Uptime**, and the **Python** version and **Platform** the server runs on. Admins also get a **Check for updates** toggle and a **Check now** button; when an update is available the version carries an *update available* badge — see *Updating AssetAtlas* for how to apply it.
- **Inventory** — **Total Assets** and **Data Sources** from the last pipeline run, the **Last Pipeline Run** time, and **Connectors** / **Active Connectors** with the **Next Sync** scheduled. Shown only when this data is available.
- **Health** — connectivity status for **Database**, **Cache**, **Scheduler** (with its active job count), and **Semantic Search**. Each is colour-coded (healthy / degraded / unhealthy / unavailable).
- **Storage** — database size on disk, cache memory in use/peak and connected clients, log size and file count, and the largest tables by row count. Sections appear only for the storage backends that are present.
