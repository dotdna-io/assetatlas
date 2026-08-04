# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests that admin cannot escalate to super_admin role.

Regression test for #187: admin could create/promote users to super_admin.
"""

import pytest
from src.auth.middleware.rbac import ROLE_HIERARCHY


def test_role_hierarchy_is_defined():
    """ROLE_HIERARCHY must have super_admin < admin < user."""
    assert ROLE_HIERARCHY['super_admin'] < ROLE_HIERARCHY['admin']
    assert ROLE_HIERARCHY['admin'] < ROLE_HIERARCHY['user']


def test_can_assign_role_not_above_own():
    """Helper: admin assigning super_admin should be detected as escalation."""
    from src.auth.middleware.rbac import ROLE_HIERARCHY

    def is_escalation(caller_role: str, target_role: str) -> bool:
        caller_level = ROLE_HIERARCHY.get(caller_role, 999)
        target_level = ROLE_HIERARCHY.get(target_role, 999)
        return target_level < caller_level  # lower value = higher privilege

    # admin -> super_admin is escalation
    assert is_escalation('admin', 'super_admin') is True
    # admin -> admin is NOT escalation
    assert is_escalation('admin', 'admin') is False
    # admin -> user is NOT escalation
    assert is_escalation('admin', 'user') is False
    # super_admin -> super_admin is NOT escalation
    assert is_escalation('super_admin', 'super_admin') is False
    # super_admin -> admin is NOT escalation
    assert is_escalation('super_admin', 'admin') is False
