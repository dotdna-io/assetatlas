# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression for the no-app-context FieldResolver factory (#792, refs #791).

Several sites build a FieldResolver in a process/thread with NO Flask app
context (the pipeline orchestrator, s20 alert evaluation, the scheduler worker,
tag-rule execution, and the MCP server). Each must read its integration-group
mapping straight from the DB — not via ``get_cached_group_mapping``, which reads
``current_app`` and silently returns ``{}`` when there is no app context. An
empty mapping erases every ``integration_group`` entry from the catalog, so a
``group:<group>:<attr>`` field fails to resolve with ``InvalidFieldError``.

``build_field_resolver`` centralises the correct construction so no-app-context
sites stop hand-rolling the catalog with an empty-``{}`` provider (the bug that
hit the MCP server in #792 and the scheduled pipeline in #791).
"""

from src.modules.shared.field_resolver_factory import build_field_resolver
from src.modules.fields.models import IntegrationGroupField


class _FakeDB:
    """db_adapter stub routing only the queries a no-app-context resolver build
    runs: the integration-group mapping (repo) and the catalog's discovery scans."""

    def execute_query(self, query, params=None, fetch=True):
        if "integration_groups" in query:      # IntegrationGroupRepository.get_group_mapping
            return [{"integration_name": "Corp SentinelOne", "group_name": "SentinelOne"}]
        if "jsonb_object_keys" in query:        # catalog extended_attribute discovery
            return [{"source_name": "Corp SentinelOne", "field_name": "siteId"}]
        return []                               # tags, posture, unified_fields → empty


def test_build_field_resolver_resolves_integration_group_field():
    # The factory must surface integration_group fields, which requires a
    # non-empty group mapping read straight from the DB. The empty-{} provider
    # (what the MCP server used before #792) would make resolve() raise
    # InvalidFieldError here.
    resolver = build_field_resolver(_FakeDB())

    resolved = resolver.resolve(
        IntegrationGroupField(
            kind="integration_group", group="SentinelOne", attribute="siteId"
        )
    )

    # Resolves to a COALESCE over the grouped member source's extended_attributes
    # instead of raising InvalidFieldError.
    where = resolved.where_expr()
    assert "extended_attributes" in where
    assert "Corp SentinelOne" in where
    assert "siteId" in where
