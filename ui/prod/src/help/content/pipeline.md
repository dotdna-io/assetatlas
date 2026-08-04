# Asset-Generation Pipeline

The pipeline turns the raw records collected from your integrations into consolidated assets — it standardises fields, matches records that describe the same device, merges them, scores confidence, and writes the result to the asset inventory. It runs on a schedule or on demand. Its settings live under **Settings → Data → Pipeline** and **Settings → Data → Confidence**.

**Scheduled runs are off by default.** A new install has no integrations to collect from, so nothing is scheduled until you set it up — once your data sources are connected, turn on **Scheduled Runs** and pick a schedule (see *Scheduling* below) so the inventory builds and stays current on its own. Until you do, the pipeline runs only when you press **Run Now**.

## Running the pipeline

- **Run Now** (top of the Pipeline tab) triggers a run immediately. It's disabled while a run is already in progress.
- **After adding a new integration, re-run the pipeline to see its assets.** Creating an integration collects its records, but those records become inventory assets only when the pipeline processes them — press **Run Now** (or wait for the next scheduled run).
- While a run is active a status strip shows **Running...**, the current **Stage**, and **Elapsed** time. Status refreshes every few seconds during a run.
- A run is tagged **manual** or **schedule** depending on how it was started.

## Scheduling

In the **Pipeline Schedule** section:

- Turn **Scheduled Runs** on or off. This toggle saves immediately; the rest of the schedule saves with **Save**. The interval or cron only runs while **Scheduled Runs** is on — with it off, the pipeline runs only via **Run Now**.
- Pick a mode: **Interval** (run every N hours, chosen from a preset list — 1, 2, 4, 6, 8, 12, 24, 48, 72, or 168) or **Cron** (a preset such as *Every 6 hours* / *Daily at 2 AM*, or **Custom...** for your own cron expression). In Cron mode you also pick a **timezone**.
- **Save** appears only when you've changed the mode/interval/cron/timezone. **Next run** shows when the scheduled run is due, or `--` when scheduling is off.

## Run history and detail

The **Run History** table lists past runs (10 per page) with **Triggered By**, **Started**, **Duration**, **Status**, and asset counts — **Total, Created, Returned, Updated, Removed, Merged**. Click a row to expand its detail:

- A left-to-right flow of counts: **Data Sources → Input Records → Excluded → Updated → Removed → Merged → Created → Returned → Unique Assets** (with **Carried Forward** and **Total** added when the grace period carried assets forward).
- A per-source breakdown (records, share, assets, excluded, success/fail) and an **Error** callout if the run failed.
- **Stage timings** grouped into phases; a phase containing a slow stage is flagged and expanded, and unusually slow stages are marked **slow**.

## Confidence, calibration & reliability

These live on the **Confidence** tab. Threshold changes take effect immediately; calibration and reliability changes apply on the next pipeline run.

- **Confidence Thresholds** — a dual-handle slider setting the Low / Medium / High boundaries. The handles keep a minimum 0.05 gap. **Reset defaults** restores them; **Save** applies.
- **Confidence Calibration** — drag points to adjust how a computed confidence score maps to the score shown on assets. The dashed line means no adjustment; the table lists each point's input, output, and the shift between them.
- **Source Reliability Scores** — how much each data-source category is trusted (higher = more authoritative), grouped into tiers. Drag the bars, then **Save Scores**; **Reset to Defaults** restores the built-in tiering.

## Key fields

- **Mapping Expiration Days** (Pipeline → Configuration) — how long a learned record-to-asset identity mapping survives before it expires. The row shows the **Default**, and the **Min**/**Max** it accepts; it has its own **Save**.
- **Removal grace period (days)** (Pipeline → Configuration) — how many days an absent asset is kept before it's removed; **0** removes it immediately. It has its own **Save**.
- **Triggered By** — *manual* (someone pressed Run Now) or *schedule* (the scheduler).
- **Excluded** (run detail) — records dropped by validation/exclusion lists before they became assets.
- **slow** badge (stage timings) — that stage ran unusually slowly compared with its past runs, not necessarily an error.
