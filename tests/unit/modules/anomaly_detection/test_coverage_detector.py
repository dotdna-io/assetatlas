# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime
from unittest.mock import MagicMock

from src.modules.anomaly_detection.domain import RunEvalContext
from src.modules.anomaly_detection.settings import AnomalySettings
from src.modules.anomaly_detection.detectors.coverage import (
    SecuritySourceLossDetector,
    UnprotectedNewcomerDetector,
)

D1_PARAMS = {'z_medium': 3, 'min_count': 5, 'window_days': 90,
             'min_samples': 8, 'severity': 'high'}


def _settings(categories=None, names=None):
    return AnomalySettings(
        security_source_categories=categories or ['endpoint security'],
        security_source_names=names or [],
        segment_min_population=10, segment_homogeneity_pct=90, aggregation_threshold=10,
        segment_coverage_pct=80,
        eligible_categories_for_coverage=['Server', 'Workstation', 'Laptop'])


def _ctx(repo, settings=None, params=None):
    return RunEvalContext(
        generation_run_id='grid-1', run_started_at=datetime(2026, 6, 1),
        current_stats={}, rule={'slug': 'x'}, params=params or D1_PARAMS,
        settings=settings or _settings(), repo=repo, current_batches=[])


def _loss_events(source, n, category='Workstation'):
    return [{'asset_id': f'{source}-{i}', 'ds_change': {'removed': [source]},
             'category': category, 'current_sources': ['AD']} for i in range(n)]


def _quiet_history(count=30, per_run=2):
    return [{'loss_count': per_run} for _ in range(count)]


def test_d1_uninstall_sweep_fires_aggregate():
    repo = MagicMock()
    repo.get_source_category_map.return_value = {
        'SentinelOne': 'endpoint security', 'ServiceNow': 'cmdb'}
    repo.get_datasource_loss_events.return_value = _loss_events('SentinelOne', 30)
    repo.get_datasource_loss_history.return_value = [
        {'loss_count': 2 + (i % 3)} for i in range(30)]
    f = SecuritySourceLossDetector().evaluate(_ctx(repo))
    assert len(f) == 1
    assert f[0].dimension_key == 'SentinelOne' and f[0].kind == 'event'
    assert f[0].severity == 'high' and f[0].alert_type == 'security'
    assert 'SentinelOne' in f[0].title and '30' in f[0].title
    assert f[0].evidence['observed'] == 30
    assert len(f[0].asset_ids) <= 25
    assert f[0].data_sources == ['SentinelOne']


def test_d1_below_floor_quiet():
    repo = MagicMock()
    repo.get_source_category_map.return_value = {'SentinelOne': 'endpoint security'}
    repo.get_datasource_loss_events.return_value = _loss_events('SentinelOne', 3)
    repo.get_datasource_loss_history.return_value = _quiet_history(per_run=1)
    assert SecuritySourceLossDetector().evaluate(_ctx(repo)) == []


def test_d1_non_security_source_loss_quiet():
    repo = MagicMock()
    repo.get_source_category_map.return_value = {
        'ServiceNow': 'cmdb', 'SentinelOne': 'endpoint security'}
    repo.get_datasource_loss_events.return_value = _loss_events('ServiceNow', 40)
    repo.get_datasource_loss_history.return_value = _quiet_history()
    assert SecuritySourceLossDetector().evaluate(_ctx(repo)) == []


def test_d1_explicit_name_list_counts():
    repo = MagicMock()
    repo.get_source_category_map.return_value = {}
    repo.get_datasource_loss_events.return_value = _loss_events('Custom EDR', 20)
    repo.get_datasource_loss_history.return_value = _quiet_history()
    settings = _settings(names=['Custom EDR'])
    f = SecuritySourceLossDetector().evaluate(_ctx(repo, settings=settings))
    assert len(f) == 1 and f[0].dimension_key == 'Custom EDR'


def test_d1_groups_by_source():
    repo = MagicMock()
    repo.get_source_category_map.return_value = {
        'SentinelOne': 'endpoint security', 'CrowdStrike': 'endpoint security'}
    repo.get_datasource_loss_events.return_value = (
        _loss_events('SentinelOne', 15) + _loss_events('CrowdStrike', 12))
    repo.get_datasource_loss_history.return_value = _quiet_history()
    f = SecuritySourceLossDetector().evaluate(_ctx(repo))
    assert sorted(x.dimension_key for x in f) == ['CrowdStrike', 'SentinelOne']


def test_d1_learning_when_short_history():
    repo = MagicMock()
    repo.get_source_category_map.return_value = {'SentinelOne': 'endpoint security'}
    repo.get_datasource_loss_events.return_value = _loss_events('SentinelOne', 30)
    repo.get_datasource_loss_history.return_value = _quiet_history(count=4)  # < min_samples
    assert SecuritySourceLossDetector().evaluate(_ctx(repo)) == []


# --------------------------- D2 UnprotectedNewcomer ---------------------------

D2_PARAMS = {'settle_min_days': 2, 'settle_max_days': 7, 'severity': 'high'}


def _newcomer(asset_id, category='Workstation', ip=None, data_source=None):
    return {'asset_id': asset_id, 'category': category, 'ip': ip or ['10.1.2.50'],
            'data_source': data_source or ['AD'], 'first_seen': datetime(2026, 5, 28)}


def _cov(segment, eligible_n, covered_n):
    return {'segment': segment, 'eligible_n': eligible_n, 'covered_n': covered_n}


def _d2_repo(newcomers, coverage):
    repo = MagicMock()
    repo.get_source_category_map.return_value = {'SentinelOne': 'endpoint security'}
    repo.get_settle_window_assets.return_value = newcomers
    repo.get_segment_coverage.return_value = coverage
    return repo


def test_d2_fires_in_covered_segment():
    repo = _d2_repo([_newcomer('n1')], [_cov('10.1.2.0/24', 50, 48)])
    f = UnprotectedNewcomerDetector().evaluate(_ctx(repo, params=D2_PARAMS))
    assert len(f) == 1 and f[0].kind == 'condition'
    assert f[0].dimension_key == 'n1' and f[0].severity == 'high'
    assert f[0].alert_type == 'security'
    ev = f[0].evidence
    assert ev['segment'] == '10.1.2.0/24' and ev['segment_covered'] == 48
    assert ev['segment_coverage_pct'] == 96.0
    assert ev['first_seen'] == '2026-05-28T00:00:00'      # serialized, not a datetime
    assert 'n1' in f[0].asset_ids
    # coverage is measured over peers established before the settle window
    assert repo.get_segment_coverage.call_args.args[4] == 7   # settle_max threaded


def test_d2_asset_with_security_source_quiet_and_resolves():
    # Already protected -> no finding. This is also the resolve path: an asset
    # that gained a security source stops being emitted, so the service resolves
    # its condition alert.
    repo = _d2_repo([_newcomer('n1', data_source=['AD', 'SentinelOne'])],
                    [_cov('10.1.2.0/24', 50, 48)])
    assert UnprotectedNewcomerDetector().evaluate(_ctx(repo, params=D2_PARAMS)) == []


def test_d2_ineligible_category_quiet():
    repo = _d2_repo([_newcomer('n1', category='Printer')], [_cov('10.1.2.0/24', 50, 48)])
    assert UnprotectedNewcomerDetector().evaluate(_ctx(repo, params=D2_PARAMS)) == []


def test_d2_segment_below_coverage_threshold_quiet():
    repo = _d2_repo([_newcomer('n1')], [_cov('10.1.2.0/24', 50, 30)])   # 60% < 80%
    assert UnprotectedNewcomerDetector().evaluate(_ctx(repo, params=D2_PARAMS)) == []


def test_d2_segment_below_min_population_quiet():
    repo = _d2_repo([_newcomer('n1')], [_cov('10.1.2.0/24', 5, 5)])     # 100% but 5 peers
    assert UnprotectedNewcomerDetector().evaluate(_ctx(repo, params=D2_PARAMS)) == []


def test_d2_no_security_set_quiet():
    # Nothing classifiable as security tooling -> can't assess coverage -> silent.
    repo = _d2_repo([_newcomer('n1')], [_cov('10.1.2.0/24', 50, 48)])
    repo.get_source_category_map.return_value = {}
    assert UnprotectedNewcomerDetector().evaluate(_ctx(repo, params=D2_PARAMS)) == []


def test_d2_flood_collapses_to_one_aggregate():
    # 14 unprotected newcomers in one covered /24 -> above aggregation_threshold
    # (10) -> ONE aggregate condition, not 14 cards (the subnet is the story).
    newcomers = [_newcomer(f'n{i:02d}', ip=[f'10.1.2.{i + 10}']) for i in range(14)]
    repo = _d2_repo(newcomers, [_cov('10.1.2.0/24', 60, 55)])
    f = UnprotectedNewcomerDetector().evaluate(_ctx(repo, params=D2_PARAMS))
    assert len(f) == 1
    agg = f[0]
    assert agg.dimension_key == 'aggregate' and agg.kind == 'condition'
    assert agg.evidence['aggregated'] is True and agg.evidence['count'] == 14
    assert '10.1.2.0/24' in agg.evidence['segments']
    assert len(agg.asset_ids) <= 25


def test_d2_lowercase_category_is_eligible():
    # Real assets.category values are lowercase ('server') while
    # eligible_categories_for_coverage is stored capitalized ('Server') -- the
    # comparison must be case-insensitive or every real asset gets excluded.
    repo = _d2_repo([_newcomer('n1', category='server')], [_cov('10.1.2.0/24', 50, 48)])
    f = UnprotectedNewcomerDetector().evaluate(_ctx(repo, params=D2_PARAMS))
    assert len(f) == 1 and f[0].kind == 'condition'
    assert f[0].dimension_key == 'n1'
    # eligible categories passed into get_segment_coverage must be lowered too,
    # so the SQL's LOWER(a.category) = ANY(%(eligible)s) actually matches.
    assert repo.get_segment_coverage.call_args.args[1] == ['laptop', 'server', 'workstation']


# --------------------------- de-jargon copy scan (Task 5b) ---------------------------

def test_d_family_copy_is_plain_language():
    from src.modules.anomaly_detection.domain import ASSET_LABEL_TOKEN
    # D1 security_source_loss: z= stripped from the narrative, story kept
    repo = MagicMock()
    repo.get_source_category_map.return_value = {'SentinelOne': 'endpoint security'}
    repo.get_datasource_loss_events.return_value = _loss_events('SentinelOne', 30)
    repo.get_datasource_loss_history.return_value = [
        {'loss_count': 2 + (i % 3)} for i in range(30)]
    d1 = SecuritySourceLossDetector().evaluate(_ctx(repo))[0]
    blob = '\n'.join([d1.title, d1.description, d1.evidence.get('narrative', '')])
    for token in ('z=', 'MAD', 'percentile', '#802', 'Proxmox'):
        assert token not in blob
    assert 'agent uninstall' in d1.evidence['narrative']      # plain story retained

    # D2 unprotected_newcomer: asset id tokenized (service resolves it), no raw id
    repo2 = _d2_repo([_newcomer('n1')], [_cov('10.1.2.0/24', 50, 48)])
    d2 = UnprotectedNewcomerDetector().evaluate(_ctx(repo2, params=D2_PARAMS))[0]
    assert ASSET_LABEL_TOKEN in d2.evidence['narrative']
    assert 'n1' not in d2.evidence['narrative']       # raw id replaced by the token
    assert 'n1' not in d2.description
