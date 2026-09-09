# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-engine equivalence (§12) + named §6.1 behavior-change guards.

The same logical filter must yield the **identical** SQL fragment through the
two builders that were re-pointed at the operator registry:

  1. Explorer repo builder  ``AssetRepository._build_filter_clauses``
  2. shared compiler        ``AssetQueryCompiler.build_where_clause``

Scope is the BUILDERS, not the orchestrators (which legitimately differ on
AND/OR ``logic``). Because both delegate to one ``op.build_sql``, the fragments
must match modulo param-name prefixes (repo emits ``%(fNN_n)s``; the compiler
emits ``%(param_n_m)s``) — ``_strip_params`` normalises that single difference.

Filters are chosen to avoid the §6.3 Explorer-only special cases (confidence
``confidence_level`` and the Data Source ``eq``/``contains`` group-name
OR-expansion): Data Source ``none``, Hostname ``eq``, Private IP ``none`` —
none of these route through a special case, so the equivalence claim is exact.
Includes the §12-mandated JSONB_ARRAY field with a list value (Private IP
``has none of``).

The in-memory ``match`` half of §6.1 is asserted directly against
``OPERATOR_REGISTRY[...]`` — both in-memory evaluators delegate to it, so the
operator's ``match`` IS the cross-engine behavior.
"""

import re
from unittest.mock import MagicMock

from src.modules.assets.repositories.asset_repository import AssetRepository
from src.modules.shared.asset_query_compiler import AssetQueryCompiler
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.fields.models import StaticField
from src.modules.fields.operators import OPERATOR_REGISTRY, OperatorCtx
from src.modules.fields.types import FieldDataType


def _resolver():
    """A resolver over an EMPTY DB.

    The fields exercised here — static "Data Source"/"Hostname"/"Last Seen" and
    core_enrichment "Private IP" — are all statically defined in catalog.py and
    resolve without any DB rows (only extended_attribute/tag/posture entries
    hit ``execute_query``). Returning ``[]`` proves the harness is DB-free.
    """
    db = MagicMock()
    db.execute_query.return_value = []
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


def _strip_params(frag):
    """Normalise %(fNN_n)s / %(param_n_m)s placeholders so two builders that
    differ only in param-naming compare equal."""
    return re.sub(r"%\([a-zA-Z0-9_]+\)s", "%(P)s", frag)


class TestCrossEngineSqlEquivalence:
    """Same logical filter → identical SQL fragment through both SQL builders."""

    def test_data_source_none_string_array(self):
        # STRING_ARRAY (native text[] column). `none` is NOT eq/contains, so the
        # §6.3 Data Source group-name OR-expansion does not fire — the two
        # builders must agree exactly.
        resolver = _resolver()
        repo = AssetRepository(db_adapter=MagicMock(), resolver=resolver)
        comp = AssetQueryCompiler(resolver=resolver)

        repo_clauses, _ = repo._build_filter_clauses(
            [{"field_id": {"kind": "static", "name": "Data Source"},
              "op": "none", "value": ["jamf", "sccm"]}])
        comp_sql, _ = comp.build_where_clause(
            [{"field_id": {"kind": "static", "name": "Data Source"},
              "operator": "none", "value": ["jamf", "sccm"]}])

        # Non-vacuous: both sides must produce real SQL (a both-empty bug must
        # not pass by comparing "" == "").
        assert repo_clauses and repo_clauses[0]
        assert comp_sql
        # Native-array shape, not the group-name JOIN.
        assert "unnest(data_source)" in repo_clauses[0]
        assert "integration_groups" not in repo_clauses[0]
        assert _strip_params(repo_clauses[0]) == _strip_params(comp_sql)

    def test_private_ip_none_jsonb_array_list(self):
        # JSONB_ARRAY field with a LIST value — the §12-mandated case that
        # STRING_ARRAY alone would not cover (different SQL idiom).
        resolver = _resolver()
        repo = AssetRepository(db_adapter=MagicMock(), resolver=resolver)
        comp = AssetQueryCompiler(resolver=resolver)

        fid = {"kind": "core_enrichment", "name": "Private IP"}
        repo_clauses, _ = repo._build_filter_clauses(
            [{"field_id": fid, "op": "none", "value": ["10.0.0.1", "10.0.0.2"]}])
        comp_sql, _ = comp.build_where_clause(
            [{"field_id": fid, "operator": "none", "value": ["10.0.0.1", "10.0.0.2"]}])

        assert repo_clauses and repo_clauses[0]
        assert comp_sql
        # JSONB_ARRAY shape: jsonb_array_elements_text, NOT EXISTS, case-insensitive.
        assert "jsonb_array_elements_text" in repo_clauses[0]
        assert _strip_params(repo_clauses[0]) == _strip_params(comp_sql)

    def test_eq_exact_string_array(self):
        # Hostname is STRING_ARRAY; `eq` must be EXACT element membership
        # (LOWER(x) = LOWER(v)), never a `contains`-style ILIKE — guards the
        # §6.1 "eq is exact, not wildcard" decision at the SQL level.
        resolver = _resolver()
        repo = AssetRepository(db_adapter=MagicMock(), resolver=resolver)
        comp = AssetQueryCompiler(resolver=resolver)
        repo_clauses, _ = repo._build_filter_clauses(
            [{"field_id": {"kind": "static", "name": "Hostname"},
              "op": "eq", "value": "Win_NT"}])
        comp_sql, _ = comp.build_where_clause(
            [{"field_id": {"kind": "static", "name": "Hostname"},
              "operator": "equals", "value": "Win_NT"}])
        assert repo_clauses and repo_clauses[0]
        assert comp_sql
        assert _strip_params(repo_clauses[0]) == _strip_params(comp_sql)
        assert "ILIKE" not in repo_clauses[0]  # exact, % / _ are literals


class TestStampedUnifiedFieldCrossEngine:
    """#1405 part 2: a materialized (stamped) unified field compiles to its
    stored-value reference through BOTH builders — the Explorer repo path and
    the shared compiler — and the two fragments stay identical."""

    _STAMP = {"slug": "lentity", "name": "lentity", "function": "coalesce",
              "sources": ["static:Hostname", "static:FQDN"],
              "data_type": FieldDataType.STRING,
              "materialized_at": "2026-09-09T00:00:00+00:00"}

    def _resolver(self):
        db = MagicMock()
        db.execute_query.return_value = []
        return FieldResolver(catalog=FieldCatalog(
            db_adapter=db, unified_fields_provider=lambda: [self._STAMP]))

    def test_stamped_unified_eq_cross_engine(self):
        resolver = self._resolver()
        repo = AssetRepository(db_adapter=MagicMock(), resolver=resolver)
        comp = AssetQueryCompiler(resolver=resolver)

        fid = {"kind": "virtual", "name": "lentity"}
        repo_clauses, _ = repo._build_filter_clauses(
            [{"field_id": fid, "op": "eq", "value": "web-1"}])
        comp_sql, _ = comp.build_where_clause(
            [{"field_id": fid, "operator": "equals", "value": "web-1"}])

        assert repo_clauses and repo_clauses[0]
        assert comp_sql
        # The stored-value reference, not the live merge expression.
        assert "unified_values->>'lentity'" in repo_clauses[0]
        assert "COALESCE" not in repo_clauses[0]
        assert _strip_params(repo_clauses[0]) == _strip_params(comp_sql)

    def test_unstamped_unified_eq_cross_engine(self):
        # Same field without the stamp → the live merge SQL through both builders.
        unstamped = {**self._STAMP, "materialized_at": None}
        db = MagicMock()
        db.execute_query.return_value = []
        resolver = FieldResolver(catalog=FieldCatalog(
            db_adapter=db, unified_fields_provider=lambda: [unstamped]))
        repo = AssetRepository(db_adapter=MagicMock(), resolver=resolver)
        comp = AssetQueryCompiler(resolver=resolver)

        fid = {"kind": "virtual", "name": "lentity"}
        repo_clauses, _ = repo._build_filter_clauses(
            [{"field_id": fid, "op": "eq", "value": "web-1"}])
        comp_sql, _ = comp.build_where_clause(
            [{"field_id": fid, "operator": "equals", "value": "web-1"}])

        assert repo_clauses and repo_clauses[0]
        assert comp_sql
        assert "unified_values" not in repo_clauses[0]
        assert "COALESCE(" in repo_clauses[0]  # the live merge expression
        assert _strip_params(repo_clauses[0]) == _strip_params(comp_sql)


class TestScalarHelperCrossEngine:
    """Phase 3 between/within_last: same logical filter → identical SQL fragment
    through both SQL builders, and the relative-duration bound is resolved
    (never interval(v)). Last Seen is a scalar TIMESTAMP static field — no §6.3
    special case fires, so the equivalence claim is exact."""

    def test_between_dates_cross_engine(self):
        resolver = _resolver()
        repo = AssetRepository(db_adapter=MagicMock(), resolver=resolver)
        comp = AssetQueryCompiler(resolver=resolver)
        fid = {"kind": "static", "name": "Last Seen"}
        repo_clauses, repo_params = repo._build_filter_clauses(
            [{"field_id": fid, "op": "between", "value": ["30d", "7d"]}])
        comp_sql, _ = comp.build_where_clause(
            [{"field_id": fid, "operator": "between", "value": ["30d", "7d"]}])
        assert repo_clauses and repo_clauses[0]
        assert comp_sql
        assert "BETWEEN" in repo_clauses[0]
        assert "interval" not in repo_clauses[0].lower()
        # Both bounds were resolved from the relative form to ISO and bound.
        assert len(repo_params) == 2
        assert all(v not in ("30d", "7d") for v in repo_params.values())
        assert _strip_params(repo_clauses[0]) == _strip_params(comp_sql)

    def test_within_last_cross_engine_single_cutoff(self):
        resolver = _resolver()
        repo = AssetRepository(db_adapter=MagicMock(), resolver=resolver)
        comp = AssetQueryCompiler(resolver=resolver)
        fid = {"kind": "static", "name": "Last Seen"}
        repo_clauses, repo_params = repo._build_filter_clauses(
            [{"field_id": fid, "op": "within_last", "value": "30d"}])
        comp_sql, _ = comp.build_where_clause(
            [{"field_id": fid, "operator": "within_last", "value": "30d"}])
        assert repo_clauses and repo_clauses[0]
        assert comp_sql
        assert ">=" in repo_clauses[0]
        assert "interval" not in repo_clauses[0].lower()
        # Exactly one cutoff param, resolved to an ISO timestamp (not '30d').
        assert len(repo_params) == 1
        cutoff = next(iter(repo_params.values()))
        assert cutoff != "30d" and re.match(r"\d{4}-\d\d-\d\d", str(cutoff))
        assert _strip_params(repo_clauses[0]) == _strip_params(comp_sql)


class TestNamedBehaviorChanges:
    """§6.1 named, intentional changes — one test each.

    The in-memory cases call ``OPERATOR_REGISTRY[...].match`` directly: both
    in-memory evaluators (``QueryEngineService._evaluate_condition`` and
    ``data_filters.evaluate_condition``) delegate to it, so the registry
    operator's ``match`` IS the unified cross-engine behavior.
    """

    def test_equals_literal_not_wildcard(self):
        # 'Win_NT' matches only the literal, not WinXNT / Win1NT, in-memory —
        # '_' is a literal underscore, NOT a SQL single-char wildcard.
        eq = OPERATOR_REGISTRY["eq"]
        assert eq.match("Win_NT", "Win_NT", None) is True
        assert eq.match("WinXNT", "Win_NT", None) is False
        assert eq.match("Win1NT", "Win_NT", None) is False

    def test_scalar_empty_blank_everywhere(self):
        # §6.1: scalar empty counts '' as empty (NULL OR '') everywhere, not
        # only None — the compiler/in-memory-tag engines gained '' handling.
        assert OPERATOR_REGISTRY["empty"].match("", None, None) is True

    def test_date_relative_duration_in_compiler(self):
        # Rules gain relative-duration on dates (§6.1). `gte 30d` binds a
        # computed ISO cutoff, NOT the literal '30d'.
        resolver = _resolver()
        comp = AssetQueryCompiler(resolver=resolver)
        sql, params = comp.build_where_clause(
            [{"field_id": {"kind": "static", "name": "Last Seen"},
              "operator": "greater than or equal", "value": "30d"}])
        # The bound param is an ISO timestamp, not '30d'.
        assert sql
        assert params  # the value was bound, not dropped
        assert all(v != "30d" for v in params.values())
        assert any(re.match(r"\d{4}-\d\d-\d\d", str(v)) for v in params.values())

    def test_all_none_case_insensitive(self):
        # §6.1 / §5.2: all & none compare case-insensitively.
        assert OPERATOR_REGISTRY["none"].match(["SCCM"], ["sccm"], None) is False
        assert OPERATOR_REGISTRY["all"].match(["JAMF", "SCCM"], ["jamf", "sccm"], None) is True

    def test_boolean_tag_presence(self):
        # tag X eq false -> tags->>'X' IS NULL (presence inversion, §5.2*).
        eq = OPERATOR_REGISTRY["eq"]

        class _Bool:
            data_type = None
            sql_kind = "tag_boolean"

            def is_array(self):
                return False

            def is_native_array(self):
                return False

            def is_jsonb_sourced(self):
                return True

        ctx = OperatorCtx(field=_Bool(), param_key="f0", params={})
        frag, _ = eq.build_sql("tags->>'Prod'", "false", ctx)
        assert frag == "tags->>'Prod' IS NULL"


class TestEncodedStringFieldFormEquivalence:
    """#822: the alert-rule wizard stores ``target_filters[].field`` as the
    encoded STRING from ``encodeFieldId`` (e.g. ``"static:Category"``). That
    string form must compile to the SAME SQL + params as the FieldId-dict and
    typed forms.

    Pre-fix, ``coerce_field_id`` passed the string through unresolved, the
    resolver rejected it, and ``build_where_clause`` dropped the filter —
    yielding a WHERE-less query that matched the whole fleet (73,739 vs the
    correct 62,442 on dev). Asserting *equality* (not just non-empty) is what
    fails on pre-fix code: the string form produced ``""`` while the dict form
    produced real SQL.
    """

    _VALUE = ["Server", "Workstation"]

    def _dict_compilation(self, comp):
        return comp.build_where_clause(
            [{"field_id": {"kind": "static", "name": "Category"},
              "operator": "any", "value": self._VALUE}])

    def test_encoded_string_compiles_identically_to_dict(self):
        resolver = _resolver()
        comp = AssetQueryCompiler(resolver=resolver)

        dict_sql, dict_params = self._dict_compilation(comp)
        str_sql, str_params = comp.build_where_clause(
            [{"field_id": "static:Category",
              "operator": "any", "value": self._VALUE}])

        # Non-vacuous: the dict form must produce real SQL, so a both-empty
        # ("" == "") regression cannot pass this equality assertion.
        assert dict_sql
        assert "LOWER(category) IN" in dict_sql
        # The encoded-string form is the #822 fix: identical SQL AND params.
        assert str_sql == dict_sql
        assert str_params == dict_params

    def test_typed_field_compiles_identically_to_dict(self):
        resolver = _resolver()
        comp = AssetQueryCompiler(resolver=resolver)

        dict_sql, dict_params = self._dict_compilation(comp)
        typed_sql, typed_params = comp.build_where_clause(
            [{"field_id": StaticField(kind="static", name="Category"),
              "operator": "any", "value": self._VALUE}])

        assert dict_sql
        assert typed_sql == dict_sql
        assert typed_params == dict_params
