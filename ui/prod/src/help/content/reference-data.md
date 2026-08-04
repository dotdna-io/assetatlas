# Reference Data

Reference data is the built-in lookup data AssetAtlas uses to enrich assets — the MAC vendor database (which manufacturer a device's MAC address belongs to) and the OS end-of-life database. It ships as a versioned bundle that you keep up to date here, in **Settings → Data → Reference Data**.

## How to update reference data

The status card at the top shows the **Current bundle** version and when it was **Last applied**. A fresh install ships with a bundle already applied, so MAC-vendor and OS end-of-life enrichment work out of the box; use this page to move to a newer bundle when one becomes available. There are two ways to update, depending on the install:

- **Check for updates** — for internet-connected installs. AssetAtlas contacts the upstream source and, if a newer bundle exists, shows an *Update available* panel with the upstream version and the datasets it contains. Click **Apply update** to download and apply it, or **Cancel** to dismiss.
- **Upload bundle** — for air-gapped installs. Obtain a bundle tarball (`.tar.gz` / `.tgz`) from an internet-connected machine, then upload it here to apply it directly.

After a check or apply, the result (including per-dataset row counts) is shown as text below the actions.

## Reviewing what's applied

- **Datasets** — the datasets in the current bundle and each one's row count. Empty until a bundle has been applied.
- **Recent applies** — the apply history: when it started, the bundle version, the source (*github* or *upload*), the outcome (*running*, *success*, *failed*), and who initiated it.
- **Re-apply this version** — shown next to a past successful upstream apply that is not the current bundle, to roll back/forward to that version.
