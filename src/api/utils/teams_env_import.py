# SPDX-License-Identifier: AGPL-3.0-or-later
"""One-time import of legacy TEAMS_* env vars into the teams settings category.

Existing installs (pre-Settings UI) hold the bot credentials in .env. On the
first boot of the new code, if the DB has no bot_app_id but the env supplies
the full trio, copy the values + flip enabled=True. Subsequent boots find
the DB populated and short-circuit. The .env values are kept (commented)
for one release cycle to support rollback.

Encryption + audit-redaction:
  This helper bypasses the Flask blueprint layer where pre_store and
  audit_redact normally run, so it has to apply both inline:
    - pre_store('bot_app_password', secret) -> encrypted ciphertext
    - after each set(), UPDATE settings_audit_log to '<redacted>' for any
      key whose hook flags audit_redact = True.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from src.api.blueprints.settings.category_hooks import CATEGORY_HOOKS

logger = logging.getLogger(__name__)


def import_legacy_teams_env_once(settings_manager: Any) -> bool:
    """Run the one-time env→DB import. Returns True iff a write happened.

    Idempotent: short-circuits when the DB already holds a non-empty
    bot_app_id, regardless of what env contains. Conservative: requires
    ALL three env vars to be set; partial env produces no write (the
    operator can finish setup via the UI).
    """
    env_id = os.environ.get('TEAMS_BOT_APP_ID', '').strip()
    env_secret = os.environ.get('TEAMS_BOT_APP_PASSWORD', '').strip()
    env_tenant = os.environ.get('TEAMS_BOT_APP_TENANT_ID', '').strip()

    if not (env_id and env_secret and env_tenant):
        return False

    existing = (settings_manager.get('teams', 'bot_app_id') or '').strip()
    if existing:
        logger.debug(
            "Teams DB already populated (bot_app_id=%s...); "
            "skipping env import.", existing[:8],
        )
        return False

    logger.info(
        "Importing legacy TEAMS_* env vars into the teams settings category."
    )

    hooks = CATEGORY_HOOKS['teams']
    encrypted_secret = hooks.pre_store('bot_app_password', env_secret)

    user_kwargs = dict(
        user_id='',  # System action — no real user.
        username='system:env-migration',
        reason='one-time import from .env at first boot of '
               'the Teams Settings UI feature',
    )

    settings_manager.set('teams', 'bot_app_id', env_id, **user_kwargs)
    settings_manager.set(
        'teams', 'bot_app_password', encrypted_secret, **user_kwargs,
    )
    settings_manager.set('teams', 'bot_app_tenant_id', env_tenant, **user_kwargs)
    settings_manager.set('teams', 'enabled', True, **user_kwargs)

    _redact_audit_rows(settings_manager, hooks)
    return True


def _redact_audit_rows(settings_manager: Any, hooks: Any) -> None:
    """Overwrite audit-log rows for any keys flagged as redactable.

    Mirrors the UPDATE pattern in src/api/blueprints/settings/settings_routes.py
    (the update_setting handler). Failure here is logged but does not roll
    back the import; an operator can review settings_audit_log directly if
    needed.
    """
    redact_keys = [
        k for k in (
            'bot_app_id', 'bot_app_password', 'bot_app_tenant_id', 'enabled',
        )
        if hooks.audit_redact(k)
    ]
    if not redact_keys:
        return

    try:
        with settings_manager.connection_manager.get_cursor() as (cur, conn):
            for key in redact_keys:
                cur.execute(
                    """
                    UPDATE settings_audit_log
                    SET old_value = '<redacted>', new_value = '<redacted>'
                    WHERE id = (
                        SELECT id FROM settings_audit_log
                        WHERE category = 'teams' AND key = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT 1
                    )
                    """,
                    (key,),
                )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "audit-log redaction failed for teams env import: %s", exc,
        )
