# SPDX-License-Identifier: AGPL-3.0-or-later
"""Metrics forwarding runtime config admin API (#922 Phase 2b).

Backs the Settings -> Monitoring config UI (Phase 3): the customer Prometheus-native
remote_write target for the opt-in metrics-forwarder agent. The remote_write secret is
encrypted at rest and masked in every response (a ``has_secret`` boolean, never the
value). Saving re-renders the agent's shared config file and best-effort reloads the
agent via POST /-/reload; an unreachable agent / disabled `forwarding` profile degrades
gracefully (persist + clear status, no 500).

Endpoints (all admin / super_admin only):
- GET       /api/metrics-forwarding/config       - read current config (secret masked)
- PUT/POST  /api/metrics-forwarding/config       - set config (+ render + reload)
- POST      /api/metrics-forwarding/config/test   - best-effort endpoint reachability check
"""

import logging
from typing import Any, Dict, Literal, Optional

from flask import Blueprint, current_app
from flask_jwt_extended import jwt_required, current_user
from pydantic import BaseModel, Field

from src.api.middleware.error_handler import handle_errors, ValidationError
from src.api.utils.responses import response_wrapper
from src.api.utils.validation import validate_request
from src.auth.middleware.rbac import require_admin

logger = logging.getLogger(__name__)

metrics_forwarding_bp = Blueprint(
    "metrics_forwarding", __name__, url_prefix="/api/metrics-forwarding"
)


class ForwardingConfigUpdate(BaseModel):
    endpoint_url: Optional[str] = None
    # Constrained so a typo is rejected at validation time (400) rather than reaching
    # the service; the service re-validates for non-HTTP callers.
    auth_type: Literal["none", "bearer", "basic"] = "none"
    # Write-only: omit / blank to keep the stored secret; the API never echoes it back.
    auth_secret: Optional[str] = None
    username: Optional[str] = None
    external_labels: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = False


@metrics_forwarding_bp.route("/config", methods=["GET"])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_config():
    return current_app.metrics_forwarding_service.get_config()


@metrics_forwarding_bp.route("/config", methods=["PUT", "POST"])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(ForwardingConfigUpdate)
@response_wrapper
def set_config(validated_data: ForwardingConfigUpdate):
    try:
        result = current_app.metrics_forwarding_service.set_config(
            endpoint_url=validated_data.endpoint_url,
            auth_type=validated_data.auth_type,
            auth_secret=validated_data.auth_secret,
            username=validated_data.username,
            external_labels=validated_data.external_labels,
            enabled=validated_data.enabled,
            updated_by=(current_user or {}).get("username"),
        )
    except ValueError as e:
        raise ValidationError(str(e))
    return result


@metrics_forwarding_bp.route("/config/test", methods=["POST"])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def test_connection():
    return current_app.metrics_forwarding_service.test_connection()
