# SPDX-License-Identifier: AGPL-3.0-or-later
from src.api.modules.settings import settings_schema
from src.modules.asset_generation.services.pipeline_orchestrator import (
    _resolve_llm_descriptions_enabled,
)


def test_schema_default_is_on():
    # #927: on by default (opt-out); only takes effect once a provider exists.
    assert settings_schema.get_default_value(
        "embedding", "llm_descriptions_enabled") is True
    assert settings_schema.validate_setting_value(
        "embedding", "llm_descriptions_enabled", False) is True


class _DB:
    def __init__(self, rows): self._rows = rows
    def execute_query(self, sql, params=None): return self._rows


def test_resolver_reads_db_true():
    assert _resolve_llm_descriptions_enabled(_DB([{"value": "true"}])) is True


def test_resolver_reads_db_false_opt_out():
    # An explicit opt-out is honoured even though the default is now on.
    assert _resolve_llm_descriptions_enabled(_DB([{"value": "false"}])) is False


def test_resolver_defaults_on_when_unset():
    assert _resolve_llm_descriptions_enabled(_DB([])) is True
