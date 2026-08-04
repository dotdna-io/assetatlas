# Asset Details

**Asset Details** is the full profile of a single unified asset — its identity, network details, posture checks, a breakdown of how its confidence score was reached, and the raw fields each data source contributed. To open it, click the three-dots menu on an asset's row in **Explore** and choose **Asset Details**.

## What it shows

- **Header** — the asset's display name (hostname, falling back to FQDN, IP, MAC, or serial), its category and tags, and the manufacturer and model. On the right: its compliance status (**Compliant**, **Non-Compliant**, or **In Grace** with days remaining), when it was last seen, a **bell** that opens this asset's alerts, and a **clock** that opens its history.
- **General Information** — hostname(s), FQDN, category and subcategory, OS, serial number, and owner / user / location fields. Missing values show as `-`.
- **Network Details** — private and public IP, MAC address and MAC vendor, and geolocation (country / region / city). Geolocation is attributed to DB-IP.
- **Asset Posture** — pass/fail checks appropriate to the asset's category (for example a workstation is checked for endpoint security, domain join, device management, vulnerability scanning, and monitoring), each showing which source satisfied it. Categories with no defined checks show *No posture checks for this category*.
- **Confidence** — the confidence level and score with a short explanation. Expand it for the six factors behind the score: **Sources**, **Quality**, **Completeness** (with the missing fields listed), **Consistency** (conflicts), **Recency**, and **Identity**.
- **Data Sources** — every source that currently contributes to the asset, with its field count and last sync. Select a source to see its raw fields, grouped General / Network / Extended; the *Search fields* box filters them. Sources that reference the asset but aren't active are listed separately under *Other Sources*.
