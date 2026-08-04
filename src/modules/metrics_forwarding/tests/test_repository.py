# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.modules.metrics_forwarding.repository import MetricsForwardingRepository
from src.modules.integrations.shared.encryption import decrypt, encrypt, is_encrypted


def _repo(rows=None):
    db = MagicMock()
    db.execute_query.return_value = rows if rows is not None else []
    return MetricsForwardingRepository(db), db


def test_save_config_upserts_single_row_and_encrypts_secret():
    repo, db = _repo([{"id": 1, "external_labels": {}, "auth_secret": None}])
    repo.save_config(
        endpoint_url="https://x/push",
        auth_type="bearer",
        auth_secret="tok",
        username=None,
        external_labels={"a": "b"},
        enabled=True,
        updated_by="admin",
    )
    sql, params = db.execute_query.call_args.args
    # Single-row UPSERT pinned to id = 1 (not one row per save).
    assert "INSERT INTO metrics_forwarding_config" in sql
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert params["id"] == 1
    # Secret is encrypted before the insert — plaintext never reaches the DB.
    assert is_encrypted(params["auth_secret"]) and "tok" not in params["auth_secret"]
    assert decrypt(params["auth_secret"]) == "tok"
    # external_labels serialized as JSON for the ::jsonb cast.
    assert json.loads(params["external_labels"]) == {"a": "b"}


def test_save_config_none_secret_stays_none():
    repo, db = _repo([{"id": 1, "auth_secret": None, "external_labels": {}}])
    repo.save_config(
        endpoint_url=None,
        auth_type="none",
        auth_secret=None,
        username=None,
        external_labels={},
        enabled=False,
        updated_by=None,
    )
    params = db.execute_query.call_args.args[1]
    assert params["auth_secret"] is None


def test_save_config_keeps_already_encrypted_secret_without_double_wrapping():
    enc = encrypt("keeper")
    repo, db = _repo([{"id": 1, "auth_secret": enc, "external_labels": {}}])
    repo.save_config(
        endpoint_url="https://x",
        auth_type="bearer",
        auth_secret=enc,
        username=None,
        external_labels={},
        enabled=True,
        updated_by=None,
    )
    params = db.execute_query.call_args.args[1]
    # encrypt() is idempotent on already-encrypted input — no re-wrapping.
    assert params["auth_secret"] == enc and decrypt(params["auth_secret"]) == "keeper"


def test_get_config_decrypts_only_when_asked():
    enc = encrypt("s3cr3t")
    row = {
        "id": 1,
        "endpoint_url": "u",
        "auth_type": "bearer",
        "auth_secret": enc,
        "username": None,
        "external_labels": {},
        "enabled": True,
        "updated_at": None,
        "updated_by": None,
    }
    repo, db = _repo([dict(row)])
    masked = repo.get_config(decrypt_secret=False)
    assert masked["auth_secret"] == enc  # still ciphertext; the service masks it
    db.execute_query.return_value = [dict(row)]
    plain = repo.get_config(decrypt_secret=True)
    assert plain["auth_secret"] == "s3cr3t"


def test_get_config_coerces_jsonb_string_labels():
    repo, _ = _repo(
        [{"id": 1, "auth_secret": None, "external_labels": '{"env": "prod"}'}]
    )
    cfg = repo.get_config()
    assert cfg["external_labels"] == {"env": "prod"}


def test_get_config_returns_none_when_no_row():
    repo, db = _repo([])
    assert repo.get_config() is None
    # Reads the single row by its pinned id.
    params = db.execute_query.call_args.args[1]
    assert params["id"] == 1


def test_get_config_datetime_passthrough():
    when = datetime(2026, 7, 11, tzinfo=timezone.utc)
    repo, _ = _repo(
        [{"id": 1, "auth_secret": None, "external_labels": {}, "updated_at": when}]
    )
    assert repo.get_config()["updated_at"] == when
