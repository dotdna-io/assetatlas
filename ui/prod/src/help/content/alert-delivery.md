# Alert Delivery

**Alert Delivery** controls where alerts go beyond the in-app Alerts view. It has two tabs under **Settings → Alert Delivery**: **Channels** (the destinations) and **Routing** (which alerts reach which destination). Both are admin-only.

## Channels

A channel is a delivery destination and its settings. Use **Add channel** to create one; each has a **Name**, a **Type** (fixed once created), and type-specific settings:

- **Webhook** — a URL, timeout, optional signing key, and optional custom headers (a flat JSON object of string values). Incoming-webhook destinations such as Teams or Slack are wired up here.
- **Email** — one or more recipient addresses. Delivery needs SMTP configured under **Settings → System → Email**; the form warns if it isn't.
- **Syslog** — host, port, protocol (UDP/TCP), and facility.

Per channel you can **Test** (sends a real test message and reports success or failure), **Edit**, or **Delete**. New channels are enabled on creation; disable one from its **Edit** dialog. A channel's **Type** is fixed once created — delete and recreate to switch — and a saved secret such as a webhook signing key shows as *saved — leave blank to keep*, so you only retype it to change it.

## Routing

Routing decides, per **rule category** — **Security**, **Compliance**, **Operational**, **Data Quality**, and **Custom** — which channels receive alerts. Each category is its own section:

- **AssetAtlas (in-app)** is always on for every category and can't be removed — the in-app Alerts view receives everything.
- Add channels to a category and set each one's **minimum severity**; the channel then receives alerts of that category at or above the chosen severity.
- **Save** is per category — edits to one category don't affect the others.

A channel only delivers if it's enabled on the **Channels** tab (and, for email, SMTP is configured); routing an alert to a disabled channel won't force it through.
