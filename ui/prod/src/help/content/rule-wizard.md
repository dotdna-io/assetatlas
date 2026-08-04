# Creating a Rule

**Create New Rule** on the Rules page opens a step-by-step wizard — an inline panel at the top of the page, where each completed step collapses into a summary column. The steps shown depend on the rule type you pick — the wizard builds the right path for a classification, compliance, or alert rule.

## The steps

- **Type** — choose **Compliance Rule** (evaluate assets against criteria and tag them passing or failing), **Classification Rule** (categorise assets with a labelled, coloured tag based on filter criteria), or **Alert Rule** (monitor a condition or threshold and raise an alert). Picking a type advances the wizard and decides which later steps appear.
- **Name** — set a **Rule Name** (required) and an optional **Description**.
- **Filters / Targets** — under **Target Assets**, add filter rows with **+ Add Filter** to choose which assets the rule applies to. A live counter shows how many assets currently match. (This step is titled *Filters* for classification rules and *Targets* for compliance and alert rules.)
- **Criteria** *(compliance and alert only)* — under **Compliance Criteria**, choose whether **ALL conditions** or **ANY condition** must hold, then add condition rows with **+ Add Condition**. Targeted assets that meet the criteria count as passing. The counter also shows the pass / fail split.
- **Settings** *(alert only)* — set the alert's **Category** and **Severity** (both required), an optional **Threshold** (*falls below* / *exceeds* a value, as a percentage or a count), and an optional **Silence Period** (suppress repeat alerts for 1 hour up to 7 days).
- **Tags** *(classification and compliance only)* — classification rules take a **Tag Label** and **Tag Color**; compliance rules take a **Pass Tag** and a **Fail Tag** applied to assets that pass or fail the criteria.

Move with **Continue** / **Back**. The final step's button reads **Create Rule**. **Cancel** prompts for confirmation and discards your progress. A new rule doesn't take effect immediately — classification and compliance rules apply on the next pipeline run, and the live counter is only a preview.

## Key fields

- **Live match counter** — appears once at least one filter is filled in. It calls a preview after a short pause and shows assets matched, plus pass / fail for compliance and alert rules. `--` means no preview yet; `...` means it's loading.
- **Valueless operators** — filter operators such as *is empty* / *is not empty* take no value and count as complete on their own; every other operator needs a value before **Continue** enables.
