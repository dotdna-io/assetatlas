# Posture Rules

Posture rules score how well your assets meet security and compliance expectations, organised into **posture categories** (such as patching, encryption, or endpoint protection). Each category holds one or more sub-rules; assets matching a sub-rule's target filters are evaluated against its compliance criteria and marked pass, fail, or N/A. Posture lives on the **Rules** page, in the **Posture** sub-section under *AssetAtlas Rules* — that sub-section is expanded by default, and each category card is collapsed until you open it.

## Categories

Each category is a card showing its name, a **Posture** label, its asset-category icons, and a toggle. Expand a card for its description, its rules, a **New Rule** button, and its coverage.

- **Asset-category icons** — read-only, derived from the target filters of the category's sub-rules (server, workstation, mobile, network, IoT, OT, miscellaneous). They reflect which categories the rules actually target.
- **Category toggle** — derived from the sub-rules: on when any sub-rule is enabled, and flipping it enables or disables every sub-rule in the category at once. It's disabled for a category with no rules.
- **Coverage** — a full-width block below the rules showing, per asset category, how many assets are compliant out of the total in that category. Coverage and pass/fail results update on the next pipeline run, not the moment you save a rule; a category with no rules shows *N/A* for all its assets.

## Sub-rules

A category's **Rules** list contains its sub-rules, each showing its target conditions, its criteria (with ALL / ANY logic), an optional output, and who last edited it.

- **Order matters** — sub-rules are evaluated top-down; use the up / down arrows to reorder. The first sub-rule whose targets match an asset decides that asset's result for the category.
- **New Rule** opens the sub-rule wizard. The pencil edits a sub-rule; the trash deletes it after a confirmation; the switch enables or disables a single sub-rule.

## The sub-rule wizard

Three steps:

- **Name** — **Rule Name** (required), an optional **Description**, and an **Enabled** checkbox (on by default).
- **Targets** — under **Target Assets**, add filter rows to choose which assets this sub-rule scores. A live counter shows how many match.
- **Criteria** — under **Compliance Criteria**, choose **ALL conditions** or **ANY condition** and add condition rows. Optionally set a **Rule Output** — *None*, a **Field value**, or fixed **Text** — recorded alongside the pass/fail result. The preview shows matched assets and the pass / fail split.

Move with **Continue** / **Back**; the last step reads **Create Rule** (or **Save** when editing). **Cancel** confirms before discarding.
