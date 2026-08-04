# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared audit vocabulary (#1150, slice 2).

The canonical spellings of the resource types and actions that
:class:`~src.api.utils.audit_logger.AuditLogger` call sites emit. Both audit
sinks consume these strings as opaque text:

- ``logs/audit.log`` — a compliance artifact whose byte format is frozen
  until slice 3 of #1150. Enum VALUES are therefore exactly the strings the
  call sites already emitted as free text; this module is a spelling
  registry, not a rename.
- the ``audit_trail`` semantic columns — populated automatically:
  ``AuditLogger.log_audit_event`` calls
  :func:`~src.api.utils.audit_context.annotate_audit` with the same values
  it writes to the file, so every call site that logs also annotates the
  in-flight request.

One deliberate normalization: there is no ``TOGGLE`` action. Toggle
endpoints record ``ENABLE`` or ``DISABLE`` chosen by the resulting state —
the toggle handler always knows which way the rule landed.

Known limitation (accepted from the slice-1 mechanism; do not redesign
here): ``annotate_audit`` is last-call-wins within a request, so a handler
that logs two audit events in one request carries only the later annotation
into ``audit_trail``. audit.log keeps both events.
"""

from enum import Enum


class AuditResource(str, Enum):
    """Resource types named by AuditLogger call sites.

    Values MUST stay byte-identical to the strings emitted before this
    registry existed — audit.log is a frozen compliance artifact. New
    resource types join only with a real call site that emits them.
    """

    INTEGRATION = 'integration'
    INTEGRATION_LOGO = 'integration_logo'
    TAG_RULE = 'tag_rule'
    POSTURE_RULE = 'posture_rule'
    ENRICHMENT_DATASET = 'enrichment_dataset'
    APPLICATION_SETTING = 'application_setting'


class AuditAction(str, Enum):
    """Actions named by AuditLogger call sites. Same freeze as AuditResource.

    The RBAC middleware composes its final action as
    ``f"{action}_{allowed|denied}"`` — the members below are the bases it
    is given, not the composed strings.
    """

    CREATE = 'create'
    UPDATE = 'update'
    DELETE = 'delete'
    ENABLE = 'enable'
    DISABLE = 'disable'
    APPLY = 'apply'
    EXECUTE = 'execute'
    EXECUTE_ALL = 'execute_all'
    REORDER = 'reorder'
    ACCESS = 'access'
    ACCESS_OWN_RESOURCE = 'access_own_resource'
    ADMIN_ACCESS_USER_RESOURCE = 'admin_access_user_resource'
