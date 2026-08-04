# Tag Rules

Tag rules are the rules that write tags onto your assets automatically. They are the **Custom Rules** section of the Rules page and come in two kinds: *classification* rules apply one labelled, coloured tag to the assets they match; *compliance* rules evaluate matched assets against criteria and apply a **pass** tag or a **fail** tag accordingly.

## What they do

- A **classification rule** tags every asset matching its target filters with a single label (e.g. `production`) in a colour you choose.
- A **compliance rule** also targets assets with filters, then checks each against its compliance criteria; passing assets get the **Pass Tag**, failing assets get the **Fail Tag**.
- The tags these rules write behave like any other asset attribute — you can filter on them in the Explorer, in reports, and as conditions in other rules.

## Creating one

- On the Rules page click **Create New Rule** and choose **Classification Rule** or **Compliance Rule**. The wizard collects the name, target filters, criteria (compliance only), and the tag(s). See *Creating a Rule* for the full step-by-step. New and edited tag rules take effect on the next pipeline run, not the moment you save.

## Managing tag rules

Each rule is a card in the **Custom Rules** section showing its name, type, tags, and how many assets it matched (and passed / failed, for compliance) at its last run.

- **Edit** (pencil) — expands the card into an inline form. Change the name, description, target filters, tags, and — for compliance — the pass/fail criteria with an **ALL conditions** / **ANY condition** toggle. A live counter previews the new **matched** total; the pass / fail figures stay at their last pipeline-run values until the next run. **Save Changes** to apply.
- **Delete** (trash) — removes the rule after a confirmation prompt. This cannot be undone.
- **Toggle** — the switch enables or disables the rule. A disabled rule keeps its definition but is skipped on future runs, and is hidden unless **Show disabled rules** is on. Disabling doesn't remove tags it already wrote — those stay until the pipeline reprocesses the assets.
- Use the search box and the **Type** filter (*Classification* / *Compliance*) to narrow the list.

## Key fields

- **Assets / pass / fail counts** — the result of the rule's *last* pipeline run, not a live figure. While editing, only the matched-assets count previews live from the current filters; pass / fail stay at the last run's values.
- **Tag colour** — classification rules require a colour; if you don't pick one a default grey is used so the rule can be saved.
