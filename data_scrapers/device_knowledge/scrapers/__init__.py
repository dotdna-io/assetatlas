# SPDX-License-Identifier: AGPL-3.0-or-later
from .nmap_os_classes import scrape as scrape_nmap
from .netbox_devicetypes import scrape as scrape_netbox
from .cpe_dictionary import scrape as scrape_cpe
from .fingerbank_devices import scrape as scrape_fingerbank
from .ieee_oui import scrape as scrape_ieee_oui
from .fusioninventory_snmp import scrape as scrape_fusioninventory
from .hwdata_vendors import scrape as scrape_hwdata
from .recog_fingerprints import scrape as scrape_recog

ALL_SCRAPERS = {
    "nmap": scrape_nmap,
    "netbox": scrape_netbox,
    "cpe": scrape_cpe,
    "fingerbank": scrape_fingerbank,
    "ieee_oui": scrape_ieee_oui,
    "fusioninventory": scrape_fusioninventory,
    "hwdata": scrape_hwdata,
    "recog": scrape_recog,
}
