# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unified-field CRUD bumps the asset-data version stamp (#1410).

Creating a unified field introduces a new set of distinct values, updating
one rebuilds them from the new definition, and deleting one removes them —
all three change what the Explorer field-values cache must serve, so each
path bumps the stamp after its write (and its materialized-value refresh).

Harness mirrors test_service.py: a real FieldCatalog over a stub DB, the
repository a MagicMock.
"""

from unittest.mock import MagicMock, patch

from src.modules.fields.catalog import FieldCatalog
from src.modules.fields.resolver import FieldResolver
from src.modules.fields.unified.models import UnifiedFieldWrite
from src.modules.fields.unified.service import UnifiedFieldsService

F1 = "11111111-2222-3333-4444-555555555555"

_BUMP = "src.modules.fields.unified.service.asset_data_version.bump"


class _DB:
    def __init__(self):
        self.conn_manager = MagicMock()

    def execute_query(self, *a, **k):
        return []


def _service(repo, defs=None):
    cat = FieldCatalog(db_adapter=_DB(), group_mapping_provider=lambda: {},
                       unified_fields_provider=lambda: (defs or []))
    resolver = FieldResolver(catalog=cat)
    return UnifiedFieldsService(repo, catalog=cat, resolver=resolver, db_adapter=_DB())


def _write(**kw):
    base = dict(name="lentity", function="coalesce",
                sources=["static:Category", "static:Subcategory"])
    base.update(kw)
    return UnifiedFieldWrite(**base)


def _row(**over):
    base = {
        "id": F1, "slug": "lentity", "name": "lentity", "function": "coalesce",
        "sources": ["static:Category", "static:Subcategory"],
        "data_type": "string", "created_at": None, "updated_at": None,
    }
    base.update(over)
    return base


def test_create_bumps_the_data_stamp():
    repo = MagicMock()
    repo.name_exists.return_value = False
    repo.slug_exists.return_value = False
    repo.create.return_value = _row()
    svc = _service(repo)
    with patch(_BUMP) as mock_bump:
        svc.create(_write())
    mock_bump.assert_called_once_with()


def test_update_bumps_the_data_stamp():
    repo = MagicMock()
    repo.name_exists.return_value = False
    repo.get.return_value = _row()
    repo.update.return_value = _row(name="lentity2")
    svc = _service(repo)
    with patch(_BUMP) as mock_bump:
        svc.update(F1, _write(name="lentity2"))
    mock_bump.assert_called_once_with()


def test_delete_bumps_the_data_stamp():
    repo = MagicMock()
    repo.get.return_value = _row()
    repo.delete.return_value = True
    svc = _service(repo)
    with patch(_BUMP) as mock_bump:
        svc.delete(F1)
    mock_bump.assert_called_once_with()
