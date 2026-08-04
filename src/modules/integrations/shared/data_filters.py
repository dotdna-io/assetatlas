# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Data-filter evaluation shared between connectors and the wizard preview.

This is the single source of truth for include/exclude filter semantics. It is
used both at ingest time (``BaseConnector._should_include_record``) and by the
integration wizard's server-side ``/preview/filter-count`` endpoint. The logic
previously lived as duplicated copies in ``base_connector.py`` and the preview
blueprint; keep it here so the two paths cannot drift apart.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_nested_value(record: dict, field_path: str) -> Any:
    """Resolve a dot-notation path in a record, preferring a literal key.

    SentinelOne PowerQuery (and other SIEM-passthrough sources) return columns
    like ``device.hostname`` as flat top-level keys, not nested objects, so the
    literal key is preferred before falling back to nested traversal.
    """
    if isinstance(record, dict) and field_path in record:
        return record[field_path]
    current = record
    for key in field_path.split('.'):
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current


def evaluate_condition(value: Any, operator: str, expected: Any = None) -> bool:
    """Evaluate a single filter condition via the operator registry.

    Fail-OPEN (§6.2): an unresolved operator keeps the record (returns True),
    matching ingest's intentional permissiveness — never fail-closed here.
    """
    from src.modules.fields.operators import (
        resolve as resolve_operator, OperatorResolutionError,
    )
    try:
        op = resolve_operator(operator)
    except OperatorResolutionError:
        logger.warning("Unknown filter operator: %s", operator)
        return True
    return op.match(value, expected, None)


def matches_filters(record: dict, filters: list) -> bool:
    """Check if a record passes all data filters (AND logic)."""
    for f in filters:
        value = get_nested_value(record, f['field'])
        if not evaluate_condition(value, f['operator'], f.get('value')):
            return False
    return True
