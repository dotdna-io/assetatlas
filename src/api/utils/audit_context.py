# SPDX-License-Identifier: AGPL-3.0-or-later
"""Request-scoped semantic audit annotations (#1150).

Handlers call :func:`annotate_audit` to attach semantic context (which
resource, what action, extra details) to the in-flight request. The audit
trail middleware (``src.api.middleware.audit_middleware``) is the ONLY reader:
it merges the annotation into the single audit_trail row it already writes --
one row per event, never a second write.

Contract:

- **Last call wins.** Calling ``annotate_audit`` twice in one request replaces
  the earlier annotation wholesale; the middleware merges exactly one
  annotation per request.
- **No-op outside an app/request context.** Audit capture is fire-and-forget
  like the rest of the subsystem: outside a Flask context the call logs a
  warning and does nothing -- it never raises into the handler. (Non-request
  events get a direct writer in a later slice of #1150.)
- **Columns stay NULL when nothing annotates.** A request with no annotation
  produces the same row it always did, plus five NULLs.
"""

import logging

from flask import g, has_app_context

logger = logging.getLogger(__name__)

# Module-private key on flask.g. Read and written only through this module.
_G_KEY = '_audit_context_annotation'


def annotate_audit(resource_type, action, resource_id=None, details=None,
                   category='admin'):
    """Attach a semantic audit annotation to the current request.

    The audit middleware merges this into the audit_trail row it writes at the
    end of the request. Calling it again in the same request replaces the
    previous annotation (last call wins).

    Args:
        resource_type: Kind of resource acted on (e.g. 'integration', 'asset').
        action: Verb describing what was done (e.g. 'create', 'update').
        resource_id: Identifier of the specific resource (uuid, slug, int),
            or None when not applicable.
        details: Optional dict of extra context. The middleware runs it
            through the same recursive redaction as request bodies and caps
            the serialized form at 64 KB before storing it as jsonb.
        category: Coarse bucket for filtering; defaults to 'admin'.

    Outside an app/request context this is a no-op that logs a warning --
    audit capture must never break the request it describes.
    """
    if not has_app_context():
        logger.warning(
            "annotate_audit(%r, %r) called outside an app/request context; "
            "annotation dropped", resource_type, action,
        )
        return

    setattr(g, _G_KEY, {
        'category': category,
        'resource_type': resource_type,
        'resource_id': resource_id,
        'action': action,
        'details': details,
    })


def get_audit_annotation():
    """Return the current request's audit annotation dict, or None.

    Only the audit trail middleware calls this. Returns None when nothing
    annotated the request (and when called outside an app context, though the
    middleware never does).
    """
    if not has_app_context():
        return None
    return getattr(g, _G_KEY, None)
