# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for SentinelOneSiemConnector.

Inherits 11 standard tests from ConnectorTestCase and adds connector-specific
tests for PowerQuery execution, time-window batching, result normalization
(table format vs. matches format), aggregation re-aggregation, and bearer auth.
"""

import json
import pytest
import responses

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.sentinelone_siem_connector import SentinelOneSiemConnector
from src.modules.integrations.base import APIKeyAuth


BASE_URL = "https://xdr.eu1.sentinelone.net"
POWERQUERY_URL = f"{BASE_URL}/api/powerQuery"


class TestSentinelOneSiemConnector(ConnectorTestCase):
    connector_class = SentinelOneSiemConnector
    mock_config = {
        "url": BASE_URL,
        "api_token": "test-api-token",
        "timespan": 1,  # 1h — fits in single batch (< MAX_BATCH_HOURS=8)
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default single-record PowerQuery response for all tests."""
        mock_http.register_from_fixture("POST", POWERQUERY_URL, "sentinelone_siem/powerquery_single.json")
        # test_connection also hits PowerQuery once more
        mock_http.register_from_fixture("POST", POWERQUERY_URL, "sentinelone_siem/powerquery_single.json")

    # -------------------------------------------------------------------------
    # Connector-specific: auth strategy
    # -------------------------------------------------------------------------

    def test_auth_is_api_key_auth(self):
        """Connector must use APIKeyAuth."""
        connector = SentinelOneSiemConnector(self.mock_config)
        assert isinstance(connector.auth, APIKeyAuth)

    def test_auth_uses_bearer_prefix(self):
        """SIEM connector uses Bearer prefix (not ApiToken)."""
        connector = SentinelOneSiemConnector(self.mock_config)
        assert connector.auth.api_key_prefix == 'Bearer '
        assert connector.auth.api_key_header == 'Authorization'
        assert connector.auth.api_key == 'test-api-token'

    # -------------------------------------------------------------------------
    # Connector-specific: init validation
    # -------------------------------------------------------------------------

    def test_missing_url_raises(self):
        """Missing url raises ValueError."""
        with pytest.raises(ValueError, match="URL is required"):
            SentinelOneSiemConnector({"api_token": "tok"})

    def test_missing_api_token_raises(self):
        """Missing api_token raises ValueError."""
        with pytest.raises(ValueError, match="API token is required"):
            SentinelOneSiemConnector({"url": BASE_URL})

    # -------------------------------------------------------------------------
    # Connector-specific: PowerQuery result normalization
    # -------------------------------------------------------------------------

    @responses.activate
    def test_table_format_normalized_to_dicts(self):
        """columns + values table format is converted to list of dicts."""
        with open(FIXTURES_DIR / "sentinelone_siem" / "powerquery_single.json") as f:
            body = json.load(f)

        responses.add(responses.POST, POWERQUERY_URL, json=body, status=200)

        connector = SentinelOneSiemConnector(self.mock_config)
        records = list(connector.fetch_data("batch-table"))

        assert len(records) == 1
        rec = records[0]
        assert rec["endpoint.name"] == "DESKTOP-WIN001"
        assert rec["endpoint.ip"] == "10.0.1.100"
        assert rec["endpoint.os"] == "Windows 10 Pro"

    @responses.activate
    def test_matches_format_normalized(self):
        """matches[] log format is returned as-is."""
        with open(FIXTURES_DIR / "sentinelone_siem" / "powerquery_matches.json") as f:
            body = json.load(f)

        responses.add(responses.POST, POWERQUERY_URL, json=body, status=200)

        connector = SentinelOneSiemConnector(self.mock_config)
        records = list(connector.fetch_data("batch-matches"))

        assert len(records) == 1
        rec = records[0]
        assert rec["endpoint.name"] == "DESKTOP-WIN001"
        assert rec["event.type"] == "endpoint.status"

    @responses.activate
    def test_multi_record_response(self):
        """Multiple rows in values are all yielded as separate records."""
        with open(FIXTURES_DIR / "sentinelone_siem" / "powerquery_multi.json") as f:
            body = json.load(f)

        responses.add(responses.POST, POWERQUERY_URL, json=body, status=200)

        connector = SentinelOneSiemConnector(self.mock_config)
        records = list(connector.fetch_data("batch-multi"))

        assert len(records) == 3
        names = {r["endpoint.name"] for r in records}
        assert "DESKTOP-WIN001" in names
        assert "LAPTOP-MAC001" in names
        assert "SERVER-LIN001" in names

    @responses.activate
    def test_unknown_response_format_returns_empty(self):
        """Unknown PowerQuery response format yields no records."""
        body = {"status": "success", "someUnknownKey": []}
        responses.add(responses.POST, POWERQUERY_URL, json=body, status=200)

        connector = SentinelOneSiemConnector(self.mock_config)
        records = list(connector.fetch_data("batch-unknown"))
        assert records == []

    # -------------------------------------------------------------------------
    # Connector-specific: error handling
    # -------------------------------------------------------------------------

    @responses.activate
    def test_failed_status_raises(self):
        """PowerQuery response with status != 'success' raises an exception."""
        body = {"status": "error", "message": "Query syntax error"}
        responses.add(responses.POST, POWERQUERY_URL, json=body, status=200)

        connector = SentinelOneSiemConnector(self.mock_config)
        with pytest.raises(Exception, match="PowerQuery execution failed"):
            list(connector.fetch_data("batch-error"))

    @responses.activate
    def test_html_response_raises_descriptive_error(self):
        """HTML response (wrong auth/endpoint) raises a clear error."""
        responses.add(
            responses.POST, POWERQUERY_URL,
            body="<!doctype html><html><body>Unauthorized</body></html>",
            status=200,
            content_type="text/html"
        )

        connector = SentinelOneSiemConnector(self.mock_config)
        with pytest.raises(Exception, match="HTML response"):
            list(connector.fetch_data("batch-html"))

    # -------------------------------------------------------------------------
    # Connector-specific: aggregation parsing
    # -------------------------------------------------------------------------

    def test_parse_aggregation_group_by(self):
        """group by clause is detected correctly."""
        connector = SentinelOneSiemConnector(self.mock_config)
        query = "source='scalyr' | group by endpoint.name"
        info = connector._parse_query_aggregation(query)
        assert info["has_aggregation"] is True
        assert "endpoint.name" in info["group_by_fields"]

    def test_parse_aggregation_count_function(self):
        """count() aggregation is detected."""
        connector = SentinelOneSiemConnector(self.mock_config)
        query = "source='scalyr' | group count() by endpoint.name"
        info = connector._parse_query_aggregation(query)
        assert info["has_aggregation"] is True
        assert any(a["function"] == "count" for a in info["aggregations"])

    def test_parse_aggregation_no_group_by(self):
        """Query without group by has no aggregation."""
        connector = SentinelOneSiemConnector(self.mock_config)
        query = "source='scalyr' | limit 100"
        info = connector._parse_query_aggregation(query)
        assert info["has_aggregation"] is False
        assert info["group_by_fields"] == []

    def test_parse_aggregation_avg_needs_count_injection(self):
        """avg() without count() sets needs_count_injection."""
        connector = SentinelOneSiemConnector(self.mock_config)
        query = "source='scalyr' | group avg_val=avg(responseTime) by endpoint.name"
        info = connector._parse_query_aggregation(query)
        assert info["needs_count_injection"] is True

    def test_parse_aggregation_avg_with_count_no_injection(self):
        """avg() with count() does not need injection."""
        connector = SentinelOneSiemConnector(self.mock_config)
        query = "source='scalyr' | group cnt=count(), avg_val=avg(responseTime) by endpoint.name"
        info = connector._parse_query_aggregation(query)
        assert info["needs_count_injection"] is False

    # -------------------------------------------------------------------------
    # Connector-specific: re-aggregation
    # -------------------------------------------------------------------------

    def test_reaggregate_count_sums_across_batches(self):
        """count aggregation is summed across time-window batches."""
        connector = SentinelOneSiemConnector(self.mock_config)

        batch1 = [{"endpoint.name": "PC1", "count": 10}]
        batch2 = [{"endpoint.name": "PC1", "count": 5}]

        agg_info = {
            "group_by_fields": ["endpoint.name"],
            "aggregations": [{"alias": "count", "function": "count", "field": ""}]
        }

        results = connector._reaggregate_results([batch1, batch2], agg_info)
        assert len(results) == 1
        assert results[0]["count"] == 15

    def test_reaggregate_min_max(self):
        """min/max aggregations are applied correctly across batches."""
        connector = SentinelOneSiemConnector(self.mock_config)

        batch1 = [{"host": "A", "min_val": 3, "max_val": 7}]
        batch2 = [{"host": "A", "min_val": 1, "max_val": 10}]

        agg_info = {
            "group_by_fields": ["host"],
            "aggregations": [
                {"alias": "min_val", "function": "min", "field": "val"},
                {"alias": "max_val", "function": "max", "field": "val"},
            ]
        }

        results = connector._reaggregate_results([batch1, batch2], agg_info)
        assert len(results) == 1
        assert results[0]["min_val"] == 1
        assert results[0]["max_val"] == 10

    def test_reaggregate_groups_by_key(self):
        """Different group keys produce separate result records."""
        connector = SentinelOneSiemConnector(self.mock_config)

        batch1 = [
            {"host": "A", "count": 5},
            {"host": "B", "count": 3},
        ]
        batch2 = [
            {"host": "A", "count": 2},
            {"host": "B", "count": 4},
        ]

        agg_info = {
            "group_by_fields": ["host"],
            "aggregations": [{"alias": "count", "function": "count", "field": ""}]
        }

        results = connector._reaggregate_results([batch1, batch2], agg_info)
        by_host = {r["host"]: r["count"] for r in results}
        assert by_host["A"] == 7
        assert by_host["B"] == 7

    # -------------------------------------------------------------------------
    # Connector-specific: `| columns` clause stripping (batching only)
    # -------------------------------------------------------------------------

    def test_strip_columns_clause_simple(self):
        """`| columns x, y` is stripped and parsed into (alias, source) pairs."""
        stripped, cols = SentinelOneSiemConnector._strip_columns_clause(
            "source='scalyr' | group count() by host | columns host, total"
        )
        assert cols == [("host", "host"), ("total", "total")]
        assert "| columns" not in stripped

    def test_strip_columns_clause_with_alias(self):
        """`alias=source` renames are parsed into separate alias/source pairs."""
        stripped, cols = SentinelOneSiemConnector._strip_columns_clause(
            "source='scalyr' | group count() by tag.hs.lentity | "
            "columns lentity=tag.hs.lentity, last_time"
        )
        assert cols == [
            ("lentity", "tag.hs.lentity"),
            ("last_time", "last_time"),
        ]
        assert "| columns" not in stripped

    def test_strip_columns_clause_absent(self):
        """Query without `| columns` is returned unchanged."""
        q = "source='scalyr' | group count() by host"
        stripped, cols = SentinelOneSiemConnector._strip_columns_clause(q)
        assert cols is None
        assert stripped == q

    def test_strip_columns_clause_whitespace_tolerance(self):
        """Trailing whitespace and irregular spacing between specs is normalized."""
        stripped, cols = SentinelOneSiemConnector._strip_columns_clause(
            "source='scalyr' | group count() by host | columns  host ,  total   "
        )
        assert cols == [("host", "host"), ("total", "total")]
        # Trailing whitespace on the stripped query is removed
        assert stripped == "source='scalyr' | group count() by host"

    def test_strip_columns_clause_takes_last_when_multiple(self):
        """If multiple `| columns` clauses exist, only the LAST is stripped."""
        stripped, cols = SentinelOneSiemConnector._strip_columns_clause(
            "source='scalyr' | columns a, b, c | group count() by a | columns a"
        )
        assert cols == [("a", "a")]
        # Earlier `| columns a, b, c` survives in the query sent to the API
        assert "| columns a, b, c" in stripped

    def test_apply_columns_projection_renames_and_filters(self):
        """Projection keeps only requested fields and applies aliases."""
        records = [
            {"model": "X", "serial": "S", "tag.hs.lentity": "L", "extra": "drop"},
        ]
        projected = SentinelOneSiemConnector._apply_columns_projection(
            records,
            [("model", "model"), ("lentity", "tag.hs.lentity")],
        )
        assert projected == [{"model": "X", "lentity": "L"}]

    def test_apply_columns_projection_missing_source_yields_none(self):
        """Source missing from a record results in None (not KeyError)."""
        projected = SentinelOneSiemConnector._apply_columns_projection(
            [{"a": 1}],
            [("a", "a"), ("b", "b")],
        )
        assert projected == [{"a": 1, "b": None}]

    @responses.activate
    def test_batched_aggregation_with_columns_projects_correctly(self):
        """
        End-to-end regression: aggregated query with `| columns alias=source`
        and a multi-batch timespan must project to the requested columns —
        not leak group-by/aggregation aliases as phantom null fields.
        """
        query = (
            "dataSource.name contains \"Alto\""
            "| group cnt=count(),last_time=max(timestamp) "
            "by device.hostname, serial, tag.hs.lentity"
            "| columns serial"
        )
        # SentinelOne would only see the STRIPPED query — return all
        # group-by + aggregation columns so re-aggregation can work.
        responses.add(
            responses.POST, POWERQUERY_URL,
            json={
                "status": "success",
                "columns": [
                    {"name": "device.hostname"},
                    {"name": "serial"},
                    {"name": "tag.hs.lentity"},
                    {"name": "cnt"},
                    {"name": "last_time"},
                ],
                "values": [
                    ["host1", "123456789012", "ACME-US", 3, 1700000000],
                ],
            },
            status=200,
        )

        connector = SentinelOneSiemConnector({
            **self.mock_config, "timespan": 24, "powerquery": query,
        })
        records = list(connector.fetch_data("batch-cols"))

        assert len(records) == 1
        data_keys = {k for k in records[0] if not k.startswith("_")}
        assert data_keys == {"serial"}, (
            f"`| columns serial` must yield exactly 'serial', got {data_keys}"
        )
        assert records[0]["serial"] == "123456789012"

    @responses.activate
    def test_batched_aggregation_with_aliased_columns(self):
        """`| columns lentity=tag.hs.lentity` renames must work under batching."""
        query = (
            "dataSource.name='Sophos XGS'"
            "| group cnt=count(),last_time=max(timestamp) "
            "by model, serial, tag.hs.lentity"
            "| columns model, serial, lentity=tag.hs.lentity, last_time"
        )
        responses.add(
            responses.POST, POWERQUERY_URL,
            json={
                "status": "success",
                "columns": [
                    {"name": "model"},
                    {"name": "serial"},
                    {"name": "tag.hs.lentity"},
                    {"name": "cnt"},
                    {"name": "last_time"},
                ],
                "values": [["XGS107", "S1", "ACME-US", 2, 1700000000]],
            },
            status=200,
        )
        connector = SentinelOneSiemConnector({
            **self.mock_config, "timespan": 24, "powerquery": query,
        })
        records = list(connector.fetch_data("batch-cols-alias"))

        assert len(records) == 1
        data_keys = {k for k in records[0] if not k.startswith("_")}
        assert data_keys == {"model", "serial", "lentity", "last_time"}
        assert records[0]["lentity"] == "ACME-US", (
            "Alias 'lentity' must carry value from 'tag.hs.lentity'"
        )

    @responses.activate
    def test_batched_non_aggregated_columns_passed_to_api(self):
        """
        For NON-aggregated queries with `| columns`, the clause must reach
        the API (each batch's response is already projected and we just
        concatenate). Regression guard against stripping in the wrong branch.
        """
        query = (
            "dataSource.name contains \"Alto\""
            "| parse \",$serial{regex=[0-9]{12,15}}$,\" from message"
            "| filter serial = *"
            "| columns serial"
        )
        captured_queries = []

        def capture(request):
            body = json.loads(request.body)
            captured_queries.append(body["query"])
            return (200, {}, json.dumps({
                "status": "success",
                "columns": [{"name": "serial"}],
                "values": [["123456789012"]],
            }))

        responses.add_callback(
            responses.POST, POWERQUERY_URL,
            callback=capture, content_type="application/json",
        )

        connector = SentinelOneSiemConnector({
            **self.mock_config, "timespan": 24, "powerquery": query,
        })
        records = list(connector.fetch_data("batch-no-agg-cols"))

        assert len(captured_queries) == 3
        for q in captured_queries:
            assert "| columns serial" in q, (
                "Non-aggregated queries must keep `| columns` for the API to apply"
            )
        assert len(records) == 3
        assert all(r["serial"] == "123456789012" for r in records)

    @responses.activate
    def test_batched_aggregation_merges_across_batches_then_projects(self):
        """
        Distinct batches contribute rows that share the same group key and
        must be re-aggregated correctly BEFORE the local `| columns`
        projection drops the aggregation alias from the output.
        """
        query = (
            "dataSource.name='Sophos XGS'"
            "| group cnt=count(),last_time=max(timestamp) "
            "by model, serial, tag.hs.lentity"
            "| columns model, serial, lentity=tag.hs.lentity"
        )

        batch_responses = iter([
            # Batch 1: PC-A=3, PC-B=2
            {
                "status": "success",
                "columns": [
                    {"name": "model"}, {"name": "serial"},
                    {"name": "tag.hs.lentity"}, {"name": "cnt"},
                    {"name": "last_time"},
                ],
                "values": [
                    ["XGS107", "S1", "ACME-US", 3, 1700000000],
                    ["XGS107", "S2", "ACME-US", 2, 1700000000],
                ],
            },
            # Batch 2: PC-A=5 (same key as batch 1 row 1 → must merge)
            {
                "status": "success",
                "columns": [
                    {"name": "model"}, {"name": "serial"},
                    {"name": "tag.hs.lentity"}, {"name": "cnt"},
                    {"name": "last_time"},
                ],
                "values": [
                    ["XGS107", "S1", "ACME-US", 5, 1700000500],
                ],
            },
            # Batch 3: PC-C, new group
            {
                "status": "success",
                "columns": [
                    {"name": "model"}, {"name": "serial"},
                    {"name": "tag.hs.lentity"}, {"name": "cnt"},
                    {"name": "last_time"},
                ],
                "values": [
                    ["XGS135", "S3", "ACME-EU", 1, 1700000700],
                ],
            },
        ])

        def callback(request):
            return (200, {}, json.dumps(next(batch_responses)))

        responses.add_callback(
            responses.POST, POWERQUERY_URL,
            callback=callback, content_type="application/json",
        )

        connector = SentinelOneSiemConnector({
            **self.mock_config, "timespan": 24, "powerquery": query,
        })
        records = list(connector.fetch_data("batch-merge-then-project"))

        # Distinct group keys: (XGS107,S1,ACME-US), (XGS107,S2,ACME-US), (XGS135,S3,ACME-EU)
        # 3 rows after re-aggregation, each projected to {model, serial, lentity}.
        assert len(records) == 3
        for r in records:
            data_keys = {k for k in r if not k.startswith("_")}
            assert data_keys == {"model", "serial", "lentity"}, (
                f"Projection must yield exactly model/serial/lentity, got {data_keys}"
            )
        by_serial = {r["serial"]: r for r in records}
        assert set(by_serial) == {"S1", "S2", "S3"}
        assert by_serial["S1"]["lentity"] == "ACME-US"
        assert by_serial["S3"]["lentity"] == "ACME-EU"

    # -------------------------------------------------------------------------
    # Connector-specific: metadata
    # -------------------------------------------------------------------------

    def test_metadata_source_is_sentinelone_siem(self, connector, mock_http):
        """The _source metadata field must be 'sentinelone_siem'."""
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "sentinelone_siem"

    def test_record_type_is_powerquery_result(self, connector, mock_http):
        """The _record_type field must be 'powerquery_result'."""
        records = list(connector.fetch_data("batch-rectype"))
        assert records[0]["_record_type"] == "powerquery_result"
