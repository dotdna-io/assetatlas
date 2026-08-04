# Rules Overview

Rules let AssetAtlas act on your inventory automatically — tagging or classifying assets, checking them against compliance criteria, and raising alerts when conditions you define are met. The **Rules** page lists every rule and is where you create and manage them.

## What's on the page

The page is split into two sections:

- **Custom Rules** — the rules you build yourself: *compliance* rules (evaluate assets and tag them pass/fail) and *classification* rules (apply a labelled, coloured tag). Each is shown as a card with its tags and the assets matched / passed / failed at its last run.
- **AssetAtlas Rules** — starts with a **Posture** sub-section (posture rules that score assets pass / fail / N/A — see *Posture Rules*), followed by alert rules grouped by type (*Security*, *Compliance*, *Operational*, *Data Quality*). These sub-sections are collapsible; *Posture* and *Security* are expanded by default and the rest start collapsed. Within each alert group, built-in rules are listed before ones you added. Alert rules produce alerts rather than tags. Some of the built-in ones are **anomaly-detection** rules that use machine learning to flag unusual patterns in your fleet, rather than checking a fixed condition you set; a few of these let you adjust their **sensitivity** — how readily they raise an alert.

A single **Create New Rule** button (top right) opens the rule wizard for all three rule types — see *Creating a Rule*.

## How rules relate to the rest of AssetAtlas

- **Classification / compliance rules** write tags onto assets on the next pipeline run — not the moment you save. Those tags are then usable everywhere assets are filtered (Explore, reports, other rules). See *Tag Rules*.
- **Alert rules** raise entries on the **Alerts** page when their target assets meet the rule's criteria or threshold. You can also pivot from an alert back to its assets in **Explore**: filter on the **Alert** field (matched by the alert's title) to list the assets a currently-active alert fired on.
- **Posture rules** live in the **AssetAtlas Rules** section on this page — see *Posture Rules*.

## Finding a rule

- **Search** — filters both sections by name and description.
- **Severity** — filters alert rules by severity. Selecting any severity hides the Custom Rules and Posture sections (neither carries a severity).
- **Type** — *Posture* shows only the Posture section; *Security / Operational / Data Quality* show alert rules only; *Classification* shows custom rules only; *Compliance* shows both custom compliance rules and Compliance alert rules.
- **Show disabled rules** — off by default, so disabled rules are hidden — a rule you disable disappears from the list entirely until you turn this on. The choice is remembered across visits.
