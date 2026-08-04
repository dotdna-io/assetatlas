# SPDX-License-Identifier: AGPL-3.0-or-later
"""Teams bot bridge endpoints + linking confirmation page.

Four JSON endpoints (called by the bridge container or the user's
browser via the linking page) plus two server-rendered HTML pages:

  POST /api/auth/teams-link/initiate    [service-key]
  GET  /teams/link?token=…              [public — JS handles signed-out state]
  POST /api/auth/teams-link/confirm     [user JWT]
  POST /api/auth/teams-bridge/exchange  [service-key]
  GET  /teams/linked                    [public success page]
"""

from __future__ import annotations

import logging
from datetime import timedelta

from flask import Blueprint, current_app, render_template, request
from flask_jwt_extended import (create_access_token, get_jwt_identity,
                                jwt_required)

from src.api.middleware.error_handler import (BadRequestError, NotFoundError,
                                               handle_errors)
from src.api.utils.responses import response_wrapper
from src.api.utils.validation import validate_request
from src.auth.api_key_auth import require_service_api_key
from src.auth.services.audit_service import AuditService
from src.modules.integrations.shared.encryption import (decrypt,
                                                          is_encrypted)
from src.modules.teams_link.models import (ExchangeRequest,
                                            LinkConfirmRequest,
                                            LinkInitiateRequest)
from src.modules.teams_link.service import LinkTokenError

logger = logging.getLogger(__name__)

_BRIDGE_JWT_TTL_SECONDS = 300

teams_bot_bp = Blueprint(
    "teams_bot", __name__,
    template_folder="../templates",
)


# ============================================================
# POST /api/auth/teams-link/initiate
# ============================================================

@teams_bot_bp.route("/api/auth/teams-link/initiate", methods=["POST"])
@require_service_api_key("ASSETATLAS_BRIDGE_API_KEY")
@handle_errors
@validate_request(LinkInitiateRequest)
@response_wrapper
def initiate_link(validated_data: LinkInitiateRequest):
    svc = current_app.teams_link_service
    url, expires_at = svc.mint_link_token(
        aad_object_id=validated_data.aad_object_id,
        tenant_id=validated_data.tenant_id,
        display_name=validated_data.display_name,
    )
    return {"link_url": url, "expires_at": expires_at.isoformat()}


# ============================================================
# GET /teams/link?token=…
# ============================================================

@teams_bot_bp.route("/teams/link", methods=["GET"])
def link_page():
    """Server-rendered confirmation page.

    We don't gate this on a server-side JWT check — JWTs live in
    localStorage on the React UI's origin and aren't sent on a regular
    browser page load. The page's JS reads `access_token` from
    localStorage and handles the "not signed in" case in-place, so the
    user doesn't get bounced through a redirect that drops `return_to`.
    """
    token = request.args.get("token", "")
    if not token:
        return render_template("teams_link_error.html",
                               reason="Missing token."), 400

    svc = current_app.teams_link_service
    try:
        claims = svc.verify_link_token(token)
    except LinkTokenError as exc:
        logger.info("Rejected link token: %s", exc)
        return render_template("teams_link_error.html",
                               reason=str(exc)), 400

    return render_template(
        "teams_link_confirm.html",
        token=token,
        display_name=claims["display_name"],
    )


# ============================================================
# POST /api/auth/teams-link/confirm
# ============================================================

@teams_bot_bp.route("/api/auth/teams-link/confirm", methods=["POST"])
@jwt_required()
@handle_errors
@validate_request(LinkConfirmRequest)
@response_wrapper
def confirm_link(validated_data: LinkConfirmRequest):
    user_id = get_jwt_identity()
    svc = current_app.teams_link_service
    try:
        claims = svc.consume_link_token(
            validated_data.token,
            user_id=user_id,
            client_ip=request.remote_addr,
        )
    except LinkTokenError as exc:
        logger.info("Confirm rejected: %s", exc)
        raise NotFoundError(str(exc)) from exc

    audit_service = AuditService(current_app.connection_manager)
    audit_service.log_auth(
        action="teams_account_linked",
        success=True,
        user_id=user_id,
        ip=request.remote_addr,
        metadata={
            "tenant_id": claims["tenant_id"],
            "aad_object_id": claims["aad_object_id"],
            "display_name": claims["display_name"],
        },
    )
    return {"status": "linked"}


# ============================================================
# POST /api/auth/teams-bridge/exchange
# ============================================================

@teams_bot_bp.route("/api/auth/teams-bridge/exchange", methods=["POST"])
@require_service_api_key("ASSETATLAS_BRIDGE_API_KEY")
@handle_errors
@validate_request(ExchangeRequest)
@response_wrapper
def exchange_token(validated_data: ExchangeRequest):
    svc = current_app.teams_link_service
    link = svc.find_link(
        validated_data.aad_object_id, validated_data.tenant_id,
    )
    if link is None:
        raise NotFoundError("not_linked")

    access_token = create_access_token(
        identity=str(link.user_id),
        expires_delta=timedelta(seconds=_BRIDGE_JWT_TTL_SECONDS),
        additional_claims={"src": "teams_bridge"},
    )
    return {
        "access_token": access_token,
        "expires_in": _BRIDGE_JWT_TTL_SECONDS,
        "user_id": str(link.user_id),
    }


# ============================================================
# GET /api/auth/teams-bridge/conversation
# POST /api/auth/teams-bridge/conversation
# DELETE /api/auth/teams-bridge/conversation
# ============================================================

@teams_bot_bp.route("/api/auth/teams-bridge/conversation",
                    methods=["GET"])
@require_service_api_key("ASSETATLAS_BRIDGE_API_KEY")
@handle_errors
@response_wrapper
def get_conversation_session():
    conv = request.args.get("conversation_id", "")
    if not conv:
        raise BadRequestError("conversation_id required")
    svc = current_app.teams_link_service
    row = svc.repo.find_conversation_session(conv)
    if row is None:
        raise NotFoundError("not_found")
    return {
        "session_id": str(row.session_id),
        "user_id": str(row.user_id),
        "last_active_at": row.last_active_at.isoformat(),
    }


@teams_bot_bp.route("/api/auth/teams-bridge/conversation",
                    methods=["POST"])
@require_service_api_key("ASSETATLAS_BRIDGE_API_KEY")
@handle_errors
@response_wrapper
def upsert_conversation_session():
    body = request.get_json(force=True) or {}
    conv = body.get("conversation_id")
    user_id = body.get("user_id")
    session_id = body.get("session_id")
    if not all([conv, user_id, session_id]):
        raise BadRequestError(
            "conversation_id, user_id, session_id all required",
        )
    svc = current_app.teams_link_service
    row = svc.repo.create_conversation_session(
        conversation_id=conv, user_id=user_id, session_id=session_id,
    )
    return {"session_id": str(row.session_id)}


@teams_bot_bp.route("/api/auth/teams-bridge/conversation",
                    methods=["DELETE"])
@require_service_api_key("ASSETATLAS_BRIDGE_API_KEY")
@handle_errors
@response_wrapper
def delete_conversation_session():
    conv = request.args.get("conversation_id", "")
    if not conv:
        raise BadRequestError("conversation_id required")
    svc = current_app.teams_link_service
    svc.repo.delete_conversation_session(conv)
    return {"deleted": True}


# ============================================================
# GET /api/auth/teams-bridge/config
# ============================================================

@teams_bot_bp.route("/api/auth/teams-bridge/config", methods=["GET"])
@require_service_api_key("ASSETATLAS_BRIDGE_API_KEY")
@handle_errors
@response_wrapper
def bridge_config():
    """Return the live Teams config for the bridge container.

    Called by the bridge on boot and every 30s. The bot password is
    decrypted here before being returned (encrypted-at-rest is fine
    for the DB; the bridge needs cleartext to authenticate to
    Microsoft Bot Framework). Service-API-key gating keeps the
    endpoint off the public surface.
    """
    logger.debug("bridge_config fetched by bridge")
    sm = current_app.settings_manager
    raw_password = sm.get('teams', 'bot_app_password') or ''
    if isinstance(raw_password, str) and is_encrypted(raw_password):
        bot_app_password = decrypt(raw_password)
    else:
        bot_app_password = raw_password
    return {
        'enabled': bool(sm.get('teams', 'enabled')),
        'bot_app_id': sm.get('teams', 'bot_app_id'),
        'bot_app_password': bot_app_password,
        'bot_app_tenant_id': sm.get('teams', 'bot_app_tenant_id'),
    }


# ============================================================
# GET /teams/linked
# ============================================================

@teams_bot_bp.route("/teams/linked", methods=["GET"])
def linked_page():
    return render_template("teams_link_done.html")


