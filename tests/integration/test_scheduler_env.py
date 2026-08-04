# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for scheduler worker environment validation.

These are effectively unit tests (no database needed) that verify the
SchedulerWorker._validate_environment() method rejects invalid configurations.
"""

import os
from unittest.mock import patch

import pytest

from src.scheduler_worker import SchedulerWorker


@pytest.mark.integration
class TestSchedulerEnvironmentValidation:
    """Validate that the scheduler rejects bad environment configs."""

    def test_exits_when_scheduler_mode_not_worker(self):
        """When SCHEDULER_MODE != 'worker', _validate_environment should sys.exit(1)."""
        worker = SchedulerWorker()
        env = {"SCHEDULER_MODE": "embedded", "DATABASE_URL": "postgresql://x"}
        with patch.dict(os.environ, env, clear=False), \
             pytest.raises(SystemExit) as exc_info:
            worker._validate_environment()
        assert exc_info.value.code == 1

    def test_exits_when_scheduler_mode_missing(self):
        """When SCHEDULER_MODE is not set, _validate_environment should sys.exit(1)."""
        worker = SchedulerWorker()
        env = {"DATABASE_URL": "postgresql://x"}
        with patch.dict(os.environ, env, clear=False), \
             patch.dict(os.environ, {"SCHEDULER_MODE": ""}, clear=False), \
             pytest.raises(SystemExit) as exc_info:
            worker._validate_environment()
        assert exc_info.value.code == 1

    def test_exits_when_database_url_missing(self):
        """When DATABASE_URL is not set, _validate_environment should sys.exit(1)."""
        worker = SchedulerWorker()
        # Remove DATABASE_URL from env entirely
        env_overrides = {"SCHEDULER_MODE": "worker"}
        with patch.dict(os.environ, env_overrides, clear=False), \
             patch.dict(os.environ, {}, clear=False) as patched_env:
            # Explicitly remove DATABASE_URL
            patched_env.pop("DATABASE_URL", None)
            with pytest.raises(SystemExit) as exc_info:
                worker._validate_environment()
            assert exc_info.value.code == 1

    def test_passes_when_env_valid(self):
        """When both SCHEDULER_MODE=worker and DATABASE_URL are set, no exit."""
        worker = SchedulerWorker()
        env = {"SCHEDULER_MODE": "worker", "DATABASE_URL": "postgresql://user:pass@host/db"}
        with patch.dict(os.environ, env, clear=False):
            # Should not raise
            worker._validate_environment()
