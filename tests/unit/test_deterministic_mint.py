# SPDX-License-Identifier: AGPL-3.0-or-later
"""New asset ids are minted deterministically from the component set (#802).

A single record with >15 hostname-like components (e.g. a company footprint with
many country-domain FQDNs) trips the oversized-group nuke every run. With random
uuid4 minting it got a brand-new id each run, so the prior id went absent and
(under the grace period) accumulated as orphan duplicates. Deriving the id from
the component set makes the re-discovered group resolve to the SAME id every run
-> identical input yields no new asset.
"""

import logging

import pytest

from src.modules.asset_generation.domain.asset_identifier import (
    AssetIdentifier,
    _UUID_PATTERN,
)


@pytest.fixture(autouse=True)
def _quiet_pipeline_logs():
    """Silence the minting chatter for this module's tests only.

    This used to be a module-level ``logging.disable(logging.WARNING)`` — a
    process-wide switch with no off switch, flipped at *import* time. pytest
    imports every collected module before any test runs, so the disable leaked
    into tests collected after this one: tests/unit/api/middleware/
    test_request_id_logging.py drives requests whose INFO lines were then never
    even created, failing 15 tests that pass standalone. Disable-and-restore
    around each test keeps the quiet without poisoning the session.
    """
    logging.disable(logging.WARNING)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)

# 18 country-domain FQDNs (> MAX_HOSTNAMES_PER_GROUP) — like a large real-world
# footprint record that reforms an oversized group every run.
OVERSIZED = [
    f'example.{tld}' for tld in
    ['com', 'co.uk', 'it', 'pt', 'at', 'es', 'fr', 'ie', 'de', 'be',
     'ca', 'nl', 'com.mx', 'co.za', 'mx', 'eu', 'cz', 'pl']
]


def test_mint_is_deterministic_for_same_component_set():
    # Two independent runs of the same brand-new record -> same id.
    id1 = AssetIdentifier().group_components(['alpha-host', 'beta-host'])
    id2 = AssetIdentifier().group_components(['beta-host', 'alpha-host'])  # order-independent
    assert id1 == id2


def test_distinct_component_sets_get_distinct_ids():
    id1 = AssetIdentifier().group_components(['alpha-host'])
    id2 = AssetIdentifier().group_components(['beta-host'])
    assert id1 != id2


def test_minted_id_is_uuid_format():
    aid = AssetIdentifier().group_components(['some-host'])
    assert _UUID_PATTERN.match(aid)


def test_oversized_record_keeps_stable_id_across_nuke_cycles():
    """The reproduced #802 churn: a >15-component record was re-minted a new id
    every run because the oversized-group nuke wiped its mappings each load.
    Deterministic minting makes the re-discovered group resolve to the same id.
    """
    old = 'aaaa0000-bbbb-cccc-dddd-eeeeeeeeeeee'
    store = {c: old for c in OVERSIZED}  # over-merged: all components -> one old id

    def run():
        ai = AssetIdentifier()
        ai.load_existing_mappings(dict(store))   # nukes the oversized group
        aid = ai.group_components(OVERSIZED)     # re-mint (no surviving mapping)
        store.update(ai.get_all_mappings())      # persist (upsert), like end of run
        return aid

    r1, r2, r3 = run(), run(), run()
    assert r1 == r2 == r3        # stable across runs (was: a new uuid every run)
    assert r1 != old             # converged to the deterministic id
