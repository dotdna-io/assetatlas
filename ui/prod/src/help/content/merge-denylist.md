# Merge Denylist

The **Merge Denylist** lists identifiers the deduplication pipeline must **never** use to merge two devices into one asset — things like a reused hostname, a shared or virtual MAC address, or an ambiguous serial. Use it only for identifiers that are genuinely shared across devices: denylisting a device's *only* identifier drops that device from the inventory entirely. It lives under **Settings → Data → Merge Denylist**.

## Adding an entry

- Choose the **identifier type**: **Hostname**, **FQDN**, **MAC**, or **Serial**.
- Choose the **match type**:
  - Hostname / FQDN — **Exact**, **Starts with**, or **Ends with**.
  - MAC — **Exact**, **Starts with**, or **Range** (a start and end address).
  - Serial — **Exact** only.
- Enter the **value** (or the start/end pair for a MAC range) and an optional **reason**.
- The **match preview** shows how much of your fleet the entry would catch (e.g. *Matches 3 of your 5,000 hostnames*) and turns a warning colour when an entry matches a large share of the fleet — a sign it's too broad.
- Click **Add**.

To remove an entry later, use its delete action — on the next pipeline run, devices it had been keeping apart may merge again.
