# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for IntegrationRepository insight methods.

Covers: get_stale_assets_count, get_attribute_coverage,
get_attribute_conflicts, get_category_coverage, get_asset_timeline.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.modules.integrations.repositories.integration_repository import (
    IntegrationRepository,
)


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def repo():
    """Create repository with mocked connection."""
    with patch.object(
        IntegrationRepository, '__init__', lambda self, **kw: None
    ):
        r = IntegrationRepository()
        r._connection_manager = MagicMock()
    return r


def _mock_cursor(repo, rows, *, fetchone=False):
    """
    Wire up repo._get_connection so the cursor returns *rows*.

    Args:
        repo: The patched IntegrationRepository instance
        rows: Data to return from fetchone or fetchall
        fetchone: If True, use fetchone (single row); else fetchall (list)

    Returns:
        The mocked cursor for assertion introspection.
    """
    cur = MagicMock()
    if fetchone:
        cur.fetchone.return_value = rows
    else:
        cur.fetchall.return_value = rows

    # cursor context manager
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cur

    # connection context manager via _get_connection
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)

    repo._connection_manager = MagicMock()
    repo._connection_manager.get_connection.return_value = conn

    return cur


# ── CORE_FIELDS constant ──────────────────────────────────────────────

class TestCoreFieldsConstant:
    """Verify CORE_FIELDS is defined correctly."""

    def test_core_fields_defined(self):
        assert hasattr(IntegrationRepository, 'CORE_FIELDS')

    def test_core_fields_has_seven_entries(self):
        assert len(IntegrationRepository.CORE_FIELDS) == 7

    def test_core_fields_contents(self):
        expected = [
            'hostname', 'ip', 'mac', 'sn',
            'os', 'manufacturer', 'model',
        ]
        assert IntegrationRepository.CORE_FIELDS == expected


# ── Method 1: get_stale_assets_count ──────────────────────────────────

class TestGetStaleAssetsCount:
    """Tests for get_stale_assets_count."""

    def test_returns_counts(self, repo):
        _mock_cursor(repo, {'stale_count': 5, 'total_count': 20},
                     fetchone=True)
        result = repo.get_stale_assets_count('jamf')
        assert result == {'stale_count': 5, 'total_count': 20}

    def test_defaults_when_no_row(self, repo):
        _mock_cursor(repo, None, fetchone=True)
        result = repo.get_stale_assets_count('jamf')
        assert result == {'stale_count': 0, 'total_count': 0}

    def test_custom_threshold(self, repo):
        cur = _mock_cursor(repo, {'stale_count': 2, 'total_count': 10},
                           fetchone=True)
        repo.get_stale_assets_count('vcenter', threshold_days=60)
        args = cur.execute.call_args[0][1]
        assert args[0] == 60
        assert args[1] == 'vcenter'

    def test_default_threshold_is_30(self, repo):
        cur = _mock_cursor(repo, {'stale_count': 0, 'total_count': 0},
                           fetchone=True)
        repo.get_stale_assets_count('crowdstrike')
        args = cur.execute.call_args[0][1]
        assert args[0] == 30

    def test_zero_counts(self, repo):
        _mock_cursor(repo, {'stale_count': 0, 'total_count': 0},
                     fetchone=True)
        result = repo.get_stale_assets_count('jamf')
        assert result['stale_count'] == 0
        assert result['total_count'] == 0

    def test_all_stale(self, repo):
        _mock_cursor(repo, {'stale_count': 100, 'total_count': 100},
                     fetchone=True)
        result = repo.get_stale_assets_count('old_source')
        assert result['stale_count'] == result['total_count']


# ── Method 2: get_attribute_coverage ──────────────────────────────────

class TestGetAttributeCoverage:
    """Tests for get_attribute_coverage."""

    def _make_coverage_row(self, source_total=100, global_total=500,
                           **overrides):
        """Build a coverage result row with sensible defaults."""
        row = {
            'source_total': source_total,
            'global_total': global_total,
        }
        for field in IntegrationRepository.CORE_FIELDS:
            row[f'source_{field}'] = overrides.get(
                f'source_{field}', source_total
            )
            row[f'global_{field}'] = overrides.get(
                f'global_{field}', global_total
            )
        return row

    def test_full_coverage(self, repo):
        _mock_cursor(repo, self._make_coverage_row(), fetchone=True)
        result = repo.get_attribute_coverage('jamf')
        for field in IntegrationRepository.CORE_FIELDS:
            assert result[field]['source_coverage'] == 100.0
            assert result[field]['global_coverage'] == 100.0
            assert result[field]['gap'] == 0.0

    def test_partial_coverage(self, repo):
        row = self._make_coverage_row(
            source_total=100, global_total=200,
            source_hostname=50, global_hostname=150,
        )
        _mock_cursor(repo, row, fetchone=True)
        result = repo.get_attribute_coverage('jamf')
        assert result['hostname']['source_coverage'] == 50.0
        assert result['hostname']['global_coverage'] == 75.0
        assert result['hostname']['gap'] == 25.0

    def test_empty_result(self, repo):
        _mock_cursor(repo, None, fetchone=True)
        result = repo.get_attribute_coverage('nonexistent')
        assert result == {}

    def test_zero_totals(self, repo):
        row = self._make_coverage_row(source_total=0, global_total=0)
        # zero-out all field counts too
        for field in IntegrationRepository.CORE_FIELDS:
            row[f'source_{field}'] = 0
            row[f'global_{field}'] = 0
        _mock_cursor(repo, row, fetchone=True)
        result = repo.get_attribute_coverage('empty')
        for field in IntegrationRepository.CORE_FIELDS:
            assert result[field]['source_coverage'] == 0.0
            assert result[field]['global_coverage'] == 0.0
            assert result[field]['gap'] == 0.0

    def test_returns_all_seven_fields(self, repo):
        _mock_cursor(repo, self._make_coverage_row(), fetchone=True)
        result = repo.get_attribute_coverage('jamf')
        assert set(result.keys()) == set(
            IntegrationRepository.CORE_FIELDS
        )

    def test_gap_calculation(self, repo):
        """Gap = global_coverage - source_coverage."""
        row = self._make_coverage_row(
            source_total=100, global_total=200,
            source_os=80, global_os=180,
        )
        _mock_cursor(repo, row, fetchone=True)
        result = repo.get_attribute_coverage('jamf')
        assert result['os']['source_coverage'] == 80.0
        assert result['os']['global_coverage'] == 90.0
        assert result['os']['gap'] == 10.0


# ── Method 3: get_attribute_conflicts ─────────────────────────────────

class TestGetAttributeConflicts:
    """Tests for get_attribute_conflicts."""

    # os excluded (raw vs normalized always differs)
    CONFLICT_FIELDS = [
        f for f in IntegrationRepository.CORE_FIELDS if f != 'os'
    ]

    def _make_conflict_row(self, **overrides):
        """Build a conflict result row (os excluded)."""
        row = {field: 0 for field in self.CONFLICT_FIELDS}
        row.update(overrides)
        return row

    def test_no_conflicts(self, repo):
        _mock_cursor(repo, self._make_conflict_row(), fetchone=True)
        result = repo.get_attribute_conflicts('jamf')
        assert result['total_conflicts'] == 0
        assert result['by_field'] == {}

    def test_some_conflicts(self, repo):
        row = self._make_conflict_row(hostname=3, model=2)
        _mock_cursor(repo, row, fetchone=True)
        result = repo.get_attribute_conflicts('vcenter')
        assert result['total_conflicts'] == 5
        assert result['by_field']['hostname'] == 3
        assert result['by_field']['model'] == 2
        assert 'os' not in result['by_field']

    def test_no_row_returns_empty(self, repo):
        _mock_cursor(repo, None, fetchone=True)
        result = repo.get_attribute_conflicts('nonexistent')
        assert result == {'total_conflicts': 0, 'by_field': {}}

    def test_only_nonzero_fields_in_by_field(self, repo):
        row = self._make_conflict_row(ip=5)
        _mock_cursor(repo, row, fetchone=True)
        result = repo.get_attribute_conflicts('jamf')
        assert set(result['by_field'].keys()) == {'ip'}
        assert 'os' not in result['by_field']

    def test_passes_integration_id_to_query(self, repo):
        cur = _mock_cursor(repo, self._make_conflict_row(),
                           fetchone=True)
        repo.get_attribute_conflicts('crowdstrike')
        args = cur.execute.call_args[0][1]
        assert args[0] == 'crowdstrike'


# ── Method 4: get_category_coverage ───────────────────────────────────

class TestGetCategoryCoverage:
    """Tests for get_category_coverage."""

    def test_returns_all_reported_categories(self, repo):
        rows = [
            {'category': 'Server', 'source_count': 80,
             'total_count': 100},
            {'category': 'Desktop', 'source_count': 10,
             'total_count': 100},
        ]
        _mock_cursor(repo, rows)
        result = repo.get_category_coverage('jamf')
        # Repo returns every reported category; share filtering is the
        # caller's job.
        assert len(result) == 2
        by_cat = {r['category']: r for r in result}
        assert by_cat['Server']['coverage_pct'] == 80.0
        assert by_cat['Server']['estimated_missing'] == 20
        assert by_cat['Desktop']['coverage_pct'] == 10.0
        assert by_cat['Desktop']['estimated_missing'] == 90

    def test_empty_when_no_rows(self, repo):
        _mock_cursor(repo, [])
        result = repo.get_category_coverage('nonexistent')
        assert result == []

    def test_full_coverage_category(self, repo):
        rows = [
            {'category': 'Printer', 'source_count': 50,
             'total_count': 50},
        ]
        _mock_cursor(repo, rows)
        result = repo.get_category_coverage('jamf')
        assert len(result) == 1
        assert result[0]['coverage_pct'] == 100.0
        assert result[0]['estimated_missing'] == 0

    def test_zero_total_count_skipped(self, repo):
        rows = [
            {'category': 'Ghost', 'source_count': 0,
             'total_count': 0},
        ]
        _mock_cursor(repo, rows)
        result = repo.get_category_coverage('jamf')
        assert result == []

    def test_passes_integration_id_twice(self, repo):
        """Query uses integration_id in both CASE and HAVING."""
        cur = _mock_cursor(repo, [])
        repo.get_category_coverage('vcenter')
        args = cur.execute.call_args[0][1]
        assert args == ('vcenter', 'vcenter')


# ── Method 5: get_confidence_distribution ────────────────────────────

class TestGetConfidenceDistribution:
    """Tests for get_confidence_distribution (per-source confidence buckets)."""

    def test_returns_buckets(self, repo):
        _mock_cursor(
            repo,
            {'high': 7, 'medium': 2, 'low': 1},
            fetchone=True,
        )
        result = repo.get_confidence_distribution('jamf')
        assert result == {'high': 7, 'medium': 2, 'low': 1}

    def test_defaults_when_no_row(self, repo):
        _mock_cursor(repo, None, fetchone=True)
        result = repo.get_confidence_distribution('nonexistent')
        assert result == {'high': 0, 'medium': 0, 'low': 0}

    def test_handles_null_counts(self, repo):
        # Postgres COUNT(*) FILTER never returns NULL, but defensively
        # ensure dict access yields ints.
        _mock_cursor(
            repo,
            {'high': None, 'medium': None, 'low': None},
            fetchone=True,
        )
        result = repo.get_confidence_distribution('empty')
        assert result == {'high': 0, 'medium': 0, 'low': 0}

    def test_passes_data_source_to_query(self, repo):
        cur = _mock_cursor(
            repo,
            {'high': 0, 'medium': 0, 'low': 0},
            fetchone=True,
        )
        repo.get_confidence_distribution('crowdstrike')
        args = cur.execute.call_args[0][1]
        assert args['data_source'] == 'crowdstrike'


# ── Method 6: get_asset_timeline ──────────────────────────────────────


def _mock_two_query_cursor(repo, series_rows, state_rows):
    """Wire repo._get_connection for the two-query timeline flow.

    get_asset_timeline issues the date-series query first, then the
    per-asset chronicle-state query on the same cursor, so fetchall must
    yield different rows per call.
    """
    cur = MagicMock()
    cur.fetchall.side_effect = [series_rows, state_rows]
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)

    repo._connection_manager = MagicMock()
    repo._connection_manager.get_connection.return_value = conn
    return cur


class TestGetAssetTimeline:
    """Tests for get_asset_timeline (chronicle + net-distinct classifier).

    The timeline mirrors the dashboard's classification but scoped to one
    source: New = asset_created, Returned = asset_readded, Departed =
    asset_removed. Classification itself is covered by
    tests/unit/dashboard/test_timeline_classifier.py; these tests cover the
    repository's query assembly, source scoping, and result shaping.
    """

    def test_returns_added_returned_removed_per_day(self, repo):
        """End-to-end assembly: series + chronicle state → 3 series/day."""
        from datetime import date
        series = [
            {'label': 'Jun 10', 'day': date(2026, 6, 10)},
            {'label': 'Jun 11', 'day': date(2026, 6, 11)},
        ]
        state = [
            # a: created in-window on Jun 10 → added
            {'asset_id': 'a', 'day': date(2026, 6, 10),
             'end_enter': True, 'last_evt': 'asset_created'},
            # b: present before the window (baseline), departs Jun 11 → removed
            {'asset_id': 'b', 'day': date(2026, 6, 9),
             'end_enter': True, 'last_evt': 'asset_created'},
            {'asset_id': 'b', 'day': date(2026, 6, 11),
             'end_enter': False, 'last_evt': 'asset_removed'},
            # c: re-added Jun 11 → returned (not added)
            {'asset_id': 'c', 'day': date(2026, 6, 11),
             'end_enter': True, 'last_evt': 'asset_readded'},
        ]
        _mock_two_query_cursor(repo, series, state)
        result = repo.get_asset_timeline('jamf', days=2)
        assert result == [
            {'label': 'Jun 10', 'added': 1, 'returned': 0, 'removed': 0},
            {'label': 'Jun 11', 'added': 0, 'returned': 1, 'removed': 1},
        ]

    def test_empty_series_returns_empty_without_state_query(self, repo):
        """No date series → short-circuit; the state query never runs."""
        cur = _mock_two_query_cursor(repo, [], [])
        result = repo.get_asset_timeline('nonexistent')
        assert result == []
        assert cur.execute.call_count == 1

    def test_days_with_no_events_are_zero_filled(self, repo):
        from datetime import date
        series = [{'label': 'Jun 10', 'day': date(2026, 6, 10)}]
        _mock_two_query_cursor(repo, series, [])
        result = repo.get_asset_timeline('jamf', days=1)
        assert result == [
            {'label': 'Jun 10', 'added': 0, 'returned': 0, 'removed': 0},
        ]

    def test_scopes_membership_to_current_union_history(self, repo):
        """Departed assets lose their live provider row, so the query must
        union asset_providers with asset_providers_history to attribute
        removals to the source — the core of #914."""
        from datetime import date
        cur = _mock_two_query_cursor(
            repo, [{'label': 'Jun 10', 'day': date(2026, 6, 10)}], [])
        repo.get_asset_timeline('jamf', days=1)
        state_sql = cur.execute.call_args_list[1][0][0]
        assert 'asset_providers_history' in state_sql
        assert 'asset_providers ' in state_sql  # the live table, not only history
        assert 'UNION' in state_sql.upper()

    def test_excludes_pipeline_merge_from_removals(self, repo):
        """Identity consolidations (pipeline_merge) are not real departures."""
        from datetime import date
        cur = _mock_two_query_cursor(
            repo, [{'label': 'Jun 10', 'day': date(2026, 6, 10)}], [])
        repo.get_asset_timeline('jamf', days=1)
        state_sql = cur.execute.call_args_list[1][0][0]
        assert 'pipeline_merge' in state_sql

    def test_default_days_is_30(self, repo):
        cur = _mock_two_query_cursor(repo, [], [])
        repo.get_asset_timeline('jamf')
        assert cur.execute.call_args_list[0][0][1] == {'days': 30}

    def test_custom_days_and_source_passed_to_state_query(self, repo):
        from datetime import date
        cur = _mock_two_query_cursor(
            repo, [{'label': 'Jun 10', 'day': date(2026, 6, 10)}], [])
        repo.get_asset_timeline('crowdstrike', days=7)
        assert cur.execute.call_args_list[0][0][1] == {'days': 7}
        assert cur.execute.call_args_list[1][0][1] == {
            'ds': 'crowdstrike', 'days': 7}

    def test_rows_converted_to_dicts(self, repo):
        """RealDictRow-like objects from either query become plain dicts."""
        from datetime import date

        def _rd(mapping):
            row = MagicMock()
            row.keys = MagicMock(return_value=list(mapping.keys()))
            row.__iter__ = MagicMock(return_value=iter(mapping.items()))
            row.__getitem__ = lambda self, k, m=mapping: m[k]
            return row

        series = [_rd({'label': 'Jun 10', 'day': date(2026, 6, 10)})]
        state = [_rd({'asset_id': 'a', 'day': date(2026, 6, 10),
                      'end_enter': True, 'last_evt': 'asset_created'})]
        _mock_two_query_cursor(repo, series, state)
        result = repo.get_asset_timeline('jamf', days=1)
        assert result == [
            {'label': 'Jun 10', 'added': 1, 'returned': 0, 'removed': 0},
        ]
        assert isinstance(result[0], dict)

    def test_db_error_degrades_to_empty(self, repo):
        """A query failure returns an empty timeline (the butterfly shows
        'No timeline data available') rather than propagating a 500."""
        cur = _mock_two_query_cursor(repo, [], [])
        cur.execute.side_effect = Exception("boom")
        assert repo.get_asset_timeline('jamf', days=1) == []
