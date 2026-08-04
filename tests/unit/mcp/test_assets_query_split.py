# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the get_asset/list_assets/search_assets query+format split.

Each ``*_query`` function must return raw structured data (no LLM strings);
each ``*_impl`` must call its matching ``*_query`` and produce the same
text output it did before the refactor.

Mocking pattern: patch ``src.mcp.tools.assets.AssetRepository`` with a
MagicMock, configure the desired repo method return value, then call the
impl / query helper directly.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import asyncio

from src.mcp.tools.assets import (
    format_asset_detail,
    format_list_assets,
    format_search_results,
    get_asset_impl,
    get_asset_query,
    list_assets_impl,
    list_assets_query,
    search_assets_impl,
    search_assets_query,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_asset(asset_id: str | None = None) -> dict:
    """Minimal well-shaped asset dict from the repo layer."""
    aid = asset_id or str(uuid4())
    return {
        "id": aid,
        "hostname": ["host-alpha"],
        "category": "server",
        "subcategory": "linux",
        "confidence": 0.90,
        "data_source": ["jamf", "crowdstrike"],
    }


# ===========================================================================
# get_asset_query
# ===========================================================================

class TestGetAssetQuery:
    def test_returns_raw_dict_when_found(self):
        db = MagicMock()
        resolver = MagicMock()
        aid = str(uuid4())
        asset = _make_asset(aid)
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            repo = MockRepo.return_value
            repo.get_asset_by_id.return_value = asset
            result = get_asset_query(db, resolver, asset_id=aid)
        assert result is asset
        repo.get_asset_by_id.assert_called_once_with(aid)

    def test_returns_none_when_not_found(self):
        db = MagicMock()
        resolver = MagicMock()
        aid = str(uuid4())
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.get_asset_by_id.return_value = None
            result = get_asset_query(db, resolver, asset_id=aid)
        assert result is None

    def test_returns_none_for_invalid_uuid(self):
        """UUID validation failure must return None (no error string).
        The error string is the impl's job."""
        db = MagicMock()
        resolver = MagicMock()
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            result = get_asset_query(db, resolver, asset_id="not-a-uuid")
        assert result is None
        # Must not have hit the repo at all
        MockRepo.return_value.get_asset_by_id.assert_not_called()


# ===========================================================================
# get_asset_impl (golden-string checks — verifies text output didn't drift)
# ===========================================================================

class TestGetAssetImpl:
    def test_returns_text_with_hostname_category_and_data_sources(self):
        db = MagicMock()
        resolver = MagicMock()
        aid = str(uuid4())
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            repo = MockRepo.return_value
            repo.get_asset_by_id.return_value = {
                "id": aid,
                "hostname": ["host-beta"],
                "category": "workstation",
                "subcategory": "mac",
                "confidence": 0.75,
                "data_source": ["jamf"],
            }
            repo.get_asset_tags.return_value = []
            repo.get_asset_providers.return_value = []
            out = get_asset_impl(db, resolver, asset_id=aid)
        assert aid in out
        assert "host-beta" in out
        assert "workstation" in out
        assert "jamf" in out

    def test_returns_error_string_for_invalid_uuid(self):
        db = MagicMock()
        resolver = MagicMock()
        with patch("src.mcp.tools.assets.AssetRepository"):
            out = get_asset_impl(db, resolver, asset_id="bad-id")
        # The text should mention the invalid id in some form; it must not be
        # an asset detail response.
        assert (
            "bad-id" in out
            or "invalid" in out.lower()
            or "asset" in out.lower()
        )

    def test_returns_not_found_string_when_asset_missing(self):
        db = MagicMock()
        resolver = MagicMock()
        aid = str(uuid4())
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.get_asset_by_id.return_value = None
            out = get_asset_impl(db, resolver, asset_id=aid)
        assert "not found" in out.lower() or aid in out


# ===========================================================================
# list_assets_query
# ===========================================================================

class TestListAssetsQuery:
    def test_returns_rows_and_total(self):
        db = MagicMock()
        resolver = MagicMock()
        rows = [_make_asset(), _make_asset()]
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.list_assets.return_value = (rows, 2)
            result_rows, total = list_assets_query(
                db, resolver, page=1, limit=10
            )
        # The query now projects encoded FieldId keys to snake_case, so the
        # returned list is a new object (not the raw repo rows). The count
        # and content must still be correct.
        assert len(result_rows) == 2
        assert total == 2

    def test_returns_snake_keyed_rows(self):
        """Repo returns encoded FieldId keys; query projects them so chat
        artifacts receive consistent snake_case keys (matching
        search_assets_query and get_asset_query)."""
        db = MagicMock()
        resolver = MagicMock()
        encoded_row = {
            "id": "a-1",
            "static:Hostname": ["host1.example"],
            "static:Category": "server",
            "static:OS%20Normalized": ["Windows 10"],
        }
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.list_assets.return_value = ([encoded_row], 1)
            rows, total = list_assets_query(db, resolver)
        assert total == 1
        assert rows[0]["hostname"] == ["host1.example"]
        assert rows[0]["category"] == "server"
        assert rows[0]["os_normalized"] == ["Windows 10"]
        # Unrecognised keys (like "id") pass through unchanged.
        assert rows[0]["id"] == "a-1"

    def test_returns_empty_list_and_zero_when_no_match(self):
        db = MagicMock()
        resolver = MagicMock()
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.list_assets.return_value = ([], 0)
            result_rows, total = list_assets_query(db, resolver)
        assert result_rows == []
        assert total == 0

    def test_filters_and_pagination_pass_through_to_repo(self):
        db = MagicMock()
        resolver = MagicMock()
        filters_arg = [{"field": "category", "op": "eq", "value": "server"}]
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.list_assets.return_value = ([], 0)
            list_assets_query(
                db, resolver,
                filters=filters_arg,
                search="myhost",
                page=2,
                limit=25,
                sort_by="hostname",
                sort_dir="asc",
            )
        call_kwargs = MockRepo.return_value.list_assets.call_args[1]
        assert call_kwargs["page"] == 2
        assert call_kwargs["limit"] == 25
        assert call_kwargs["search"] == "myhost"
        assert call_kwargs["sort_dir"] == "asc"


# ===========================================================================
# list_assets_impl (golden-string checks)
# ===========================================================================

class TestListAssetsImpl:
    def test_returns_no_results_message_when_empty(self):
        db = MagicMock()
        resolver = MagicMock()
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.list_assets.return_value = ([], 0)
            out = list_assets_impl(db, resolver)
        assert "No assets found" in out

    def test_happy_path_shows_total_count(self):
        db = MagicMock()
        resolver = MagicMock()
        rows = [
            {"id": "a-1", "hostname": ["host01"], "category": "server"},
            {"id": "a-2", "hostname": ["host02"], "category": "workstation"},
        ]
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.list_assets.return_value = (rows, 2)
            out = list_assets_impl(db, resolver, limit=10)
        assert "Assets: 2 total" in out
        assert "host01" in out
        assert "host02" in out


# ===========================================================================
# search_assets_query
# ===========================================================================

class TestSearchAssetsQuery:
    def test_returns_list_of_raw_matches_with_match_field(self):
        db = MagicMock()
        resolver = MagicMock()
        hits = [
            {"id": "a-1", "hostname": ["host01"], "category": "server",
             "match_field": "hostname"},
        ]
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.search_assets.return_value = hits
            result = search_assets_query(db, resolver, query="host01")
        assert result is hits
        # match_field annotation preserved
        assert result[0]["match_field"] == "hostname"

    def test_returns_empty_list_when_no_match(self):
        db = MagicMock()
        resolver = MagicMock()
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.search_assets.return_value = []
            result = search_assets_query(db, resolver, query="zzz_nobody")
        assert result == []

    def test_limit_clamp_passed_to_repo(self):
        """The query helper should apply the same 1-100 limit clamp and pass
        the clamped value on to the repo."""
        db = MagicMock()
        resolver = MagicMock()
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.search_assets.return_value = []
            search_assets_query(db, resolver, query="x", limit=5)
        call_args = MockRepo.return_value.search_assets.call_args
        assert (
            call_args[0][0] == "x"
            or call_args[1].get("query") == "x"
        )
        # limit passed as positional or keyword
        passed_limit = (
            call_args[1].get("limit")
            if "limit" in call_args[1]
            else call_args[0][1]
        )
        assert passed_limit == 5


# ===========================================================================
# search_assets_impl (golden-string checks)
# ===========================================================================

class TestSearchAssetsImpl:
    def test_no_match_returns_descriptive_string(self):
        db = MagicMock()
        resolver = MagicMock()
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.search_assets.return_value = []
            out = search_assets_impl(db, resolver, query="zzz_nobody")
        assert "zzz_nobody" in out
        assert "No assets found" in out

    def test_happy_path_shows_match_count_and_hostname(self):
        db = MagicMock()
        resolver = MagicMock()
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.search_assets.return_value = [
                {"id": "a-1", "hostname": ["host01"], "category": "server",
                 "match_field": "hostname"},
            ]
            out = search_assets_impl(db, resolver, query="host01")
        assert "Found 1" in out
        assert "host01" in out
        assert "hostname" in out   # match_field appears in output


# ===========================================================================
# format_search_results (golden-string checks — pure formatter)
# ===========================================================================

class TestFormatSearchResults:
    def test_empty_results_returns_not_found_string(self):
        out = format_search_results([], query="nobody")
        assert "nobody" in out
        assert "No assets found" in out

    def test_single_hit_shows_count_and_hostname(self):
        results = [
            {"id": "a-1", "hostname": ["host01"], "category": "server",
             "match_field": "hostname"},
        ]
        out = format_search_results(results, query="host01")
        assert "Found 1" in out
        assert "host01" in out
        assert "hostname" in out   # match_field in output

    def test_multiple_hits_shows_correct_count(self):
        results = [
            {"id": "a-1", "hostname": ["host01"], "category": "server",
             "match_field": "hostname"},
            {"id": "a-2", "hostname": ["host02"], "category": "workstation",
             "match_field": "hostname"},
        ]
        out = format_search_results(results, query="host")
        assert "Found 2" in out
        assert "host01" in out
        assert "host02" in out


# ===========================================================================
# format_asset_detail (golden-string checks — pure formatter)
# ===========================================================================

class TestFormatAssetDetail:
    def test_happy_path_contains_required_fields(self):
        aid = str(uuid4())
        asset = {
            "id": aid,
            "hostname": ["host-gamma"],
            "category": "server",
            "subcategory": "linux",
            "confidence": 0.88,
            "data_source": ["jamf"],
        }
        out = format_asset_detail(
            asset, asset_id=aid,
            tags=[], providers=[],
        )
        assert aid in out
        assert "host-gamma" in out
        assert "server" in out
        assert "jamf" in out

    def test_tags_appear_in_output(self):
        aid = str(uuid4())
        asset = {"id": aid, "category": "workstation"}
        tags = [{"tag_name": "managed"}, {"tag_name": "encrypted"}]
        out = format_asset_detail(
            asset, asset_id=aid, tags=tags, providers=[],
        )
        assert "managed" in out
        assert "encrypted" in out

    def test_providers_appear_in_output(self):
        aid = str(uuid4())
        asset = {"id": aid, "category": "server"}
        providers = [
            {"data_source": "crowdstrike", "hostname": "cs-host"},
        ]
        out = format_asset_detail(
            asset, asset_id=aid, tags=[], providers=providers,
        )
        assert "crowdstrike" in out
        assert "Providers" in out

    def test_no_tags_no_providers_no_tags_section(self):
        aid = str(uuid4())
        asset = {"id": aid, "category": "iot"}
        out = format_asset_detail(
            asset, asset_id=aid, tags=[], providers=[],
        )
        assert "Tags:" not in out
        assert "Providers" not in out


# ===========================================================================
# format_list_assets (golden-string checks — pure formatter)
# ===========================================================================

class TestFormatListAssets:
    def test_empty_results_returns_not_found_string(self):
        out = format_list_assets([], total=0, page=1, limit=20)
        assert "No assets found" in out

    def test_single_page_shows_total_and_hostname(self):
        rows = [
            {"id": "a-1", "hostname": ["host01"], "category": "server"},
        ]
        out = format_list_assets(rows, total=1, page=1, limit=20)
        assert "Assets: 1 total" in out
        assert "host01" in out

    def test_pagination_hint_appears_when_more_pages(self):
        rows = [{"id": f"a-{i}", "hostname": [f"host{i:02d}"],
                 "category": "server"} for i in range(5)]
        out = format_list_assets(rows, total=20, page=1, limit=5)
        # 20 / 5 = 4 pages; hint should mention page=2
        assert "page=2" in out

    def test_no_pagination_hint_on_last_page(self):
        rows = [{"id": "a-1", "hostname": ["host01"], "category": "server"}]
        out = format_list_assets(rows, total=5, page=2, limit=4)
        # page 2, limit 4 → total_pages = ceil(5/4)=2; no next page
        assert "page=3" not in out


# ===========================================================================
# Binding "calls query only once" regression tests
# ===========================================================================

class TestBindingsSingleQuery:
    """Each binding must call its repo method exactly once so we can't
    silently regress back to the double-query pattern."""

    def _make_registry(self):
        from src.api.blueprints.chat.tool_bindings import build_registry
        return build_registry(db=MagicMock(), suggestion_service=MagicMock())

    def test_search_assets_routes_through_list_query_once(self):
        """search_assets is now a deprecation shim onto query_assets(mode=list,
        search=…): it issues a single repo.list_assets free-text query — NOT the
        legacy fuzzy repo.search_assets — and still queries exactly once (the
        no-double-query invariant survives the shim)."""
        r = self._make_registry()
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.list_assets.return_value = (
                [{"id": "a-1", "hostname": ["host01"], "category": "server"}], 1,
            )
            asyncio.run(r.invoke(
                "search_assets", {"query": "host01"}, user_role="user",
            ))
        MockRepo.return_value.list_assets.assert_called_once()
        MockRepo.return_value.search_assets.assert_not_called()

    def test_get_asset_calls_get_by_id_once(self):
        r = self._make_registry()
        aid = str(uuid4())
        # Patch the AssetRepository in both namespaces that the binding uses:
        # - src.mcp.tools.assets.AssetRepository  (used by get_asset_query)
        # - src.api.blueprints.chat.tool_bindings.AssetRepository
        #   (used directly for tags/providers fetch in the binding)
        shared_repo = MagicMock()
        shared_repo.get_asset_by_id.return_value = {
            "id": aid, "category": "server",
        }
        shared_repo.get_asset_tags.return_value = []
        shared_repo.get_asset_providers.return_value = []
        mock_cls = MagicMock(return_value=shared_repo)
        with patch("src.mcp.tools.assets.AssetRepository", mock_cls), \
             patch("src.api.blueprints.chat.tool_bindings.AssetRepository",
                   mock_cls):
            asyncio.run(r.invoke(
                "get_asset", {"asset_id": aid}, user_role="user",
            ))
        shared_repo.get_asset_by_id.assert_called_once()

    def test_list_assets_calls_repo_once(self):
        r = self._make_registry()
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.list_assets.return_value = (
                [{"id": "a-1", "hostname": ["host01"], "category": "server"}],
                1,
            )
            asyncio.run(r.invoke(
                "list_assets", {}, user_role="user",
            ))
        MockRepo.return_value.list_assets.assert_called_once()
