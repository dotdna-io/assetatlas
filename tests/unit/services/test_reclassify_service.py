# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for ReclassifyService — manual asset reclassification.

Mocks: PostgreSQLAdapter, CategoryOverrideRepository
"""

from typing import NamedTuple
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.assets.services.reclassify_service import ReclassifyService


class ServiceBundle(NamedTuple):
    service: ReclassifyService
    db: MagicMock
    override_repo: MagicMock
    custom_repo: MagicMock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ASSET_ID = str(uuid4())

ASSET_ROW = {
    "id": ASSET_ID,
    "category": "Server",
    "subcategory": "Linux Server",
    "manufacturer": "Dell",
    "model": "PowerEdge R740",
    "os_normalized": "Ubuntu 22.04",
    "mac_vendor": "Dell Inc.",
    "vendor_category": "Server",
}

CATEGORIES_DATA = {
    "categories": [
        {
            "name": "Workstation",
            "subcategories": ["Windows Workstation", "Mac Workstation"],
        },
        {
            "name": "Server",
            "subcategories": ["Linux Server", "Windows Server"],
        },
    ]
}


def _make_service(custom_subcategories=None, catalog=None):
    db_adapter = MagicMock()
    override_repo = MagicMock()
    custom_repo = MagicMock()
    custom_map = custom_subcategories or {}
    custom_repo.get_all.return_value = custom_map
    custom_repo.get_for_category.side_effect = lambda cat: custom_map.get(cat, [])
    db_adapter.bulk_write_chronicle_events = MagicMock()

    with patch("builtins.open", MagicMock()), \
         patch("os.path.exists", return_value=True), \
         patch("json.load", return_value=catalog or CATEGORIES_DATA):
        service = ReclassifyService(
            db_adapter=db_adapter,
            override_repo=override_repo,
            custom_subcategory_repo=custom_repo,
        )

    return ServiceBundle(service, db_adapter, override_repo, custom_repo)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReclassifyServiceFeaturesSnapshot:

    def test_features_snapshot_passed_to_save_override(self):
        """save_override receives features_snapshot built from current asset fields."""
        service, db_adapter, override_repo, _ = _make_service()
        db_adapter.execute_query.return_value = [ASSET_ROW]

        service.reclassify_asset(
            asset_id=ASSET_ID,
            category="Workstation",
            subcategory="Windows Workstation",
            user="admin",
        )

        override_repo.save_override.assert_called_once()
        call_kwargs = override_repo.save_override.call_args.kwargs
        assert "features_snapshot" in call_kwargs

        snapshot = call_kwargs["features_snapshot"]
        assert snapshot["manufacturer"] == "Dell"
        assert snapshot["model"] == "PowerEdge R740"
        assert snapshot["os_normalized"] == "Ubuntu 22.04"
        assert snapshot["mac_vendor"] == "Dell Inc."
        assert snapshot["vendor_category"] == "Server"

    def test_features_snapshot_defaults_to_empty_string_for_missing_fields(self):
        """Missing feature fields default to empty string, not None or KeyError."""
        service, db_adapter, override_repo, _ = _make_service()
        sparse_row = {
            "id": ASSET_ID,
            "category": "Server",
            "subcategory": "Linux Server",
            # no manufacturer, model, os_normalized, mac_vendor, vendor_category
        }
        db_adapter.execute_query.return_value = [sparse_row]

        service.reclassify_asset(
            asset_id=ASSET_ID,
            category="Workstation",
            subcategory="Windows Workstation",
            user="admin",
        )

        call_kwargs = override_repo.save_override.call_args.kwargs
        snapshot = call_kwargs["features_snapshot"]
        assert snapshot["manufacturer"] == ""
        assert snapshot["model"] == ""
        assert snapshot["os_normalized"] == ""
        assert snapshot["mac_vendor"] == ""
        assert snapshot["vendor_category"] == ""

    def test_raises_lookup_error_when_asset_not_found(self):
        """LookupError raised if asset does not exist in DB."""
        service, db_adapter, override_repo, _ = _make_service()
        db_adapter.execute_query.return_value = []

        with pytest.raises(LookupError, match="Asset not found"):
            service.reclassify_asset(
                asset_id=ASSET_ID,
                category="Workstation",
                subcategory="Windows Workstation",
                user="admin",
            )

        override_repo.save_override.assert_not_called()


# ---------------------------------------------------------------------------
# Custom subcategory tests
# ---------------------------------------------------------------------------

class TestCustomSubcategories:
    """get_categories merges and add_custom_subcategory persists."""

    def test_get_categories_merges_custom_after_catalog(self):
        service, _, _, _ = _make_service(
            custom_subcategories={"Server": ["Custom Linux"]}
        )

        merged = service.get_categories()
        server = next(c for c in merged if c["name"] == "Server")
        assert server["subcategories"] == [
            "Linux Server",
            "Windows Server",
            "Custom Linux",
        ]

    def test_get_categories_dedups_against_catalog_case_insensitive(self):
        service, _, _, _ = _make_service(
            custom_subcategories={"Server": ["linux server"]}
        )

        merged = service.get_categories()
        server = next(c for c in merged if c["name"] == "Server")
        # Collision with catalog → custom dropped, catalog kept.
        assert server["subcategories"] == ["Linux Server", "Windows Server"]

    def test_reclassify_accepts_custom_subcategory(self):
        service, db_adapter, override_repo, _ = _make_service(
            custom_subcategories={"Server": ["Edge Server"]}
        )
        db_adapter.execute_query.return_value = [ASSET_ROW]

        # Should not raise — custom subcategory is in the merged set.
        service.reclassify_asset(
            asset_id=ASSET_ID,
            category="Server",
            subcategory="Edge Server",
            user="admin",
        )

        override_repo.save_override.assert_called_once()

    def test_add_custom_subcategory_trims_and_persists(self):
        service, _, _, custom_repo = _make_service()

        result = service.add_custom_subcategory(
            category="Server",
            name="  Edge Server  ",
            user="admin",
        )

        custom_repo.add.assert_called_once_with("Server", "Edge Server", "admin")
        assert result["subcategory"] == "Edge Server"

    def test_add_custom_subcategory_rejects_unknown_category(self):
        service, _, _, custom_repo = _make_service()

        with pytest.raises(ValueError, match="Invalid category"):
            service.add_custom_subcategory(
                category="DoesNotExist",
                name="X",
                user="admin",
            )
        custom_repo.add.assert_not_called()

    def test_add_custom_subcategory_rejects_empty_name(self):
        service, _, _, custom_repo = _make_service()

        with pytest.raises(ValueError, match="must not be empty"):
            service.add_custom_subcategory(
                category="Server",
                name="   ",
                user="admin",
            )
        custom_repo.add.assert_not_called()

    def test_add_custom_subcategory_rejects_too_long(self):
        service, _, _, custom_repo = _make_service()

        with pytest.raises(ValueError, match="64 characters"):
            service.add_custom_subcategory(
                category="Server",
                name="a" * 65,
                user="admin",
            )
        custom_repo.add.assert_not_called()

    def test_add_custom_subcategory_rejects_catalog_collision(self):
        service, _, _, custom_repo = _make_service()

        with pytest.raises(ValueError, match="already exists"):
            service.add_custom_subcategory(
                category="Server",
                name="linux server",  # collides with catalog "Linux Server"
                user="admin",
            )
        custom_repo.add.assert_not_called()

    @pytest.mark.parametrize(
        "name",
        [
            "bad\nname",         # newline
            "bad\rname",         # carriage return
            "bad\tname",         # tab
            "bad\x00name",       # null
            "bad\x01name",       # SOH (control char)
            "bad\x7fname",       # DEL
            "bad\x1fname",       # unit separator
            "café",              # non-ASCII
        ],
    )
    def test_add_custom_subcategory_rejects_non_printable_ascii(self, name):
        service, _, _, custom_repo = _make_service()

        with pytest.raises(ValueError, match="printable ASCII"):
            service.add_custom_subcategory(
                category="Server",
                name=name,
                user="admin",
            )
        custom_repo.add.assert_not_called()


# ---------------------------------------------------------------------------
# Reclassify hot-path: catalog values must NOT trigger a custom-table query.
# ---------------------------------------------------------------------------


class TestReclassifyValidationFastPath:
    def test_catalog_subcategory_skips_custom_repo_lookup(self):
        service, db_adapter, _, custom_repo = _make_service()
        db_adapter.execute_query.return_value = [ASSET_ROW]

        service.reclassify_asset(
            asset_id=ASSET_ID,
            category="Server",
            subcategory="Linux Server",  # in catalog
            user="admin",
        )

        custom_repo.get_for_category.assert_not_called()
        custom_repo.get_all.assert_not_called()

    def test_non_catalog_subcategory_falls_through_to_custom_repo(self):
        service, db_adapter, _, custom_repo = _make_service(
            custom_subcategories={"Server": ["Edge Server"]}
        )
        db_adapter.execute_query.return_value = [ASSET_ROW]

        service.reclassify_asset(
            asset_id=ASSET_ID,
            category="Server",
            subcategory="Edge Server",  # custom, not in catalog
            user="admin",
        )

        custom_repo.get_for_category.assert_called_once_with("Server")


# ---------------------------------------------------------------------------
# Lowercase-catalog test mirroring data/categories.json casing.
# ---------------------------------------------------------------------------


_LOWERCASE_CATALOG = {
    "categories": [
        {"name": "server", "subcategories": ["server", "hypervisor"]},
        {"name": "workstation", "subcategories": ["laptop", "workstation"]},
    ]
}


class TestLowercaseCatalog:
    def test_add_with_lowercase_category_succeeds(self):
        service, _, _, custom_repo = _make_service(catalog=_LOWERCASE_CATALOG)

        result = service.add_custom_subcategory(
            category="server",
            name="edge node",
            user="admin",
        )

        custom_repo.add.assert_called_once_with("server", "edge node", "admin")
        assert result["subcategory"] == "edge node"

    def test_add_rejects_title_case_when_catalog_is_lowercase(self):
        service, _, _, custom_repo = _make_service(catalog=_LOWERCASE_CATALOG)

        # Catalog match is case-sensitive; title-case "Server" must not
        # resolve to lowercase "server".
        with pytest.raises(ValueError, match="Invalid category"):
            service.add_custom_subcategory(
                category="Server",
                name="anything",
                user="admin",
            )
        custom_repo.add.assert_not_called()

    def test_collision_check_against_lowercase_catalog_is_case_insensitive(self):
        service, _, _, custom_repo = _make_service(catalog=_LOWERCASE_CATALOG)

        with pytest.raises(ValueError, match="already exists"):
            service.add_custom_subcategory(
                category="server",
                name="HYPERVISOR",  # collides with lowercase catalog "hypervisor"
                user="admin",
            )
        custom_repo.add.assert_not_called()
