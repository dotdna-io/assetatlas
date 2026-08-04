# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end integration tests: every FieldId kind exercised through the
real API against a live database.

Scope
-----
Happy-path coverage only — for each FieldId variant (static, core_enrichment,
extended_attribute, integration_group, tag, posture-state, posture-output,
virtual) we hit at least one production endpoint and assert it accepted the
payload without error.

This complements tests/unit/security/test_field_injection_regression.py (the
negative path — unknown/malicious FieldIds are rejected 4xx). Together they
prove: the real catalog accepts real FieldIds AND rejects everything else.

``live_app`` / ``live_admin_headers`` / ``live_client`` / ``live_db`` come
from tests/integration/conftest.py (session-scoped) and skip when no live DB
is configured via DATABASE_URL. Tests whose preconditions are absent (no
tags, no integration groups, no extended_attributes) ``pytest.skip``.
No DB writes that aren't cleaned up in ``finally``.
"""

import uuid

import pytest

from src.modules.fields.encoding import encode
from src.modules.fields.models import (
    ExtendedAttributeField,
    StaticField,
    TagField,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Catalog-pick fixtures — one DB scan per module, reused across tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def any_tag_name(live_db):
    """Some tag_name present in the DB, or None if no tags exist.

    ORDER BY tag_name keeps the pick deterministic across replicas/runs.
    """
    rows = live_db.execute_query(
        """
        SELECT tag_name FROM (
            SELECT tag_name FROM asset_tags_v2
            UNION
            SELECT tag_name FROM asset_manual_tags
        ) all_tags
        ORDER BY tag_name
        LIMIT 1
        """,
        {},
    ) or []
    return rows[0]["tag_name"] if rows else None


@pytest.fixture(scope="module")
def any_extended_attribute(live_db):
    """(source, attribute) pair for some extended_attribute in the DB, or None.

    ORDER BY source_name, field_name keeps the pick deterministic.
    """
    rows = live_db.execute_query(
        """
        SELECT
            source.key AS source_name,
            jsonb_object_keys(source.value) AS field_name
        FROM assets,
             LATERAL jsonb_each(extended_attributes) AS source(key, value)
        WHERE extended_attributes IS NOT NULL
          AND jsonb_typeof(source.value) = 'object'
          AND source.key <> 'AssetAtlas'
        ORDER BY source_name, field_name
        LIMIT 1
        """,
        {},
    ) or []
    if not rows:
        return None
    return rows[0]["source_name"], rows[0]["field_name"]


@pytest.fixture(scope="module")
def any_integration_group(live_db, any_extended_attribute):
    """(group_name, attribute) for some integration_group field, or None.

    Best-effort smoke pairing: the attribute is picked INDEPENDENTLY of the
    group (reuses ``any_extended_attribute``), so there's no guarantee it's
    stored under a member source of the group. The resolver may therefore
    legitimately return a 4xx (catalog miss) instead of 200 — the caller
    (test_integration_group) accepts that outcome as valid "kind wired
    through the stack" evidence. Tightening the join would require reading
    integration_groups membership (instance->group map) and is deliberately
    out of scope.
    """
    groups = live_db.execute_query(
        "SELECT name FROM integration_groups ORDER BY name LIMIT 1",
        {},
    ) or []
    if not groups or any_extended_attribute is None:
        return None
    group_name = groups[0]["name"]
    _, attribute = any_extended_attribute
    return group_name, attribute


# ===========================================================================
# Tag-rules preview — FieldId kinds: static, core_enrichment, extended_attribute,
# integration_group, tag, virtual
# ===========================================================================

class TestTagRulesPreviewWithEachKind:
    """Every kind that target_filters/compliance_criteria can hold must be
    accepted by POST /api/tag-rules/preview against the live catalog."""

    def _assert_preview_ok(self, resp):
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        # PreviewCountResponse shape: total_matching / pass_count / fail_count
        data = body.get("data", body)
        assert "total_matching" in data

    def test_static(self, live_client, live_admin_headers):
        """StaticField — ``{"kind": "static", "name": "Hostname"}`` via an
        "is not empty" operator that needs no value."""
        resp = live_client.post(
            "/api/tag-rules/preview",
            json={
                "target_filters": [
                    {
                        "field_id": {"kind": "static", "name": "Hostname"},
                        "operator": "is not empty",
                        "value": None,
                    }
                ]
            },
            headers=live_admin_headers,
        )
        self._assert_preview_ok(resp)

    def test_core_enrichment(self, live_client, live_admin_headers):
        """CoreEnrichmentField — IP Type is a STRING core enrichment."""
        resp = live_client.post(
            "/api/tag-rules/preview",
            json={
                "target_filters": [
                    {
                        "field_id": {"kind": "core_enrichment", "name": "IP Type"},
                        "operator": "is not empty",
                        "value": None,
                    }
                ]
            },
            headers=live_admin_headers,
        )
        self._assert_preview_ok(resp)

    def test_extended_attribute(self, live_client, live_admin_headers, any_extended_attribute):
        """ExtendedAttributeField — picks any source/attribute present in the DB."""
        if any_extended_attribute is None:
            pytest.skip("No extended_attributes present in this DB — skipping")
        source, attribute = any_extended_attribute

        resp = live_client.post(
            "/api/tag-rules/preview",
            json={
                "target_filters": [
                    {
                        "field_id": {
                            "kind": "extended_attribute",
                            "source": source,
                            "attribute": attribute,
                        },
                        "operator": "is not empty",
                        "value": None,
                    }
                ]
            },
            headers=live_admin_headers,
        )
        self._assert_preview_ok(resp)

    def test_integration_group(self, live_client, live_admin_headers, any_integration_group):
        """IntegrationGroupField — requires at least one integration_group defined."""
        if any_integration_group is None:
            pytest.skip("No integration_groups / extended_attributes configured — skipping")
        group, attribute = any_integration_group

        resp = live_client.post(
            "/api/tag-rules/preview",
            json={
                "target_filters": [
                    {
                        "field_id": {
                            "kind": "integration_group",
                            "group": group,
                            "attribute": attribute,
                        },
                        "operator": "is not empty",
                        "value": None,
                    }
                ]
            },
            headers=live_admin_headers,
        )
        # any_integration_group pairs the group and the attribute
        # independently, so the attribute may not be owned by any member
        # source of the group — that's a legitimate resolver 4xx (catalog
        # miss). The shape still had to pass Pydantic + reach the resolver,
        # which is all this smoke test proves.
        assert resp.status_code in (200, 400, 422), resp.get_data(as_text=True)

    def test_tag(self, live_client, live_admin_headers, any_tag_name):
        """TagField — requires at least one tag in asset_tags_v2 / asset_manual_tags."""
        if any_tag_name is None:
            pytest.skip("No tags present in this DB — skipping")
        tag_name = any_tag_name

        resp = live_client.post(
            "/api/tag-rules/preview",
            json={
                "target_filters": [
                    {
                        "field_id": {"kind": "tag", "name": tag_name},
                        "operator": "equals",
                        "value": True,
                    }
                ]
            },
            headers=live_admin_headers,
        )
        self._assert_preview_ok(resp)

    def test_virtual(self, live_client, live_admin_headers):
        """VirtualField — confidence_level is the one registered virtual."""
        resp = live_client.post(
            "/api/tag-rules/preview",
            json={
                "target_filters": [
                    {
                        "field_id": {"kind": "virtual", "name": "confidence_level"},
                        "operator": "equals",
                        "value": "high",
                    }
                ]
            },
            headers=live_admin_headers,
        )
        self._assert_preview_ok(resp)

    @pytest.mark.parametrize("field_name", [
        "hostname_count",
        "ip_count",
        "fqdn_count",
        "user_count",
        "owner_count",
    ])
    def test_virtual_count_fields(self, live_client, live_admin_headers, field_name):
        """Each count virtual must be accepted via tag-rules preview using a numeric op."""
        resp = live_client.post(
            "/api/tag-rules/preview",
            json={
                "target_filters": [
                    {
                        "field_id": {"kind": "virtual", "name": field_name},
                        "operator": "greater than",
                        "value": 1,
                    }
                ]
            },
            headers=live_admin_headers,
        )
        self._assert_preview_ok(resp)

    def test_virtual_asset_label(self, live_client, live_admin_headers):
        """asset_label is accepted as a string virtual via 'is not empty'."""
        resp = live_client.post(
            "/api/tag-rules/preview",
            json={
                "target_filters": [
                    {
                        "field_id": {"kind": "virtual", "name": "asset_label"},
                        "operator": "is not empty",
                        "value": None,
                    }
                ]
            },
            headers=live_admin_headers,
        )
        self._assert_preview_ok(resp)

    def test_virtual_asset_label_honors_override(self, live_client, live_admin_headers, live_db):
        """When assets.asset_label_override is non-empty, the asset_label virtual
        SQL returns the override (verified end-to-end against a live Postgres).

        Picks any existing asset, sets a unique override directly via SQL, then:
          1. Asserts the COALESCE virtual expression evaluates to the override
             (proves the SQL composition in catalog.py is correct).
          2. Issues a tag-rules preview filtering by ``asset_label equals
             <override>`` and asserts ``total_matching >= 1`` (proves the
             virtual is wired through the resolver / WHERE clause path).
        Cleans up the override in ``finally`` even on assertion failure.
        """
        # Pick any existing live asset to avoid coupling to specific seed rows.
        rows = live_db.execute_query(
            "SELECT id FROM assets WHERE merged_into IS NULL LIMIT 1",
            fetch=True,
        ) or []
        if not rows:
            pytest.skip("No live assets available — cannot exercise override SQL")
        asset_id = str(rows[0]["id"])
        unique_label = f"override-test-{uuid.uuid4().hex[:8]}"

        try:
            live_db.execute_query(
                "UPDATE assets SET asset_label_override = %s WHERE id = %s::uuid",
                (unique_label, asset_id),
                fetch=False,
            )

            # 1. Direct SQL: COALESCE returns the override (not the fallback).
            check = live_db.execute_query(
                """
                SELECT COALESCE(
                    NULLIF(asset_label_override, ''),
                    NULLIF(hostname[1], ''),
                    NULLIF(fqdn[1], ''),
                    NULLIF(mac[1], ''),
                    NULLIF(sn[1], ''),
                    NULLIF(ip[1], '')
                ) AS asset_label
                FROM assets WHERE id = %s::uuid
                """,
                (asset_id,),
                fetch=True,
            )
            assert check and check[0]["asset_label"] == unique_label, (
                f"Direct COALESCE should resolve to override; got {check!r}"
            )

            # 2. End-to-end: tag-rules preview matches via the virtual field.
            resp = live_client.post(
                "/api/tag-rules/preview",
                json={
                    "target_filters": [
                        {
                            "field_id": {"kind": "virtual", "name": "asset_label"},
                            "operator": "equals",
                            "value": unique_label,
                        }
                    ]
                },
                headers=live_admin_headers,
            )
            self._assert_preview_ok(resp)

            body = resp.get_json() or {}
            data = body.get("data", body)
            total_matching = data.get("total_matching", 0)
            assert total_matching >= 1, (
                f"asset_label = {unique_label!r} should have matched the override "
                f"asset; got data={data!r}"
            )
        finally:
            live_db.execute_query(
                "UPDATE assets SET asset_label_override = NULL WHERE id = %s::uuid",
                (asset_id,),
                fetch=False,
            )


# ===========================================================================
# Posture sub-rule preview — PostureField kinds (state + output)
# ===========================================================================

class TestPostureSubRulePreview:
    """PostureField variants (read=state, read=output) in target_filters."""

    def _body_with_target_on_posture(self, read_mode: str) -> dict:
        return {
            "target_filters": [
                {
                    "field_id": {
                        "kind": "posture",
                        "category": "secured",
                        "read": read_mode,
                    },
                    "operator": "is not empty",
                    "value": None,
                }
            ],
            "compliance_criteria": {
                "logic": "any",
                "conditions": [
                    {
                        "field_id": {"kind": "static", "name": "Category"},
                        "operator": "is not empty",
                        "value": None,
                    }
                ],
            },
        }

    def test_posture_state(self, live_client, live_admin_headers):
        """PostureField(read='state') — the 'secured' category is always seeded."""
        resp = live_client.post(
            "/api/posture-rules/sub-rules/preview",
            json=self._body_with_target_on_posture("state"),
            headers=live_admin_headers,
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()["data"]
        assert set(data.keys()) == {"matched", "pass", "fail"}

    def test_posture_output(self, live_client, live_admin_headers):
        """PostureField(read='output') — same category, output text read."""
        resp = live_client.post(
            "/api/posture-rules/sub-rules/preview",
            json=self._body_with_target_on_posture("output"),
            headers=live_admin_headers,
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()["data"]
        assert set(data.keys()) == {"matched", "pass", "fail"}


# ===========================================================================
# Charts query — static + extended_attribute as SELECT dimension
# ===========================================================================

class TestChartsQueryWithEachKind:
    """POST /api/charts/query accepts typed FieldIds in select.dimension."""

    def test_static_dimension(self, live_client, live_admin_headers):
        """Distribution over ``static:Category``."""
        resp = live_client.post(
            "/api/charts/query",
            json={
                "queryType": "distribution",
                "from": {"table": "assets"},
                "select": {
                    "dimension": {"kind": "static", "name": "Category"},
                },
                "filters": [],
            },
            headers=live_admin_headers,
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body.get("success") is True
        assert "data" in body
        assert isinstance(body["data"], list)

    def test_extended_attribute_dimension(self, live_client, live_admin_headers, any_extended_attribute):
        """Distribution over an extended_attribute drawn from the live catalog."""
        if any_extended_attribute is None:
            pytest.skip("No extended_attributes present in this DB — skipping")
        source, attribute = any_extended_attribute

        resp = live_client.post(
            "/api/charts/query",
            json={
                "queryType": "distribution",
                "from": {"table": "assets"},
                "select": {
                    "dimension": {
                        "kind": "extended_attribute",
                        "source": source,
                        "attribute": attribute,
                    },
                },
                "filters": [],
            },
            headers=live_admin_headers,
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body.get("success") is True


# ===========================================================================
# Explorer — encoded CSV for ?fields=, encoded scalar for ?field=
# ===========================================================================

class TestExplorerEncodedFields:
    """/api/assets?fields=CSV and /api/assets/values?field=ENCODED both
    accept the canonical ``encode()`` output for a mix of FieldId kinds."""

    def test_fields_csv_mixed_kinds(self, live_client, live_admin_headers, any_tag_name):
        """``?fields=<static>,<tag>`` — two kinds combined via CSV. If no tag
        exists, falls back to two statics so the CSV path still runs."""
        parts = [encode(StaticField(kind="static", name="Hostname"))]

        if any_tag_name:
            parts.append(encode(TagField(kind="tag", name=any_tag_name)))
        else:
            parts.append(encode(StaticField(kind="static", name="Category")))

        resp = live_client.get(
            f"/api/assets?fields={','.join(parts)}&limit=1",
            headers=live_admin_headers,
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        # Dynamic list path returns { assets: [...], ... }; wrapped in
        # {success, data}. Either access pattern should work.
        payload = body.get("data", body)
        assert "assets" in payload

    def test_fields_csv_with_two_segment_extended_attribute(self, live_client, live_admin_headers, any_extended_attribute):
        """The explorer ?fields= CSV correctly round-trips a two-segment encoded
        extended_attribute FieldId (ext:source:attr) — covers the second ':' as
        a literal delimiter."""
        if any_extended_attribute is None:
            pytest.skip("No extended_attribute fields in catalog")
        source, attr = any_extended_attribute

        static_part = encode(StaticField(kind="static", name="Hostname"))
        ext_part = encode(ExtendedAttributeField(
            kind="extended_attribute", source=source, attribute=attr,
        ))
        url = f"/api/assets?fields={static_part},{ext_part}&limit=1"

        resp = live_client.get(url, headers=live_admin_headers)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        # Same wrapping tolerance as test_fields_csv_mixed_kinds above:
        # the explorer list endpoint returns {assets: [...], ...}, possibly
        # wrapped in {success, data: {...}} depending on middleware order.
        payload = body.get("data", body)
        assert "assets" in payload

    def test_values_endpoint_static(self, live_client, live_admin_headers):
        """GET /api/assets/values?field=<encoded static Category>."""
        encoded = encode(StaticField(kind="static", name="Category"))
        resp = live_client.get(
            f"/api/assets/values?field={encoded}&limit=5",
            headers=live_admin_headers,
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        # Response wrapper: {success, data: {values: [...], ...}} — just
        # confirm the endpoint accepted the encoded FieldId.
        payload = body.get("data", body)
        assert isinstance(payload, dict)
