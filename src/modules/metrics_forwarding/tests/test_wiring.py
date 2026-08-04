# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock

from src.modules.metrics_forwarding.service import MetricsForwardingService
from src.modules.metrics_forwarding.wiring import build_metrics_forwarding_service


def test_build_returns_service_with_repository():
    svc = build_metrics_forwarding_service(MagicMock())
    assert isinstance(svc, MetricsForwardingService)
    assert svc.repo is not None


def test_build_passes_dir_and_reload_overrides():
    svc = build_metrics_forwarding_service(
        MagicMock(), config_dir="/tmp/fwd", reload_url="http://fwd:9090/-/reload"
    )
    assert svc.config_dir == "/tmp/fwd"
    assert svc.reload_url == "http://fwd:9090/-/reload"
