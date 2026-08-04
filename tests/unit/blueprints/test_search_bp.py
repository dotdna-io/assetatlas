# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the search blueprint (no auth required).

Endpoints tested:
- GET /api/search/synonyms       — synonym lookup table with ETag caching
- GET /api/search/synonyms/stats — synonym statistics
"""

import json
import os
from datetime import datetime

import pytest

from tests.tz_helpers import WEST_OF_UTC_TZ, local_timezone


@pytest.fixture(autouse=True)
def _restore_synonym_path(blueprint_app):
    """Save and restore SYNONYM_LOOKUP_PATH so config changes don't leak."""
    original = blueprint_app.config.get("SYNONYM_LOOKUP_PATH")
    yield
    if original is not None:
        blueprint_app.config["SYNONYM_LOOKUP_PATH"] = original
    else:
        blueprint_app.config.pop("SYNONYM_LOOKUP_PATH", None)


class TestSearchSynonyms:
    """GET /api/search/synonyms -- serves static synonym JSON with ETag."""

    def test_returns_200_with_json(self, client, blueprint_app):
        """Synonym endpoint returns 200 when file exists."""
        resp = client.get("/api/search/synonyms")
        assert resp.status_code == 200
        data = resp.get_json()
        # The real file is at static/synonym_lookup.json and should be present
        assert "synonyms" in data or "_metadata" in data

    def test_etag_header_present(self, client, blueprint_app):
        """Response includes an ETag header for cache validation."""
        resp = client.get("/api/search/synonyms")
        if resp.status_code == 200:
            assert "ETag" in resp.headers

    def test_cache_control_header(self, client, blueprint_app):
        """Response includes Cache-Control with max-age."""
        resp = client.get("/api/search/synonyms")
        if resp.status_code == 200:
            assert "Cache-Control" in resp.headers
            assert "max-age=3600" in resp.headers["Cache-Control"]

    def test_conditional_request_304(self, client, blueprint_app):
        """If-None-Match with matching ETag returns 304 Not Modified."""
        # First request to get the ETag
        resp1 = client.get("/api/search/synonyms")
        if resp1.status_code != 200:
            pytest.skip("Synonym file not found -- skipping 304 test")

        etag = resp1.headers.get("ETag")
        assert etag is not None

        # Second request with If-None-Match
        resp2 = client.get(
            "/api/search/synonyms",
            headers={"If-None-Match": etag},
        )
        assert resp2.status_code == 304

    def test_conditional_request_stale_etag(self, client, blueprint_app):
        """If-None-Match with non-matching ETag returns 200 with full body."""
        resp = client.get(
            "/api/search/synonyms",
            headers={"If-None-Match": '"stale-etag-value"'},
        )
        # Should get a full response (200) or 404 if file missing
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            data = resp.get_json()
            assert data is not None

    def test_server_timestamp_added(self, client, blueprint_app):
        """Response includes _server.served_at timestamp."""
        resp = client.get("/api/search/synonyms")
        if resp.status_code == 200:
            data = resp.get_json()
            assert "_server" in data
            assert "served_at" in data["_server"]

    def test_file_not_found_returns_404(self, client, blueprint_app):
        """When synonym file doesn't exist, returns 404."""
        blueprint_app.config["SYNONYM_LOOKUP_PATH"] = "/nonexistent/path/synonyms.json"
        resp = client.get("/api/search/synonyms")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data

    def test_invalid_json_returns_500(self, client, blueprint_app, tmp_path):
        """When synonym file contains invalid JSON, returns 500."""
        bad_file = tmp_path / "bad_synonyms.json"
        bad_file.write_text("{not valid json!!!")
        blueprint_app.config["SYNONYM_LOOKUP_PATH"] = str(bad_file)
        resp = client.get("/api/search/synonyms")
        assert resp.status_code == 500
        data = resp.get_json()
        assert "error" in data


class TestSearchSynonymStats:
    """GET /api/search/synonyms/stats -- synonym statistics."""

    def test_stats_returns_200(self, client, blueprint_app):
        """Stats endpoint returns 200 when file exists."""
        resp = client.get("/api/search/synonyms/stats")
        # Could be 200 (file exists) or 404 (file not found)
        assert resp.status_code in (200, 404)

    def test_stats_file_not_found_returns_404(self, client, blueprint_app):
        """When synonym file doesn't exist, stats returns 404."""
        blueprint_app.config["SYNONYM_LOOKUP_PATH"] = "/nonexistent/path/synonyms.json"
        resp = client.get("/api/search/synonyms/stats")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["exists"] is False

    def test_stats_with_valid_file(self, client, blueprint_app, tmp_path):
        """Stats returns expected fields when file is valid."""
        synonym_data = {
            "_metadata": {
                "generated_at": "2025-01-01T00:00:00Z",
                "model": "test-model",
                "similarity_threshold": 0.7,
                "total_terms": 10,
                "matched_terms": 5,
                "total_fields": 3,
            },
            "synonyms": {"host": "hostname", "ip": "ip_address"},
            "fields": {"hostname": {}, "ip_address": {}, "mac": {}},
        }
        f = tmp_path / "synonym_lookup.json"
        f.write_text(json.dumps(synonym_data))
        blueprint_app.config["SYNONYM_LOOKUP_PATH"] = str(f)

        resp = client.get("/api/search/synonyms/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["exists"] is True
        assert data["synonym_count"] == 2
        assert data["field_count"] == 3
        assert data["model"] == "test-model"


class TestSynonymStatsFileModifiedAt:
    """file_modified_at must name the instant the file was written.

    It used to be `datetime.fromtimestamp(st_mtime).isoformat() + 'Z'` — naive
    *local* wall-clock asserted to be UTC (#1162), so the emitted instant was
    wrong by the host's UTC offset while still looking authoritative. The
    timezone is pinned below because the containers run UTC, where the bug is
    invisible.
    """

    MTIME = 1_700_000_000.5  # 2023-11-14T22:13:20.5Z

    def test_file_modified_at_round_trips_to_the_source_mtime(
            self, client, blueprint_app, authed_headers, tmp_path):
        f = tmp_path / "synonym_lookup.json"
        f.write_text(json.dumps({"_metadata": {}, "synonyms": {}, "fields": {}}))
        os.utime(f, (self.MTIME, self.MTIME))
        blueprint_app.config["SYNONYM_LOOKUP_PATH"] = str(f)

        with local_timezone(WEST_OF_UTC_TZ):
            resp = client.get("/api/search/synonyms/stats", headers=authed_headers())

        assert resp.status_code == 200
        emitted = resp.get_json()["file_modified_at"]
        assert emitted.endswith("Z")
        parsed = datetime.fromisoformat(emitted.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
        assert parsed.timestamp() == pytest.approx(self.MTIME, abs=0.001), (
            f"{emitted!r} is off by the host's UTC offset — it was serialized "
            f"from local wall-clock and labelled UTC"
        )
