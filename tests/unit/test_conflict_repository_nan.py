# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Test that ConflictRepository sanitizes NaN values before JSONB insert.

Regression test for GitHub issue #189.

NOTE: The ConflictRepository is deprecated (conflict detection now uses
alert rules at S20), but we keep this test to guard the NaN sanitization
logic in save_conflicts() which may still be called by external code.
"""

import json
import math
from unittest.mock import Mock, MagicMock, patch, call
from uuid import uuid4
from datetime import datetime

import pytest

from src.modules.asset_generation.repositories.conflict_repository import (
    ConflictRepository,
)


class _Conflict:
    """Minimal stand-in for the removed Conflict domain class."""

    def __init__(self, evidence):
        self.id = uuid4()
        self.generation_run_id = uuid4()
        self.conflict_type = "overlapping_components"
        self.severity = "high"
        self.asset_id = uuid4()
        self.component = "server01"
        self.conflicting_asset_id = uuid4()
        self.evidence = evidence
        self.status = "open"
        self.created_at = datetime.now()


def _make_conflict(evidence: dict) -> _Conflict:
    """Create a mock Conflict with the given evidence dict."""
    return _Conflict(evidence=evidence)


class TestConflictRepositoryNanSanitization:
    """Verify evidence dicts are sanitized before JSONB insert."""

    def test_nan_in_evidence_replaced_with_null(self):
        """NaN float in evidence must become None before DB insert (issue #189)."""
        conflict = _make_conflict({
            "overlap_percentage": float("nan"),
            "shared_components": ["server01"],
        })

        cursor = MagicMock()
        conn = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = Mock(return_value=(cursor, conn))
        ctx.__exit__ = Mock(return_value=False)

        db_adapter = Mock()
        db_adapter.conn_manager.get_cursor.return_value = ctx

        repo = ConflictRepository(db_adapter)
        repo.save_conflicts([conflict])

        # Extract the evidence parameter passed to execute
        args = cursor.execute.call_args
        params = args[0][1]  # positional params tuple
        evidence_json = params[7]  # 8th param is evidence

        parsed = json.loads(evidence_json)
        assert parsed["overlap_percentage"] is None
        assert parsed["shared_components"] == ["server01"]

    def test_nested_nan_in_evidence_replaced(self):
        """Nested NaN values in evidence should also be sanitized."""
        conflict = _make_conflict({
            "overlap_percentage": 75.0,
            "details": {
                "score": float("nan"),
                "items": [1.0, float("nan"), 3.0],
            },
        })

        cursor = MagicMock()
        conn = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = Mock(return_value=(cursor, conn))
        ctx.__exit__ = Mock(return_value=False)

        db_adapter = Mock()
        db_adapter.conn_manager.get_cursor.return_value = ctx

        repo = ConflictRepository(db_adapter)
        repo.save_conflicts([conflict])

        args = cursor.execute.call_args
        evidence_json = args[0][1][7]
        parsed = json.loads(evidence_json)

        assert parsed["overlap_percentage"] == 75.0
        assert parsed["details"]["score"] is None
        assert parsed["details"]["items"] == [1.0, None, 3.0]

    def test_clean_evidence_passes_through(self):
        """Evidence without NaN should pass through unchanged."""
        conflict = _make_conflict({
            "overlap_percentage": 50.0,
            "shared_components": ["mac01"],
        })

        cursor = MagicMock()
        conn = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = Mock(return_value=(cursor, conn))
        ctx.__exit__ = Mock(return_value=False)

        db_adapter = Mock()
        db_adapter.conn_manager.get_cursor.return_value = ctx

        repo = ConflictRepository(db_adapter)
        repo.save_conflicts([conflict])

        args = cursor.execute.call_args
        evidence_json = args[0][1][7]
        parsed = json.loads(evidence_json)

        assert parsed == {
            "overlap_percentage": 50.0,
            "shared_components": ["mac01"],
        }
