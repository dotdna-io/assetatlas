# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Layer 1: Deterministic synonym rules.

Four strategies (in priority order):
A) Core field synonyms — complete, curated synonym lists for the 20 static
   AssetAtlas fields. These are authoritative and never overridden.
B) Explicit overrides — hardcoded term → field_name for known LLM mismatches
   on extended fields.
C) Abbreviation expansion — IT abbreviations → expanded forms,
   then substring-matched against field descriptions.
D) Substring match — literal term found in field description.

All matching is case-insensitive. Results include confidence scores.
"""

from dataclasses import dataclass


@dataclass
class RuleMatch:
    """A deterministic synonym match."""
    term: str
    field_name: str
    confidence: float    # 1.0 = core/override/abbreviation, 0.95 = substring
    strategy: str        # "core", "override", "abbreviation", or "substring"


# ---------------------------------------------------------------------------
# Core field synonyms — complete, curated lists for the 20 static fields.
# These are the authoritative synonym mappings for AssetAtlas's own fields.
# Format: field_name → list of search terms (all lowercase).
# The LLM is NOT called for these fields — these lists are the source of truth.
# ---------------------------------------------------------------------------
CORE_FIELD_SYNONYMS: dict[str, list[str]] = {
    "hostname": [
        "hostname", "host name", "computer name", "machine name", "device name",
        "server name", "workstation name", "pc name", "node name", "system name",
        "netbios name", "netbios", "dns short name", "endpoint name", "host",
        "node", "asset name", "workstation", "computer",
    ],
    "ip": [
        "ip", "ip address", "ip addr", "network address", "ipv4", "ipv6",
        "ipv4 address", "ipv6 address", "internet protocol", "network ip",
        "device ip", "host ip", "layer 3 address", "logical address",
        "network addr", "address",
    ],
    "mac": [
        "mac", "mac address", "mac addr", "hardware address", "physical address",
        "ethernet address", "nic address", "layer 2 address", "data link address",
        "burnt-in address", "network interface", "network card address",
    ],
    "sn": [
        "sn", "serial number", "serial", "service tag", "asset tag",
        "hardware id", "chassis serial", "product serial", "device serial",
        "s/n", "serial no",
    ],
    "fqdn": [
        "fqdn", "fully qualified domain name", "full hostname", "full dns name",
        "complete hostname", "full domain name", "dns name", "dns record",
        "fully qualified", "hostname with domain", "complete name",
    ],
    "category": [
        "category", "asset type", "device type", "classification", "type",
        "device class", "asset class", "hardware type", "device category",
        "server", "laptop", "desktop", "virtual machine", "vm",
        "network device", "switch", "router", "firewall", "iot", "iot device",
        "printer", "mobile device", "tablet", "phone",
    ],
    "subcategory": [
        "subcategory", "sub category", "sub-category", "subtype", "sub type",
        "specific type", "detailed type", "secondary category", "device subtype",
    ],
    "os_normalized": [
        "os", "operating system", "os version", "os name", "os type", "platform",
        "system software", "windows", "linux", "macos", "mac os", "ubuntu",
        "red hat", "rhel", "centos", "debian", "freebsd", "os family",
        "system type", "os platform",
    ],
    "manufacturer": [
        "manufacturer", "vendor", "make", "brand", "oem", "hardware vendor",
        "hardware manufacturer", "producer", "maker", "company",
        "dell", "hp", "hewlett packard", "cisco", "apple", "lenovo", "vmware",
        "microsoft", "supermicro",
    ],
    "model": [
        "model", "model number", "model name", "product name", "hardware model",
        "device model", "product", "sku", "part number", "form factor",
        "chassis type", "product model",
    ],
    "location": [
        "location", "site", "building", "floor", "room", "rack", "data center",
        "datacenter", "dc", "rack location", "physical location", "where",
        "physical site", "office", "branch", "campus", "facility",
        "deployment location", "geography", "region",
    ],
    "owner": [
        "owner", "assigned to", "user", "responsible", "contact", "department",
        "team", "custodian", "manager", "assignee", "accountable",
        "responsible party", "asset owner", "who owns", "ownership",
        "cost center", "user email",
    ],
    "data_source": [
        "data source", "source", "integration", "provider", "discovered by",
        "origin", "imported from", "source system", "discovery source",
    ],
    "data_source_category": [
        "data source category", "source type", "source category",
        "integration type", "discovery method",
    ],
    "first_seen": [
        "first seen", "first discovered", "discovered", "created", "added",
        "discovery date", "first detected", "initial discovery", "when added",
        "when discovered", "created date", "first found",
    ],
    "last_seen": [
        "last seen", "last active", "last updated", "recent", "latest",
        "last observation", "last check-in", "most recent", "last activity",
        "last timestamp", "last visit",
    ],
    "confidence": [
        "confidence", "confidence score", "score", "quality", "accuracy",
        "certainty", "reliability", "data quality", "reliability score",
        "trust score",
    ],
    "os_eol_status": [
        "eol", "eol status", "end of life", "end of support", "supported",
        "unsupported", "lifecycle", "support status", "os lifecycle",
        "os end of life", "os eol", "os support", "retired", "decommissioned",
        "patch status", "update status",
    ],
    "os_eol_date": [
        "eol date", "end of life date", "end of support date", "os eol date",
        "os end of life date", "support end date", "retirement date",
        "discontinuation date", "sunset date", "expiry date",
    ],
    "os_is_lts": [
        "lts", "long term support", "is lts", "os lts", "os is lts",
        "long term servicing", "ltsc", "lts release", "stable release",
        "extended support",
    ],
    "user_field": [
        "user field", "custom field", "user defined", "user data",
        "custom attribute", "user note", "user label",
    ],
    "ipam_location": [
        "ipam", "ipam location", "ip management", "subnet", "network location",
        "network segment", "ip location", "subnet location",
    ],
    "mac_vendor_enriched": [
        "mac vendor", "nic vendor", "oui", "oui vendor", "oui lookup",
        "network card vendor", "network card manufacturer", "ethernet vendor",
        "hardware vendor lookup", "network interface manufacturer",
    ],
    "ip_private": [
        "private ip", "internal ip", "rfc1918", "private address", "lan ip",
        "internal address", "non-routable ip", "private network",
    ],
    "ip_public": [
        "public ip", "external ip", "internet ip", "globally routable ip",
        "wan ip", "public address", "routable ip", "internet address",
    ],
    "ip_type": [
        "ip type", "ip classification", "address type", "network type",
        "private or public", "ip category",
    ],
    "geo_countries": [
        "country", "geo country", "geolocation country", "ip country",
        "location country", "nation", "territory",
        "which country", "where country",
    ],
    "geo_cities": [
        "city", "geo city", "geolocation city", "ip city", "location city",
        "metro area", "municipality", "town", "which city", "where city",
    ],
    "geo_regions": [
        "geo region", "state", "province", "subdivision", "geolocation region",
        "ip region", "location region", "which state", "which region",
    ],
}

# Build reverse lookup: term → field_name (for fast matching)
_CORE_TERM_TO_FIELD: dict[str, str] = {}
for _field, _terms in CORE_FIELD_SYNONYMS.items():
    for _term in _terms:
        _t = _term.lower().strip()
        if _t not in _CORE_TERM_TO_FIELD:
            _CORE_TERM_TO_FIELD[_t] = _field


# ---------------------------------------------------------------------------
# Explicit overrides for extended fields — fix known LLM mismatches.
# ---------------------------------------------------------------------------
EXPLICIT_OVERRIDES: dict[str, str] = {
    # Security
    "edr": "microsoft_defender_api_av",
    "endpoint detection": "microsoft_defender_api_av",
    "malware": "microsoft_defender_api_av",
    "infected": "sentinelone_detectionState",
    "security agent": "sentinelone_agentVersion",
    "up to date": "sentinelone_isUpToDate",
    "outdated": "sentinelone_isUpToDate",
    "needs update": "sentinelone_isUpToDate",
}


# ---------------------------------------------------------------------------
# Abbreviation expansion for extended fields.
# ---------------------------------------------------------------------------
ABBREVIATION_MAP: dict[str, list[str]] = {
    "av": ["antivirus", "endpoint protection"],
    "edr": ["endpoint detection", "threat detection"],
    "mdm": ["mobile device management", "device management", "device enrollment"],
    "mfa": ["multi-factor authentication", "two-factor"],
    "2fa": ["two-factor authentication", "multi-factor"],
    "tpm": ["trusted platform module"],
    "vpn": ["virtual private network"],
    "sso": ["single sign-on"],
    "fde": ["full disk encryption", "disk encryption"],
    "nic": ["network interface", "network card", "ethernet"],
}

# Minimum term length for substring matching
_MIN_SUBSTRING_LEN = 3


# ---------------------------------------------------------------------------
# Matching functions
# ---------------------------------------------------------------------------

def match_core(term: str) -> list[RuleMatch]:
    """
    Check if term matches a core field synonym.
    Returns at most one match — core synonyms are authoritative.
    """
    normalized = term.lower().strip()
    field_name = _CORE_TERM_TO_FIELD.get(normalized)
    if field_name:
        return [RuleMatch(term=normalized, field_name=field_name,
                          confidence=1.0, strategy="core")]
    return []


def match_overrides(
    term: str,
    field_descriptions: dict[str, str],
) -> list[RuleMatch]:
    """Check explicit term → field_name overrides for extended fields."""
    normalized = term.lower().strip()
    target = EXPLICIT_OVERRIDES.get(normalized)
    if not target:
        return []

    # Direct match
    if target in field_descriptions:
        return [RuleMatch(term=normalized, field_name=target,
                          confidence=1.0, strategy="override")]

    # Try with source prefix variants
    for field_name in field_descriptions:
        if field_name == target or field_name.endswith(f"_{target}"):
            return [RuleMatch(term=normalized, field_name=field_name,
                              confidence=1.0, strategy="override")]

    return []


def match_abbreviations(
    term: str,
    field_descriptions: dict[str, str],
) -> list[RuleMatch]:
    """Expand abbreviation and find fields whose descriptions contain expanded forms."""
    normalized = term.lower().strip()
    expansions = ABBREVIATION_MAP.get(normalized, [])
    if not expansions:
        return []

    matches = []
    for field_name, desc in field_descriptions.items():
        desc_lower = desc.lower()
        for expansion in expansions:
            if expansion in desc_lower:
                matches.append(RuleMatch(
                    term=normalized, field_name=field_name,
                    confidence=1.0, strategy="abbreviation",
                ))
                break

    return matches


def match_substring(
    term: str,
    field_descriptions: dict[str, str],
) -> list[RuleMatch]:
    """Find fields whose description contains the exact term as a substring."""
    normalized = term.lower().strip()
    if len(normalized) < _MIN_SUBSTRING_LEN:
        return []

    matches = []
    for field_name, desc in field_descriptions.items():
        if normalized in desc.lower():
            matches.append(RuleMatch(
                term=normalized, field_name=field_name,
                confidence=0.95, strategy="substring",
            ))

    return matches


def match_rules(
    term: str,
    field_descriptions: dict[str, str],
) -> list[RuleMatch]:
    """
    Run all strategies for a term.

    Priority: core > override > abbreviation > substring.
    Core and override return immediately if matched.
    """
    # Core synonyms are authoritative
    core = match_core(term)
    if core:
        return core

    # Overrides for known LLM mismatches
    override = match_overrides(term, field_descriptions)
    if override:
        return override

    # Abbreviation expansion + substring (merged)
    abbr_matches = match_abbreviations(term, field_descriptions)
    substr_matches = match_substring(term, field_descriptions)

    by_field: dict[str, RuleMatch] = {}
    for m in abbr_matches + substr_matches:
        existing = by_field.get(m.field_name)
        if not existing or m.confidence > existing.confidence:
            by_field[m.field_name] = m

    return list(by_field.values())


def get_all_core_synonyms() -> dict[str, str]:
    """
    Return all core synonyms as a flat term → field_name dict.
    Used by the synonym generator to seed the lookup table without LLM calls.
    """
    return dict(_CORE_TERM_TO_FIELD)
