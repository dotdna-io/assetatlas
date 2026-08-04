# Monitoring Dashboards

The **Monitoring** dashboards are an in-app viewer for AssetAtlas's bundled Prometheus dashboards — request rates, database and cache health, pipeline timing, and host and container metrics. They live under **Settings → Monitoring → Dashboards**. Viewing them needs the monitoring stack (Prometheus plus exporters) deployed on the host and enabled here.

## Turning it on

Two independent things control monitoring:

- **Enable monitoring dashboards** (the checkbox at the top) shows or hides the dashboards and the `/api/monitoring` proxy inside the app. It does **not** start or stop the Prometheus containers.
- **The monitoring stack** itself is a set of containers on the host, started and stopped with the `assetatlas-cli enable monitoring` / `disable monitoring` commands shown on the page. The status line reports whether the stack is **Running** or **Stopped or not deployed**.

## Dashboards

When enabled, pick a dashboard tab — **System**, **Database**, **Redis**, **Pipeline**, **Host**, or **Containers** — choose a time range (**Last 1h / 6h / 24h / 7d**, or **Custom**), and a refresh interval (**Off / 10s / 30s / 1m / 5m**, plus a manual refresh).

Each dashboard is a grid of tiles (for example System shows request rate, error rate, and latency percentiles; Host shows CPU, memory, disk, and network). A tile that can't load offers **Retry**; one with no data for the range says so.

## Metrics storage & retention

The **Metrics storage & retention** panel shows disk used, history retained, ingest rate, and active series. **Desired retention** picks how long metrics are kept (7 days up to 1 year). Changing it only records the desired value — Prometheus applies it on the host with `assetatlas-cli apply-retention`, and until then the panel shows a **Pending** notice.
