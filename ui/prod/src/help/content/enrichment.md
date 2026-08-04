# Enrichment Datasets

An enrichment dataset is a CSV you upload to add your own fields to assets — for example, mapping a hostname or IP to a location, owner, or business unit. AssetAtlas matches each CSV row against assets and writes the extra column into the matched asset's enrichment data.

## How to upload a dataset

Open **Settings → Data → Enrichment** and click **Upload CSV**:

- **Dataset Name** — a label for this dataset (required).
- **CSV File** — drag in or browse for a `.csv` file. It must have at least 2 columns. The first two columns are pre-selected as the match and enrich columns.
- **Match Column (CSV)** — the CSV column whose values are looked up against assets.
- **Enrich Column (CSV)** — the CSV column whose value is written onto matched assets.
- **Match Asset Field** — the asset field to match the match column against (e.g. Hostname, IP Address, MAC Address). Search the field picker and select one.
- **Match Type** — **Exact** by default. **CIDR** is only selectable when the match field is **IP Address**; it matches asset IPs that fall inside the CIDR ranges in the match column.
- **Target Field Name** — the field name added to each matching asset. Auto-derived from the enrich column (lowercased, spaces to underscores); editable.
- Click **Upload & Preview**. The CSV is stored and a preview of the parsed rows is shown. Then click **Apply Now** to match it against assets, or **Close** to apply later from the table.

## Managing datasets

The table lists every dataset with per-row actions:

- **Status toggle** — switches the dataset between *active* and *inactive*.
- **Apply** — matches the dataset against the current assets now and reports how many matched. It's a point-in-time match — assets added or changed later aren't enriched until you apply again.
- **Delete** — removes the dataset after a confirmation prompt. This cannot be undone.

## Key fields

- **Match Field** — the asset field rows are matched on. Extended (data-source-specific) fields show as *source → attribute*.
- **Type** — *exact* or *cidr*, shown as a badge.
- **Target Field** — the enrichment field written onto matched assets.
- **Rows** — number of rows stored from the CSV.
- **Matched** — assets matched by the most recent apply; `—` until the dataset has been applied.
- **Status** — *active* or *inactive*, controlled by the toggle.
