# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime
from unittest.mock import MagicMock

from src.modules.anomaly_detection.domain import RunEvalContext
from src.modules.anomaly_detection.settings import AnomalySettings
from src.modules.anomaly_detection.detectors.population import (
    SegmentOutlierDetector,
    FirstSeenValueDetector,
    derive_segment,
)

SETTINGS = AnomalySettings(
    security_source_categories=['endpoint security'], security_source_names=[],
    segment_min_population=10, segment_homogeneity_pct=90, aggregation_threshold=10,
    segment_coverage_pct=80,
    eligible_categories_for_coverage=['Server', 'Workstation', 'Laptop'])


def _created(asset_id, category=None, ip=None, manufacturer=None, os_normalized=None):
    return {'asset_id': asset_id, 'category': category, 'ip': ip or [],
            'manufacturer': manufacturer or [], 'os_normalized': os_normalized,
            'data_source': ['AD'], 'first_seen': datetime(2026, 6, 1)}


def _ctx(repo, profiles=None, params=None, severity='medium'):
    return RunEvalContext(
        generation_run_id='grid-1', run_started_at=datetime(2026, 6, 1),
        current_stats={}, rule={'slug': 'x'}, params=params or {'severity': severity},
        settings=SETTINGS, repo=repo, current_batches=[], profiles=profiles)


# --------------------------- derive_segment ---------------------------

def test_derive_segment_private_ipv4():
    assert derive_segment(['10.1.2.50']) == '10.1.2.0/24'
    assert derive_segment(['192.168.5.9']) == '192.168.5.0/24'


def test_derive_segment_skips_public_and_invalid():
    assert derive_segment(['203.0.113.9']) is None
    assert derive_segment(['not-an-ip', '']) is None
    assert derive_segment([]) is None


def test_derive_segment_first_valid_private_wins():
    assert derive_segment(['203.0.113.9', '10.9.8.7']) == '10.9.8.0/24'


# --------------------------- C1 SegmentOutlier ---------------------------

def test_c1_workstation_in_server_segment_fires():
    repo = MagicMock()
    repo.get_created_assets.return_value = [
        _created('a1', 'Workstation', ['10.1.2.50'], ['Dell'], 'Windows 11')]
    repo.get_segment_profiles.return_value = [
        {'segment': '10.1.2.0/24', 'category': 'Server', 'n': 48},
        {'segment': '10.1.2.0/24', 'category': 'Workstation', 'n': 1}]
    f = SegmentOutlierDetector().evaluate(_ctx(repo))
    assert len(f) == 1
    assert f[0].kind == 'event' and f[0].dimension_key == 'a1'
    assert f[0].severity == 'medium' and f[0].alert_type == 'security'
    assert f[0].evidence['segment'] == '10.1.2.0/24'
    assert f[0].evidence['dominant_category'] == 'Server'


def test_c1_heterogeneous_segment_quiet():
    repo = MagicMock()
    repo.get_created_assets.return_value = [
        _created('a1', 'Workstation', ['10.1.2.50'])]
    repo.get_segment_profiles.return_value = [
        {'segment': '10.1.2.0/24', 'category': 'Server', 'n': 30},
        {'segment': '10.1.2.0/24', 'category': 'Workstation', 'n': 20}]
    assert SegmentOutlierDetector().evaluate(_ctx(repo)) == []


def test_c1_small_segment_quiet():
    repo = MagicMock()
    repo.get_created_assets.return_value = [
        _created('a1', 'Workstation', ['10.1.2.50'])]
    repo.get_segment_profiles.return_value = [
        {'segment': '10.1.2.0/24', 'category': 'Server', 'n': 6}]
    assert SegmentOutlierDetector().evaluate(_ctx(repo)) == []


def test_c1_no_valid_private_ip_skipped():
    repo = MagicMock()
    repo.get_created_assets.return_value = [
        _created('a1', 'Workstation', ['203.0.113.9'])]
    assert SegmentOutlierDetector().evaluate(_ctx(repo)) == []
    repo.get_segment_profiles.assert_not_called()


def test_c1_matching_category_quiet():
    repo = MagicMock()
    repo.get_created_assets.return_value = [
        _created('a1', 'Server', ['10.1.2.50'])]
    repo.get_segment_profiles.return_value = [
        {'segment': '10.1.2.0/24', 'category': 'Server', 'n': 48},
        {'segment': '10.1.2.0/24', 'category': 'Workstation', 'n': 1}]
    assert SegmentOutlierDetector().evaluate(_ctx(repo)) == []


def test_c1_segment_outlier_stays_asset_subject():
    # Control for Task 2: per-asset detectors must keep the default subject_type
    # ('asset') so their alerts still render the "Involved Assets" grid — only the
    # volume.py count/rate detectors are tagged 'metric'.
    repo = MagicMock()
    repo.get_created_assets.return_value = [
        _created('a1', 'Workstation', ['10.1.2.50'], ['Dell'], 'Windows 11')]
    repo.get_segment_profiles.return_value = [
        {'segment': '10.1.2.0/24', 'category': 'Server', 'n': 48},
        {'segment': '10.1.2.0/24', 'category': 'Workstation', 'n': 1}]
    f = SegmentOutlierDetector().evaluate(_ctx(repo))
    assert f and all(x.subject_type == 'asset' for x in f)


# --------------------------- C2 FirstSeenValue ---------------------------

def _profiles(known_map):
    p = MagicMock()
    p.known_values.side_effect = lambda kind: set(known_map.get(kind, set()))
    return p


def test_c2_new_manufacturer_fires_once_per_value_and_upserts():
    repo = MagicMock()
    repo.get_created_assets.return_value = [
        _created('a1', manufacturer=['WeirdCorp'], os_normalized='Windows 11'),
        _created('a2', manufacturer=['weirdcorp '], os_normalized='Windows 11')]
    profiles = _profiles({'fleet_manufacturer': {'dell', 'hp'},
                          'fleet_os': {'windows 11'}})
    f = FirstSeenValueDetector().evaluate(_ctx(repo, profiles, severity='low'))
    mfr = [x for x in f if x.dimension_key == 'fleet_manufacturer:weirdcorp']
    assert len(mfr) == 1
    assert set(mfr[0].asset_ids) == {'a1', 'a2'} and mfr[0].severity == 'low'
    assert mfr[0].kind == 'event' and mfr[0].alert_type == 'security'
    # os 'windows 11' is already known -> no os finding
    assert [x for x in f if x.dimension_key.startswith('fleet_os:')] == []
    profiles.bootstrap_if_empty.assert_called_once()
    # both kinds get upserted (also for already-known values)
    assert profiles.upsert_values.call_count == 2


def test_c2_known_value_quiet_but_still_upserted():
    repo = MagicMock()
    repo.get_created_assets.return_value = [
        _created('a1', manufacturer=['Dell'], os_normalized='Windows 11')]
    profiles = _profiles({'fleet_manufacturer': {'dell'}, 'fleet_os': {'windows 11'}})
    f = FirstSeenValueDetector().evaluate(_ctx(repo, profiles, severity='low'))
    assert f == []
    assert profiles.upsert_values.call_count == 2


# --------------------------- de-jargon copy scan (Task 5b) ---------------------------

def test_c_family_copy_is_plain_and_uuid_free():
    repo = MagicMock()
    repo.get_created_assets.return_value = [
        _created('a1', 'Workstation', ['10.1.2.50'], ['Dell'], 'Windows 11')]
    repo.get_segment_profiles.return_value = [
        {'segment': '10.1.2.0/24', 'category': 'Server', 'n': 48},
        {'segment': '10.1.2.0/24', 'category': 'Workstation', 'n': 1}]
    so = SegmentOutlierDetector().evaluate(_ctx(repo))[0]
    blob = '\n'.join([so.title, so.description, so.evidence.get('narrative', '')])
    for token in ('z=', 'MAD', 'percentile', '#802', 'Proxmox'):
        assert token not in blob
    assert 'a1' not in so.title            # no raw asset id in the title
    assert 'a1' not in (so.evidence.get('narrative') or '')
