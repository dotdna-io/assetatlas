# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared factory: build a FieldResolver for code with NO Flask app context.

The scheduler worker, the asset-generation pipeline orchestrator, tag-rule
execution, alert evaluation (s20), and the MCP server all build a FieldResolver
while running outside any Flask app context. In that state the cached provider
``get_cached_group_mapping`` reads ``current_app`` and silently returns ``{}`` —
which erases every ``integration_group`` entry from the ``FieldCatalog``, so a
``group:<group>:<attr>`` field fails to resolve with ``InvalidFieldError`` (the
scheduled-pipeline bug #791, the MCP bug #792).

Building the resolver here — with the integration-group mapping and unified
fields read straight from the DB — gives every no-app-context site one correct
construction to call instead of hand-rolling the catalog and accidentally
passing the empty-``{}`` provider. It can't *prevent* a future caller from
building ``FieldCatalog(db_adapter=...)`` directly (that still inherits the
unsafe ``group_mapping_provider=lambda: {}`` default), so reach for this helper
rather than constructing the catalog by hand. Callers that DO have an app
context (the API) should keep using the app-scoped, cache-primed
``current_app.field_resolver`` instead; this factory is only for the no-context
path.
"""

from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.fields.unified.repository import UnifiedFieldsRepository
from src.modules.integrations.repositories.integration_group_repository import (
    IntegrationGroupRepository,
)


def build_field_resolver(db_adapter) -> FieldResolver:
    """Return a FieldResolver whose catalog reads its integration-group mapping
    and unified fields directly from ``db_adapter`` — no Flask app context
    required. Use from the scheduler/pipeline/MCP/CLI; do not use where an
    app-scoped, cache-primed resolver is already available."""
    return FieldResolver(
        catalog=FieldCatalog(
            db_adapter=db_adapter,
            group_mapping_provider=IntegrationGroupRepository(db_adapter).get_group_mapping,
            unified_fields_provider=UnifiedFieldsRepository(db_adapter).list_for_catalog,
        )
    )
