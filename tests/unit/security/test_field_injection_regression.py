# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression suite for the #455 SQL-injection CVE via field passthrough.

Background
----------
Issue #455 was an SQL-injection vulnerability caused by a passthrough in the
legacy ``get_db_column()`` helper: user-supplied field identifiers were
interpolated into SQL with no validation, letting an attacker smuggle SQL
fragments like ``COALESCE(id::text, (SELECT current_setting('my.secret')))``
through the field parameter on every endpoint that accepted a field name.

Chunk 2 of the remediation replaced the passthrough with a typed ``FieldId``
discriminated union and a ``FieldResolver`` that rejects anything not present
in the live catalog (``InvalidFieldError``).  Blueprints wrap that error as a
400/422 response.

This suite exercises the boundary with the exact #455 PoC payload plus two
more hostile payloads across every endpoint that accepts a ``FieldId``, to
prove the injection is dead at the request layer.

Scope note
----------
The ``TestDatabaseUnchanged`` assertion lives in the integration suite
(Task 25) — unit-test services are MagicMocks so they don't hit a real DB,
and asserting ``execute_query.call_count == 0`` from here is fragile (the
mock still gets called for catalog/auth regardless of what the route does).
See ``tests/integration/test_field_id_end_to_end.py`` for the companion
DB-side-effect assertions.
"""

import json
from unittest.mock import MagicMock

import pytest

from src.modules.fields import FieldCatalog, FieldResolver, InvalidFieldError
from src.modules.fields.models import (
    ExtendedAttributeField,
    StaticField,
    TagField,
)


# PAYLOAD_STATIC_COALESCE is the literal payload from the #455 report;
# the other two exercise the ExtendedAttributeField / TagField branches
# of the discriminated union.

PAYLOAD_STATIC_COALESCE = {
    "kind": "static",
    "name": "COALESCE(id::text, (SELECT current_setting('my.secret')))",
}

PAYLOAD_EXT_DROP_TABLE = {
    "kind": "extended_attribute",
    "source": "a'; DROP TABLE assets;--",
    "attribute": "x",
}

PAYLOAD_TAG_DELETE_FROM = {
    "kind": "tag",
    "name": "'; DELETE FROM assets;--",
}


_ALL_PAYLOADS = [
    pytest.param(PAYLOAD_STATIC_COALESCE, id="static-COALESCE-current_setting"),
    pytest.param(PAYLOAD_EXT_DROP_TABLE, id="ext-drop-table"),
    pytest.param(PAYLOAD_TAG_DELETE_FROM, id="tag-delete-from"),
]


def _body_snippet(resp) -> str:
    """Return a short representation of the response body for assertion messages."""
    try:
        return json.dumps(resp.get_json())[:400]
    except Exception:
        return (resp.data or b"").decode(errors="replace")[:400]


class TestResolverRejectsInjection:
    """``FieldResolver.resolve`` raises ``InvalidFieldError`` for every
    hostile ``FieldId`` — nothing reaches SQL construction.

    Catalog is built from an empty db_adapter (no extended_attributes, no tags,
    no posture categories).  The static catalog is still populated from
    ``STATIC_FIELDS``; none of the payloads listed above match any entry there.
    """

    @pytest.fixture
    def resolver(self):
        db = MagicMock()
        db.execute_query.return_value = []
        return FieldResolver(catalog=FieldCatalog(db_adapter=db))

    def test_static_coalesce_current_setting_raises(self, resolver):
        """#455 PoC — ``static`` name containing a SQL expression."""
        malicious = StaticField(
            kind="static",
            name="COALESCE(id::text, (SELECT current_setting('my.secret')))",
        )
        with pytest.raises(InvalidFieldError):
            resolver.resolve(malicious)

    def test_extended_attribute_drop_table_raises(self, resolver):
        """``extended_attribute`` source carrying a statement-terminator payload."""
        malicious = ExtendedAttributeField(
            kind="extended_attribute",
            source="a'; DROP TABLE assets;--",
            attribute="x",
        )
        with pytest.raises(InvalidFieldError):
            resolver.resolve(malicious)

    def test_tag_delete_from_raises(self, resolver):
        """``tag`` name carrying a DELETE statement."""
        malicious = TagField(kind="tag", name="'; DELETE FROM assets;--")
        with pytest.raises(InvalidFieldError):
            resolver.resolve(malicious)


# POST endpoints that accept FieldId payloads all share the same
# rejection shape: the service raises InvalidFieldError, the route
# decorator wraps it, the client sees 400. Parametrized via
# _ENDPOINT_SPECS so adding a new endpoint is one tuple.

_INJECTION_ERROR_MSG = "malicious field did not match any catalog entry"


def _tag_rules_create_body(payload):
    return {
        "name": "injection-attempt",
        "type": "classification",
        "enabled": True,
        "target_filters": [
            {"field_id": payload, "operator": "equals", "value": "x"},
        ],
        "tags": {"label": "injected", "color": "#ff0000"},
    }


def _tag_rules_preview_body(payload):
    return {
        "target_filters": [
            {"field_id": payload, "operator": "equals", "value": "x"},
        ],
    }


def _posture_sub_rule_body(payload):
    return {
        "name": "injection-attempt",
        "target_filters": [
            {"field_id": payload, "operator": "equals", "value": "x"},
        ],
        "compliance_criteria": {
            "logic": "any",
            "conditions": [
                {"field_id": payload, "operator": "equals", "value": "y"},
            ],
        },
    }


def _posture_preview_body(payload):
    return {
        "target_filters": [
            {"field_id": payload, "operator": "equals", "value": "x"},
        ],
        "compliance_criteria": {
            "logic": "any",
            "conditions": [
                {"field_id": payload, "operator": "equals", "value": "y"},
            ],
        },
    }


# tag-rules/preview sets two mocks because the route branches on payload
# shape (count vs compliance). The rest need a single mock each.
_ENDPOINT_SPECS = [
    pytest.param(
        {
            "url": "/api/tag-rules",
            "body_builder": _tag_rules_create_body,
            "service_calls": [("rule_mgmt_service", "create_rule")],
        },
        id="tag-rules-create",
    ),
    pytest.param(
        {
            "url": "/api/tag-rules/preview",
            "body_builder": _tag_rules_preview_body,
            "service_calls": [
                ("preview_service", "count_matching_assets"),
                ("preview_service", "preview_compliance"),
            ],
        },
        id="tag-rules-preview",
    ),
    pytest.param(
        {
            "url": "/api/posture-rules/secured/sub-rules",
            "body_builder": _posture_sub_rule_body,
            "service_calls": [("posture_sub_rule_mgmt_service", "create")],
        },
        id="posture-sub-rule-create",
    ),
    pytest.param(
        {
            "url": "/api/posture-rules/sub-rules/preview",
            "body_builder": _posture_preview_body,
            "service_calls": [("posture_preview_service", "preview")],
        },
        id="posture-preview",
    ),
]


class TestEndpointsRejectInjection:
    """Every endpoint that accepts a FieldId rejects malicious payloads with
    HTTP 400. The InvalidFieldError raised inside the route is wrapped by the
    handle_errors decorator as APIValidationError (status 400); the GET
    query-string endpoints reach the same 400 via Pydantic query-param
    validation failure."""

    @pytest.mark.parametrize("endpoint_spec", _ENDPOINT_SPECS)
    @pytest.mark.parametrize("payload", _ALL_PAYLOADS)
    def test_post_endpoint_rejects(
        self, payload, endpoint_spec, client, authed_headers, blueprint_app
    ):
        for service_attr, method_name in endpoint_spec["service_calls"]:
            service = getattr(blueprint_app, service_attr)
            getattr(service, method_name).side_effect = InvalidFieldError(
                _INJECTION_ERROR_MSG
            )

        resp = client.post(
            endpoint_spec["url"],
            json=endpoint_spec["body_builder"](payload),
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400, (
            f"expected 400, got {resp.status_code}: {_body_snippet(resp)}"
        )

    @pytest.mark.parametrize("payload", _ALL_PAYLOADS)
    def test_charts_query_rejects(self, payload, client, authed_headers, blueprint_app):
        """Distribution chart query with a malicious dimension returns 400.

        The route instantiates QueryBuilder with current_app.field_resolver;
        mocking its resolve() is the correct seam here because the builder
        calls resolver.resolve(...) before any service call.
        """
        blueprint_app.field_resolver.resolve.side_effect = InvalidFieldError(
            _INJECTION_ERROR_MSG
        )

        body = {
            "queryType": "distribution",
            "from": {"table": "assets"},
            "select": {"dimension": payload},
        }
        resp = client.post(
            "/api/charts/query",
            json=body,
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400, (
            f"expected 400, got {resp.status_code}: {_body_snippet(resp)}"
        )

    def test_assets_values_rejects_encoded_injection(
        self, client, authed_headers, blueprint_app
    ):
        """Malformed encoded FieldId on /api/assets/values: Pydantic
        query-param decode fails and APIValidationError (400) is raised
        before the service is reached. Service mock side_effect is set as
        a safety net."""
        blueprint_app.asset_service.get_field_values.side_effect = InvalidFieldError(
            _INJECTION_ERROR_MSG
        )

        bad_field = "static:COALESCE(id::text,(SELECT%20current_setting('my.secret')))"
        resp = client.get(
            f"/api/assets/values?field={bad_field}",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400, (
            f"expected 400, got {resp.status_code}: {_body_snippet(resp)}"
        )

    def test_assets_aggregate_rejects_unknown_prefix(
        self, client, authed_headers, blueprint_app
    ):
        """row_field with an unknown encoded prefix: Pydantic query-param
        validation fails -> 400."""
        blueprint_app.asset_service.aggregate_assets.side_effect = InvalidFieldError(
            _INJECTION_ERROR_MSG
        )

        resp = client.get(
            "/api/assets/aggregate?row_field=bogus:prefix:x",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400, (
            f"expected 400, got {resp.status_code}: {_body_snippet(resp)}"
        )
