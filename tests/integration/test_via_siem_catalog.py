# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration tests for the via-SIEM technology catalog (#494).

Verifies that:
  - Migration 093's seed rows are present in the live DB.
  - The merged registry includes DB-defined tiles alongside code connectors.
  - A SIEM-only Integration (no Python connector class) resolves to its
    transport via effective_connector_type, so the runtime ingestion path
    delegates to the existing transport connector.
"""

import os
from uuid import uuid4

import pytest

from src.modules.integrations import discover_connectors
from src.modules.integrations.registry import ConnectorRegistry
from src.modules.integrations.models import Integration, IntegrationStatus
from src.modules.integrations.repositories.technology_repository import (
    IntegrationTechnologyRepository,
)


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def repo(live_app):  # live_app sets DATABASE_URL & boots the app once
    return IntegrationTechnologyRepository()


def test_seed_contains_49_vendor_rows(repo):
    rows = repo.list_technologies()
    assert len(rows) >= 49, f"expected ≥49 seeded rows, got {len(rows)}"
    # A few hand-picked tiles that must exist
    types = {r['integration_type'] for r in rows}
    for required in ('palo-alto-networks', 'cisco', 'tanium', 'fortinet', 'vmware'):
        assert required in types, f"missing seeded tile: {required}"


def test_windows_events_and_custom_tiles_present(repo):
    """#678: 'Windows Events' and 'Custom' must surface as SIEM-only vendor tiles.

    direct_connector=None is exactly what routes a tile into the wizard's
    'Vendor (without direct connection)' group (TypeSelector.tsx). If a future
    change gave either a direct_connector, it would silently move groups — so
    this asserts the property that matters, not just presence.
    """
    rows = {r['integration_type']: r for r in repo.list_technologies()}

    expected = {
        'windows-events': ('Windows Events', '/logos/windows.svg'),
        'custom': ('Custom (via SIEM/datalake)', '/logos/assetatlas.png'),
    }
    for tile_id, (name, logo) in expected.items():
        assert tile_id in rows, f"missing seeded tile: {tile_id}"
        tile = rows[tile_id]
        assert tile['display_name'] == name
        assert tile['logo_url'] == logo
        assert tile['direct_connector'] is None  # SIEM-only -> vendor-via group
        assert tile['connector_role'] == 'technology'
        # Migration 108 omits these and leans on the table defaults; assert the
        # resulting values so a default change or schema-full typo can't drift.
        assert tile['category'] == 'Vendor'
        assert tile['siem_passthrough_supported'] is True
        assert tile['default_transports'] == [
            'splunk', 'log_analytics', 'databricks', 'sentinelone_siem']


def test_registry_merge_exposes_db_tiles(repo):
    discover_connectors()
    merged = ConnectorRegistry.get_all_connectors(tech_repository=repo)
    types = {c['integration_type'] for c in merged}

    # Code connector still present
    assert 'crowdstrike' in types
    # DB-only tile merged in
    assert 'palo-alto-networks' in types

    palo = next(c for c in merged if c['integration_type'] == 'palo-alto-networks')
    assert palo['direct_connector'] is None
    assert palo['connector_role'] == 'technology'
    assert palo['logo_url'] == '/logos/paloalto.png'


def test_logos_resolve_for_seeded_rows(repo):
    missing = repo.warn_missing_logos()
    assert missing == [], f"missing logos for: {missing}"


def test_via_siem_integration_resolves_to_transport():
    """
    A SIEM-only Integration (DB-defined technology) saved with
    source_method='splunk' must resolve to the splunk transport at runtime,
    so existing transport connector code handles ingestion.
    """
    integration = Integration(
        id=uuid4(),
        name='Palo Alto via Splunk (test)',
        integration_type='palo-alto-networks',
        category='Vendor',
        status=IntegrationStatus.DISABLED,
        config={},
        field_mapping={},
        transformations={},
        data_filters=[],
        source_method='splunk',
    )

    assert integration.integration_type == 'palo-alto-networks'
    assert integration.effective_connector_type == 'splunk'


def test_direct_integration_keeps_its_own_type():
    """
    A direct (non-SIEM) integration's effective_connector_type should still
    be its own integration_type — no regression for existing connectors.
    """
    integration = Integration(
        id=uuid4(),
        name='CrowdStrike direct (test)',
        integration_type='crowdstrike',
        category='EDR',
        status=IntegrationStatus.DISABLED,
        config={},
        field_mapping={},
        transformations={},
        data_filters=[],
        source_method='direct',
    )
    assert integration.effective_connector_type == 'crowdstrike'
