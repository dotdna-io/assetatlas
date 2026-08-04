# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for field schema extraction from raw payloads."""

from src.modules.integrations.services.field_schema import compute_field_schema


class TestComputeFieldSchema:
    """compute_field_schema() extracts paths, types, samples, and coverage."""

    def test_flat_object(self):
        records = [
            {"data": {"hostname": "srv01", "ip": "10.0.0.1"}},
            {"data": {"hostname": "srv02", "ip": "10.0.0.2"}},
        ]
        schema = compute_field_schema(records)
        paths = {f["path"] for f in schema}
        assert "hostname" in paths
        assert "ip" in paths
        for field in schema:
            assert field["coverage"] == 100.0

    def test_nested_object(self):
        records = [
            {"data": {"computer": {"general": {"name": "mac01"}}}},
        ]
        schema = compute_field_schema(records)
        paths = {f["path"] for f in schema}
        assert "computer.general.name" in paths

    def test_coverage_calculation(self):
        records = [
            {"data": {"hostname": "srv01", "os": "Linux"}},
            {"data": {"hostname": "srv02"}},
        ]
        schema = compute_field_schema(records)
        by_path = {f["path"]: f for f in schema}
        assert by_path["hostname"]["coverage"] == 100.0
        assert by_path["os"]["coverage"] == 50.0

    def test_type_detection(self):
        records = [
            {"data": {"name": "srv01", "count": 5, "active": True, "tags": ["a", "b"]}},
        ]
        schema = compute_field_schema(records)
        by_path = {f["path"]: f for f in schema}
        assert by_path["name"]["type"] == "string"
        assert by_path["count"]["type"] == "number"
        assert by_path["active"]["type"] == "boolean"
        assert by_path["tags"]["type"] == "array"

    def test_sample_value_included(self):
        records = [
            {"data": {"hostname": "srv01"}},
        ]
        schema = compute_field_schema(records)
        assert schema[0]["sample"] == "srv01"

    def test_empty_records(self):
        assert compute_field_schema([]) == []

    def test_array_values_not_recursed(self):
        """Arrays are reported as type 'array', not recursed into."""
        records = [
            {"data": {"ips": ["10.0.0.1", "10.0.0.2"]}},
        ]
        schema = compute_field_schema(records)
        paths = {f["path"] for f in schema}
        assert "ips" in paths
        assert len(schema) == 1

    def test_sorted_by_coverage_desc(self):
        records = [
            {"data": {"a": 1, "b": 2, "c": 3}},
            {"data": {"a": 1}},
        ]
        schema = compute_field_schema(records)
        coverages = [f["coverage"] for f in schema]
        assert coverages == sorted(coverages, reverse=True)

    def test_null_values_not_counted(self):
        records = [
            {"data": {"hostname": "srv01"}},
            {"data": {"hostname": None}},
        ]
        schema = compute_field_schema(records)
        by_path = {f["path"]: f for f in schema}
        assert by_path["hostname"]["coverage"] == 50.0

    def test_max_depth_limit(self):
        """Deeply nested objects stop at reasonable depth."""
        deep = {"a": {"b": {"c": {"d": {"e": {"f": "deep"}}}}}}
        records = [{"data": deep}]
        schema = compute_field_schema(records)
        paths = {f["path"] for f in schema}
        # Should flatten but not go infinitely deep
        assert len(paths) > 0
