# Metrics Forwarding

**Metrics forwarding** sends AssetAtlas's own metrics **out** to a Prometheus-compatible endpoint (Grafana, Mimir, Thanos, VictoriaMetrics, …) via remote-write. It's configured under **Settings → Monitoring → Forwarding**.

## Configuring the target

- Tick **Enable metrics forwarding**.
- Under **Remote write target**, set the **Endpoint URL** and pick an **Authentication** mode: **None**, **Bearer token**, or **Basic auth** (username and password). Secrets are write-only — a saved secret shows as `•••••••• (saved — leave blank to keep)`, so leave the field blank to keep the current one.
- Under **External labels**, add name/value pairs attached to every forwarded series (for example `cluster`, `environment`).
- Click **Save**. Use **Test connection** to check the endpoint is reachable — it tests the *saved* endpoint, so save before testing. Setting **Authentication** to *None* clears any stored secret.
