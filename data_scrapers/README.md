# Data Scrapers

Utilities that collect reference data from external sources. They fall into two
groups with different lifecycles.

## Base-image build inputs

Run at image-**build** time by `scripts/prepare-base-data.sh`, which writes their
output into `data/` for `Dockerfile.base` to COPY. No database involved.

| Directory | Produces |
|---|---|
| `device_knowledge/` | `data/device_knowledge_base/*.csv` — device categorisation patterns merged from CPE, Fingerbank, FusionInventory SNMP, hwdata, IEEE OUI, NetBox device-types, Nmap OS classes and Recog |
| `geolocation/` | `data/dbip-city-lite.mmdb` — IP-to-city lookup |

`prepare-base-data.sh` caches both against `data/.manifest.json` and re-runs
`device_knowledge` when its tree hash changes; pass `--force` to regenerate
everything. Invoke them directly only when debugging a scraper:

```bash
PYTHONPATH="$PWD" python -m data_scrapers.device_knowledge.run_all \
  --output-dir data/device_knowledge_base
python data_scrapers/geolocation/download_dbip.py
```
