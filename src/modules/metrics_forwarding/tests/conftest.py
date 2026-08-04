# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test bootstrap for the colocated metrics_forwarding tests.

These tests live outside the ``tests/`` tree, so the root ``tests/conftest.py`` is not
an ancestor and its environment setup does not apply when this directory is collected on
its own (e.g. ``pytest src/modules/metrics_forwarding/tests``). The repository imports
``src.modules.integrations.shared.encryption``, which requires ``ASSETATLAS_ENCRYPTION_KEY``
at import time with no fallback — so set the same deterministic, clearly-fake test key
here (mirrors the root conftest / alert_delivery conftest) to keep these tests
self-sufficient.
"""

import os

from cryptography.fernet import Fernet

os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@localhost:5434/assetatlas_test"
)
os.environ.setdefault(
    "ASSETATLAS_ENCRYPTION_KEY",
    Fernet.generate_key().decode(),  # fresh per-run; never committed
)
