# Data Retention

The **Retention** tab sets how long AssetAtlas keeps its historical data — ingestion batches, asset and provider history, alert firing history, and the audit trails. It lives under **Settings → System → Retention** and is admin-only. Changes take effect without a restart.

## What you can set

Retention is expressed two ways, and for several data types you get both — whichever keeps *more* wins:

- **…retention days** — keep rows from the last N days.
- **…retention count** — keep the latest N regardless of age (for example the latest N batches per integration, or the latest N asset-history snapshots).

The settings cover ingestion batches, excluded records, asset change history and snapshots, provider history, alert events and deliveries, and the audit and auth-audit trails. Defaults are mostly **90 days** (batches 7 days; alert deliveries 30 days), with sensible "latest N" floors. There's also a **Log GET requests in the audit trail** toggle — off by default, because logging reads can grow the audit trail quickly — turn it on if you want read requests to appear in the **Audit Log**.

Edit any value and click **Save**; each row has a reset-to-default control.
