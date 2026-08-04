# Third-Party Notices

AssetAtlas redistributes copies of the third-party components listed below. Each
component remains the property of its respective copyright holder(s) and is
provided under the license stated in its section. This file documents those
vendored components and reproduces — or points to a colocated file containing —
the verbatim upstream license text for every distinct license.

This document is informational. It does not modify any license. Where this
summary and a component's own license text differ, the component's license text
governs.

## Bundled reference data

### Device knowledge base — `data/device_knowledge_base/product_lines.csv`

Rows in this file are derived from the upstream sources named in the `source`
column:

- **Rapid7 Recog** (https://github.com/rapid7/recog) — BSD-2-Clause. Portions of
  the device-classification patterns are derived from the Recog fingerprint
  database. The license notice is reproduced below as required.
- **NetBox Device Type Library** (https://github.com/netbox-community/devicetype-library)
  — CC0-1.0 public-domain dedication (no attribution required; listed for provenance).
- **Nmap OS classes** (https://nmap.org/data/os-classes.txt) — reference for device categories.

```
Copyright (c) Rapid7, Inc.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES ARE DISCLAIMED. IN NO EVENT SHALL THE
COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Third-party trademarks and logos — `ui/prod/public/logos/`

The vendor logos and product names bundled under `ui/prod/public/logos/` are the
trademarks of their respective owners, included solely for nominative use
(identifying the data source each integration connects to). Their inclusion does
not imply endorsement by or affiliation with those vendors.

## Data fetched into the base image at build time

`scripts/prepare-base-data.sh` downloads the following into the base container
image (they are **not** committed to this repository). Redistributors of the
built base image must honor their terms:

- **DB-IP City Lite** (`data/dbip-city-lite.mmdb`, https://db-ip.com/db/lite.php)
  — **CC-BY-4.0**. Attribution required: "IP Geolocation by DB-IP" (https://db-ip.com).
- **BAAI/bge-base-en-v1.5** embedding model (https://huggingface.co/BAAI/bge-base-en-v1.5)
  — **MIT**.
- **IEEE OUI registry** (`data/mac_ranges.csv`, https://standards-oui.ieee.org/)
  — IEEE registration-authority public data.
