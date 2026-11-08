# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guards the conftest healing of mock attributes that ``monkeypatch`` deletes.

pytest >= 9.2 reads a monkeypatched instance attribute's old value from
``target.__dict__``.  A MagicMock keeps auto-created children in
``_mock_children``, so it reads as "absent", and the undo ``delattr``s the name
-- after which the mock raises ``AttributeError`` for it forever.  On the
session-scoped ``blueprint_app`` that failed every later test touching the same
attribute.  See ``_undelete_mock_attrs`` / ``_heal_service_mock_attrs`` in
``tests/conftest.py``.
"""

from unittest.mock import MagicMock

import pytest

from tests.conftest import _undelete_mock_attrs


def test_undelete_restores_attr_removed_by_monkeypatch_undo():
    svc = MagicMock(name="svc")
    mp = pytest.MonkeyPatch()
    mp.setattr(svc, "set", lambda *a, **k: None)
    mp.undo()

    _undelete_mock_attrs(svc)

    svc.set.return_value = (True, None)  # AttributeError if still deleted
    assert svc.set() == (True, None)


def test_undelete_reaches_nested_children():
    svc = MagicMock(name="svc")
    mp = pytest.MonkeyPatch()
    mp.setattr(svc.inner, "leaf", lambda: None)
    mp.undo()

    _undelete_mock_attrs(svc)

    assert svc.inner.leaf() is not None


def test_undelete_ignores_non_mocks():
    _undelete_mock_attrs(None)
    _undelete_mock_attrs(object())


# The next two tests are order-dependent on purpose (definition order is the
# run order): the first leaves the shared mock's attribute for monkeypatch to
# delete, the second proves the autouse fixture healed it in between.


def test_a_monkeypatching_a_shared_service_mock(blueprint_app, monkeypatch):
    monkeypatch.setattr(
        blueprint_app.chat_providers_service,
        "get_active_provider",
        lambda: "anthropic",
    )
    assert blueprint_app.chat_providers_service.get_active_provider() == "anthropic"


def test_b_shared_service_mock_attr_survives_the_previous_test(blueprint_app):
    providers = blueprint_app.chat_providers_service
    providers.get_active_provider.return_value = "openai"  # raised pre-fix on 9.2.0
    assert providers.get_active_provider() == "openai"
