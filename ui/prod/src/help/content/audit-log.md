# Audit Log

The **Audit Log** is a read-only record of API activity — who called what, when, and with what result. It lives under **Settings → Access → Audit Log** and, like the rest of Settings, is admin-only.

## What's on the page

- **Filters** — **Username**, **Method** (GET/POST/PUT/PATCH/DELETE, or All), **Path**, and **Status Code**, plus a time range (**24h / 7d / 30d / Custom**, default 24h). The text filters apply when you click **Search** or press Enter; the method and time range apply immediately.
- **Table** — Timestamp, User, Method, Path, Status, IP, and Duration, 50 rows per page. Status, method, and duration are colour-coded. Click a row to expand its **Request Body** and **Response Summary**.

By default, GET (read) requests aren't recorded — an admin turns on *Log GET requests in the audit trail* on the **Retention** tab — so filtering by Method **GET** usually returns nothing until that's enabled. Older entries are cleared on the schedule set on the Retention tab.
