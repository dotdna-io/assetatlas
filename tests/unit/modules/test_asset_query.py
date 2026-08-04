# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for AssetRepository.query_assets()."""

import pytest
from unittest.mock import MagicMock

from src.modules.assets.repositories.asset_repository import AssetRepository
from src.modules.fields import FieldCatalog, FieldResolver


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def repo(db):
    resolver_db = MagicMock()
    resolver_db.execute_query.return_value = []
    resolver = FieldResolver(catalog=FieldCatalog(db_adapter=resolver_db))
    return AssetRepository(db, resolver=resolver)


class TestQueryAssets:
    def _mock_query(self, db, rows, total=None):
        """Set up mock to return count then rows for query_assets calls."""
        if total is None:
            total = len(rows)
        db.execute_query.side_effect = [[{'total': total}], rows]

    def test_query_by_ip(self, repo, db):
        self._mock_query(db, [{'id': 'a1', 'hostname': '{web01}'}])
        rows, total = repo.query_assets(ip='10.1.2.3')
        assert len(rows) == 1
        assert total == 1
        # Second call is the SELECT
        sql = db.execute_query.call_args_list[1][0][0]
        assert 'ip' in sql.lower()

    def test_query_by_hostname(self, repo, db):
        self._mock_query(db, [])
        repo.query_assets(hostname='web01')
        sql = db.execute_query.call_args_list[1][0][0]
        assert 'hostname' in sql.lower()
        assert 'ILIKE' in sql

    def test_query_by_fqdn(self, repo, db):
        self._mock_query(db, [])
        repo.query_assets(fqdn='web01.corp.local')
        sql = db.execute_query.call_args_list[1][0][0]
        assert 'fqdn' in sql.lower()

    def test_query_by_mac(self, repo, db):
        self._mock_query(db, [])
        repo.query_assets(mac='AA:BB:CC:DD:EE:FF')
        sql = db.execute_query.call_args_list[1][0][0]
        assert 'mac' in sql.lower()

    def test_query_by_sn(self, repo, db):
        self._mock_query(db, [])
        repo.query_assets(sn='SN12345')
        sql = db.execute_query.call_args_list[1][0][0]
        assert 'sn' in sql.lower()

    def test_query_free_text(self, repo, db):
        self._mock_query(db, [])
        repo.query_assets(q='web01')
        sql = db.execute_query.call_args_list[1][0][0]
        # Free text searches all fields
        assert 'hostname' in sql.lower()
        assert 'ip' in sql.lower()

    def test_multiple_filters_combine_with_and(self, repo, db):
        self._mock_query(db, [])
        repo.query_assets(ip='10.1.2.3', hostname='web')
        sql = db.execute_query.call_args_list[1][0][0]
        assert 'AND' in sql

    def test_respects_limit(self, repo, db):
        self._mock_query(db, [])
        repo.query_assets(ip='10.1.2.3', limit=5)
        params = db.execute_query.call_args_list[1][0][1]
        assert params['limit'] == 5

    def test_default_limit_is_10(self, repo, db):
        self._mock_query(db, [])
        repo.query_assets(ip='10.1.2.3')
        params = db.execute_query.call_args_list[1][0][1]
        assert params['limit'] == 10

    def test_select_specific_fields(self, repo, db):
        self._mock_query(db, [{'id': 'a1', 'hostname': '{web01}', 'ip': '{10.1.2.3}'}])
        rows, total = repo.query_assets(ip='10.1.2.3', fields=['id', 'hostname', 'ip'])
        sql = db.execute_query.call_args_list[1][0][0]
        assert 'hostname' in sql
        assert 'ip' in sql

    def test_total_exceeds_limit(self, repo, db):
        self._mock_query(db, [{'id': 'a1'}], total=50)
        rows, total = repo.query_assets(ip='10.1.2.3', limit=1)
        assert len(rows) == 1
        assert total == 50


class TestGetTagsForAssets:
    def test_returns_grouped_tags(self, repo, db):
        db.execute_query.return_value = [
            {'asset_id': 'a1', 'tag_name': 'prod', 'color': '#00ff00', 'tag_type': 'classification'},
            {'asset_id': 'a1', 'tag_name': 'critical', 'color': '#ff0000', 'tag_type': 'classification'},
            {'asset_id': 'a2', 'tag_name': 'dev', 'color': '#0000ff', 'tag_type': 'classification'},
        ]
        result = repo.get_tags_for_assets(['a1', 'a2'])
        assert len(result['a1']) == 2
        assert len(result['a2']) == 1

    def test_empty_ids_returns_empty(self, repo, db):
        result = repo.get_tags_for_assets([])
        assert result == {}
