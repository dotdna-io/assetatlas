# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for SentinelOneSiemConnector._parse_query_aggregation

Verifies that all Scalyr PowerQuery group-by syntaxes are correctly
parsed so that batched time-window re-aggregation preserves all rows.
"""

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def connector():
    """Create a SentinelOneSiemConnector with minimal config (no real API calls)."""
    with patch(
        'src.modules.integrations.connectors.sentinelone_siem_connector.APIKeyAuth'
    ):
        from src.modules.integrations.connectors.sentinelone_siem_connector import (
            SentinelOneSiemConnector,
        )
        return SentinelOneSiemConnector({
            'url': 'https://xdr.eu1.sentinelone.net',
            'api_token': 'fake-token',
            'powerquery': "source='scalyr' | limit 1",
        })


class TestParseQueryAggregation:
    """Test _parse_query_aggregation for various Scalyr PowerQuery syntaxes."""

    # ------------------------------------------------------------------
    # Pattern 1: "| group by field1, field2"
    # ------------------------------------------------------------------
    def test_group_by_simple(self, connector):
        query = "source='scalyr' | group by hostname, ip"
        info = connector._parse_query_aggregation(query)
        assert info['group_by_fields'] == ['hostname', 'ip']

    # ------------------------------------------------------------------
    # Pattern 2: "| group field1, field2 compute ..."
    # ------------------------------------------------------------------
    def test_group_compute(self, connector):
        query = "source='scalyr' | group hostname, ip compute total=count()"
        info = connector._parse_query_aggregation(query)
        assert info['group_by_fields'] == ['hostname', 'ip']
        assert any(a['alias'] == 'total' and a['function'] == 'count'
                    for a in info['aggregations'])

    # ------------------------------------------------------------------
    # Pattern 3: Scalyr "| group <aggs> by field1, field2"
    # This is the syntax that was broken — the root cause of the bug.
    # ------------------------------------------------------------------
    def test_scalyr_group_aggs_by_fields(self, connector):
        """The exact query from the bug report."""
        query = (
            "dataSource.name='Windows Event Logs' && EventID=4624"
            "| parse \"Network Address:..$ip\" from message"
            "| filter wkname != \"-\" && ip = * && ip != \"127.0.0.1\""
            "| group count(),last_time=max(timestamp) by wkname, ip, acctdomain"
            "| columns last_time, wkname, ip, acctdomain"
        )
        info = connector._parse_query_aggregation(query)
        assert set(info['group_by_fields']) == {'wkname', 'ip', 'acctdomain'}
        assert info['has_aggregation'] is True

    def test_scalyr_group_single_agg_by_fields(self, connector):
        query = "source='scalyr' | group count() by hostname"
        info = connector._parse_query_aggregation(query)
        assert info['group_by_fields'] == ['hostname']

    def test_scalyr_group_multiple_aggs_by_fields(self, connector):
        query = (
            "source='scalyr'"
            "| group count(), total=sum(bytes), avg_dur=avg(duration) by host, ip"
        )
        info = connector._parse_query_aggregation(query)
        assert set(info['group_by_fields']) == {'host', 'ip'}
        assert any(a['alias'] == 'total' and a['function'] == 'sum'
                    for a in info['aggregations'])
        assert any(a['alias'] == 'avg_dur' and a['function'] == 'avg'
                    for a in info['aggregations'])

    # ------------------------------------------------------------------
    # Bare count() detection
    # ------------------------------------------------------------------
    def test_bare_count_in_group_clause(self, connector):
        """count() without alias should be detected alongside named aggs."""
        query = "source='scalyr' | group count(), last_time=max(timestamp) by host"
        info = connector._parse_query_aggregation(query)
        assert any(a['alias'] == 'count' and a['function'] == 'count'
                    for a in info['aggregations'])
        assert any(a['alias'] == 'last_time' and a['function'] == 'max'
                    for a in info['aggregations'])

    def test_aliased_count_not_duplicated(self, connector):
        """my_count=count() should not also generate a bare 'count' entry."""
        query = "source='scalyr' | group my_count=count() by host"
        info = connector._parse_query_aggregation(query)
        count_aggs = [a for a in info['aggregations'] if a['function'] == 'count']
        assert len(count_aggs) == 1
        assert count_aggs[0]['alias'] == 'my_count'

    def test_bare_pipe_count(self, connector):
        """Standalone | count should still be detected."""
        query = "source='scalyr' | count"
        info = connector._parse_query_aggregation(query)
        assert any(a['alias'] == 'count' and a['function'] == 'count'
                    for a in info['aggregations'])

    # ------------------------------------------------------------------
    # No aggregation
    # ------------------------------------------------------------------
    def test_no_aggregation(self, connector):
        query = "source='scalyr' | filter host='web01' | limit 100"
        info = connector._parse_query_aggregation(query)
        assert info['has_aggregation'] is False
        assert info['group_by_fields'] == []

    # ------------------------------------------------------------------
    # Regression guard: re-aggregation with group_by_fields
    # ------------------------------------------------------------------
    def test_reaggregate_preserves_rows_with_group_by(self, connector):
        """
        Regression test: with group_by_fields populated, _reaggregate_results
        must keep distinct groups as separate rows — not collapse to 1.
        """
        batch_1 = [
            {'wkname': 'PC-A', 'ip': '10.0.0.1', 'acctdomain': 'CORP', 'count': 5, 'last_time': '2025-01-01T10:00:00'},
            {'wkname': 'PC-B', 'ip': '10.0.0.2', 'acctdomain': 'CORP', 'count': 3, 'last_time': '2025-01-01T09:00:00'},
        ]
        batch_2 = [
            {'wkname': 'PC-A', 'ip': '10.0.0.1', 'acctdomain': 'CORP', 'count': 2, 'last_time': '2025-01-01T18:00:00'},
            {'wkname': 'PC-C', 'ip': '10.0.0.3', 'acctdomain': 'CORP', 'count': 1, 'last_time': '2025-01-01T17:00:00'},
        ]

        agg_info = {
            'has_aggregation': True,
            'group_by_fields': ['wkname', 'ip', 'acctdomain'],
            'aggregations': [
                {'alias': 'count', 'function': 'count', 'field': ''},
                {'alias': 'last_time', 'function': 'max', 'field': 'timestamp'},
            ],
            'needs_count_injection': False,
        }

        results = connector._reaggregate_results([batch_1, batch_2], agg_info)

        # Should have 3 distinct groups (PC-A, PC-B, PC-C), NOT 1
        assert len(results) == 3

        by_name = {r['wkname']: r for r in results}
        assert by_name['PC-A']['count'] == 7   # 5 + 2
        assert by_name['PC-B']['count'] == 3
        assert by_name['PC-C']['count'] == 1
