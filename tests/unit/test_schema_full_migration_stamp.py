# SPDX-License-Identifier: AGPL-3.0-or-later
"""schema-full.sql must record exactly the migrations it embodies, and must
schema-qualify both halves of the function/materialized-view relationship.

Three invariants over the same file, all DB-free, all in the same required check.
The second and third are at the bottom of the file under their own banners:
#1196 (a function body must qualify the relations it reads) and #1204 (a
materialized view must qualify the functions it calls, so the file can carry
pg_dump's own `search_path = ''` instead of a hand-patch).

Fresh installs build the database from schema-full.sql (compose bind-mounts it to
/docker-entrypoint-initdb.d/), NOT by running migrations. Before the stamp block
existed, that left schema_migrations empty while creating the 'assets' table,
which is precisely the signal deploy/lib/migrate.sh's bootstrap_if_empty() reads
as "installed before the migration runner existed, therefore fully migrated" — so
it stamped every file on disk as applied without running any of it.

That made the dual-write discipline load-bearing and unverified. Add migration
150 without mirroring it into schema-full.sql and: fresh installs get a schema
without 150's changes, 150 is recorded as applied anyway, apply_migrations skips
it forever, update.sh never catches up, and nothing errors — now or on any later
upgrade. Invisible on the developer's machine, where 150 was applied
incrementally and everything looks right.

This guard is why that can no longer happen silently: the list is explicit, so a
missing entry fails here, and at runtime an unlisted migration simply applies
normally.

Runs without a database, and is included in the "pipeline + delivery domain
(hard gate)" required check — unlike plain `pytest tests/unit`, which is
warning-only in CI (#596 H7) and therefore cannot enforce anything.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "src" / "database" / "migrations"
SCHEMA_FULL = PROJECT_ROOT / "src" / "database" / "schema-full.sql"

# The stamp block: INSERT INTO public.schema_migrations (filename) VALUES (...);
_STAMP = re.compile(
    r"INSERT\s+INTO\s+(?:public\.)?schema_migrations\s*\(\s*filename\s*\)\s*VALUES(.*?);",
    re.S | re.I,
)
_FILENAME = re.compile(r"\(\s*'([^']+)'\s*\)")


def _stamped_filenames():
    """Every filename listed in schema-full.sql's stamp block, in file order."""
    text = SCHEMA_FULL.read_text()
    blocks = _STAMP.findall(text)
    assert blocks, (
        "schema-full.sql has no `INSERT INTO public.schema_migrations (filename) "
        "VALUES ...` block. Fresh installs would leave schema_migrations empty, so "
        "deploy/lib/migrate.sh's bootstrap_if_empty() would stamp every migration "
        "as applied without running it — and any later drift would be permanent."
    )
    names = []
    for block in blocks:
        names.extend(_FILENAME.findall(block))
    return names


def _migration_filenames():
    return sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))


def test_migrations_dir_is_not_empty():
    """Guards the guard: an empty glob would make the parity test vacuously pass."""
    assert _migration_filenames(), f"no *.sql migrations found in {MIGRATIONS_DIR}"


def test_stamp_lists_every_migration_on_disk():
    """A migration missing from the stamp is the silent-drift bug this prevents.

    It would still APPLY correctly (unlisted => not recorded => apply_migrations
    runs it), so this is not about runtime breakage. It is about schema-full.sql
    having drifted away from the migrations it claims to embody: fresh installs
    would then get a schema built without that migration's changes.
    """
    missing = sorted(set(_migration_filenames()) - set(_stamped_filenames()))
    assert not missing, (
        "These migrations are not listed in schema-full.sql's schema_migrations "
        f"stamp block: {missing}\n\n"
        "schema-full.sql is the fresh-install path and must carry every change its "
        "migrations make. Apply the change to schema-full.sql, then add the "
        "filename to the stamp block at the end of the file."
    )


def test_stamp_lists_nothing_that_is_not_on_disk():
    """A stamped-but-absent filename is worse: it is recorded as applied on every
    fresh install, so if the file ever reappears (or is re-added under the same
    name) it can never run."""
    extra = sorted(set(_stamped_filenames()) - set(_migration_filenames()))
    assert not extra, (
        "schema-full.sql's stamp block lists migrations that do not exist in "
        f"{MIGRATIONS_DIR.name}/: {extra}\n\n"
        "Filenames are the primary key of schema_migrations and are never renamed "
        "or re-numbered, so a stale entry means the stamp block was edited by hand "
        "and drifted. Remove it."
    )


def test_stamp_has_no_duplicates():
    names = _stamped_filenames()
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"duplicate entries in schema-full.sql's stamp block: {dupes}"


@pytest.mark.parametrize("reserved", ["001", "002"])
def test_reserved_prefixes_stay_absent(reserved):
    """001/002 do not exist as migrations — schema-full.sql IS the bootstrap for
    them (see test_migration_layout.py). Stamping them would claim a file that can
    never be produced."""
    assert not [n for n in _stamped_filenames() if n.startswith(f"{reserved}_")], (
        f"stamp block references a {reserved}_* migration; that range is reserved "
        "for the schema-full.sql bootstrap and has no file on disk."
    )


# ── Function bodies must be schema-qualified: the restore invariant (#1196) ───
#
# pg_dump emits `SELECT pg_catalog.set_config('search_path', '', false)` (the
# CVE-2018-1058 hardening) and qualifies everything it generates — but it CANNOT
# re-qualify a function body, which it dumps as opaque text. So an unqualified
# relation reference inside a function body survives into every dump verbatim.
#
# That is only latent for a plpgsql body, which is never parsed at restore time
# (pg_dump also sets check_function_bodies = false) and is called later with a
# normal search_path. It is fatal for a `LANGUAGE sql STABLE` function that a
# materialized view calls: the planner inlines it while planning the view, so
# `CREATE MATERIALIZED VIEW` itself fails, `ON_ERROR_STOP=1` aborts, and
# deploy/lib/backup.sh's restore_db dies — AFTER its `DROP SCHEMA public CASCADE`,
# leaving the database empty. That took out auto-rollback, `update.sh --rollback`
# and `assetatlas-cli restore` at once, plus every nightly timer backup on every
# deployment.
#
# These tests are DB-free on purpose: the defect is a property of the text, and
# the only thing that ever caught it was a ~20-minute e2e job nothing gates on.

_FUNCTION = re.compile(
    r"^CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:public\.)?(\w+)\s*\("
    r".*?\bAS\s*(\$\w*\$)(.*?)\2\s*;",
    re.S | re.I | re.M,
)

_MATVIEW = re.compile(
    r"^CREATE\s+MATERIALIZED\s+VIEW\s+(?:public\.)?(\w+)\s+AS\b(.*?);\s*$",
    re.S | re.I | re.M,
)

# A relation reference in a function body. Deliberately narrow — FROM/JOIN only,
# which is every read path an inlined SQL function can take. `(?!\()` skips
# `FROM (SELECT ...`; the dotted capture lets the caller tell `system_settings`
# from `public.system_settings`.
_BODY_RELATION = re.compile(r"\b(?:FROM|JOIN)\s+(?!\()([A-Za-z_][\w.]*)", re.I)

# CTE names are local to the body and correctly unqualified.
_CTE_NAME = re.compile(r"\b(\w+)\s+AS\s*\(", re.I)

_THRESHOLD_FUNCTIONS = ["confidence_threshold_high", "confidence_threshold_medium"]


def _functions(text):
    """{function name: body text} for every dollar-quoted function in `text`."""
    return {m.group(1): m.group(3) for m in _FUNCTION.finditer(text)}


def _unqualified_relations(body):
    """Relation names a function body reads without a schema qualifier."""
    ctes = {n.lower() for n in _CTE_NAME.findall(body)}
    return sorted(
        {
            name
            for name in _BODY_RELATION.findall(body)
            if "." not in name and name.lower() not in ctes
        }
    )


def test_schema_full_defines_the_confidence_threshold_functions():
    """Guards the guard: a regex that stopped matching would pass vacuously."""
    found = _functions(SCHEMA_FULL.read_text())
    missing = [f for f in _THRESHOLD_FUNCTIONS if f not in found]
    assert not missing, (
        f"could not extract these function bodies from schema-full.sql: {missing}. "
        "Either they were removed, or the dollar-quoting/formatting changed and "
        "_FUNCTION no longer matches — fix the pattern, do not delete the test."
    )


@pytest.mark.parametrize("func", _THRESHOLD_FUNCTIONS)
def test_confidence_threshold_bodies_qualify_system_settings(func):
    """The exact defect of #1196: `FROM system_settings`, not `FROM public.system_settings`."""
    body = _functions(SCHEMA_FULL.read_text())[func]
    assert "public.system_settings" in body, (
        f"{func}()'s body in schema-full.sql does not read public.system_settings.\n\n"
        "pg_dump cannot re-qualify a function body, and it restores with "
        "search_path = ''. Four materialized views call this function, the planner "
        "inlines it while planning them, and an unqualified table therefore does not "
        "resolve — so `CREATE MATERIALIZED VIEW` aborts under ON_ERROR_STOP=1 and "
        "restore_db dies AFTER its DROP SCHEMA public CASCADE, leaving an empty "
        "database. Write `FROM public.system_settings` (see migration "
        "142_qualify_confidence_threshold_function_bodies.sql)."
    )
    assert not re.search(r"\bFROM\s+system_settings\b", body, re.I), (
        f"{func}()'s body in schema-full.sql still has an unqualified "
        "`FROM system_settings` alongside the qualified one — see #1196."
    )


def test_functions_inlined_by_materialized_views_are_fully_qualified():
    """The class, not just the instance.

    Any function a materialized view calls gets inlined at plan time, so its body
    is parsed during a restore that runs with search_path = ''. Every relation it
    reads must therefore be schema-qualified. plpgsql helpers that no view calls
    are out of scope — their bodies are never parsed at restore time.
    """
    text = SCHEMA_FULL.read_text()
    functions = _functions(text)
    view_bodies = {m.group(1): m.group(2) for m in _MATVIEW.finditer(text)}
    assert view_bodies, "no CREATE MATERIALIZED VIEW found in schema-full.sql"

    offenders = {}
    for name, body in functions.items():
        callers = [v for v, vbody in view_bodies.items() if f"{name}(" in vbody]
        if not callers:
            continue
        bad = _unqualified_relations(body)
        if bad:
            offenders[name] = (bad, sorted(callers))

    assert not offenders, (
        "These function bodies read a relation without a schema qualifier while a "
        f"materialized view inlines them: {offenders}\n\n"
        "pg_dump restores with search_path = '' and cannot re-qualify a function "
        "body, so the view's CREATE fails and restore_db empties the database "
        "(#1196). Qualify every relation in the body as public.<table>, in a "
        "migration AND in schema-full.sql."
    )


@pytest.mark.parametrize("func", _THRESHOLD_FUNCTIONS)
def test_newest_migration_defining_a_threshold_function_qualifies_it(func):
    """The other half of the dual write.

    schema-full.sql is the fresh-install path; migrations are the upgrade path. A
    later migration that redefines one of these functions with an unqualified body
    would silently re-break every existing install while schema-full.sql — and the
    test above — stayed green. Migration 110 introduced the unqualified form and is
    history (filenames are the primary key, applied migrations are never edited);
    142 supersedes it, so the NEWEST definition is the one that has to be right.
    """
    define = re.compile(
        rf"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:public\.)?{func}\s*\(", re.I
    )
    # Lexical order is numeric order for the zero-padded NNN_ prefixes.
    definers = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if define.search(p.read_text()))
    assert definers, f"no migration defines {func}() — expected 110 and 142"

    newest = definers[-1]
    body = _functions(newest.read_text())[func]
    assert "public.system_settings" in body, (
        f"{newest.name} is the newest migration defining {func}() and its body "
        "reads system_settings unqualified.\n\n"
        "That reintroduces #1196 on every host that applies it: the function is "
        "inlined into four materialized views, so pg_dump output stops being "
        "restorable and restore_db empties the database instead. Use "
        "`FROM public.system_settings`, and mirror the change into schema-full.sql."
    )


# ── The other half: call sites, and the search_path line (#1204) ──────────────
#
# The two tests above cover a function body qualifying the relations it READS.
# These cover the mirror image: a materialized view qualifying the function it
# CALLS, which is what lets this file carry pg_dump's `search_path = ''`.
#
# Until #1204 it did not. The four dashboard views called
# confidence_threshold_high()/_medium() unqualified, so line 22 of the file was a
# hand-patch — `set_config('search_path', 'public', false)` — with a comment
# asking whoever regenerated the file to please put it back. That is the entire
# enforcement mechanism a generated file cannot have: pg_dump writes
# `search_path = ''` every time (CVE-2018-1058 hardening), so one regeneration
# dropped the patch and every fresh install died on
#
#   ERROR:  function confidence_threshold_high() does not exist
#
# at the first CREATE MATERIALIZED VIEW, with ON_ERROR_STOP=1 aborting the rest
# of the schema.
#
# Qualifying the call sites removes the need for the patch, and — the point of
# doing it this way — makes the file agree with what pg_dump would produce, since
# pg_dump deparses these call sites as public.confidence_threshold_high() anyway.
# After that a regeneration is safe. These two tests are what keeps it that way;
# the only other reader of this file's search_path line is
# tools/regression_harness/seed_db.sh, which now loads the file verbatim.

# A call of `name`, with the `public.` prefix captured if present. The negative
# lookbehind stops the unqualified branch from also matching inside a qualified
# call (`public.f(` would otherwise match again at `f(`). Case-insensitive like
# every other pattern here: SQL identifiers fold, and these matview blocks are
# hand-maintained — a `PUBLIC.f()` that this missed entirely would count as zero
# call sites and the offender check would pass vacuously.
def _call_sites(body, name):
    """[bool] — one entry per call of `name` in `body`, True if it is qualified."""
    pattern = re.compile(rf"(?<![\w.])(public\.)?{re.escape(name)}\s*\(", re.I)
    return [m.group(1) is not None for m in pattern.finditer(body)]


def test_materialized_views_qualify_the_functions_they_call():
    """The class, not just the instance — same shape as the #1196 test above.

    A view stores the function's OID, not the text, so an unqualified call is
    invisible in the database and harmless there: `psql` resolves it through a
    normal search_path when this file's CREATE runs, and pg_dump re-qualifies it
    on the way out. It is fatal in THIS file alone, which is applied with
    search_path = '' and is the only path a fresh install takes.

    Scope is functions this file defines, i.e. the ones that live in public.
    Built-ins (count, avg, now, …) are correctly unqualified: pg_catalog stays on
    the implicit search_path even when it is set to ''.
    """
    text = SCHEMA_FULL.read_text()
    functions = _functions(text)
    view_bodies = {m.group(1): m.group(2) for m in _MATVIEW.finditer(text)}
    assert view_bodies, "no CREATE MATERIALIZED VIEW found in schema-full.sql"

    examined = 0
    offenders = {}
    for view, body in sorted(view_bodies.items()):
        for name in sorted(functions):
            calls = _call_sites(body, name)
            examined += len(calls)
            unqualified = calls.count(False)
            if unqualified:
                offenders.setdefault(view, {})[name] = unqualified

    assert examined, (
        "no materialized view in schema-full.sql calls any function this file "
        f"defines ({sorted(functions)}), so this test proved nothing. Either the "
        "views stopped using them, or _MATVIEW/_FUNCTION no longer match the "
        "file's formatting — fix the patterns, do not delete the test."
    )
    assert not offenders, (
        "These materialized views call a public function without qualifying it "
        f"(view -> function -> number of call sites): {offenders}\n\n"
        "schema-full.sql runs with search_path = '' — pg_dump's own setting, and "
        "the CVE-2018-1058 hardening — so an unqualified call does not resolve and "
        "the CREATE MATERIALIZED VIEW aborts, taking the rest of the fresh install "
        "with it. Write public.<function>(). Do NOT re-add a "
        "set_config('search_path', 'public', ...) line to work around it: pg_dump "
        "overwrites that on the next regeneration and the breakage comes back "
        "silently (#1204)."
    )


def test_schema_full_keeps_pg_dumps_empty_search_path():
    """The hand-patch #1204 removed must not come back.

    It is not merely redundant now that the call sites are qualified: a
    non-empty search_path in a file restored as superuser is the CVE-2018-1058
    exposure pg_dump's `''` exists to close, and re-adding it would hide the very
    regression the test above catches.
    """
    text = SCHEMA_FULL.read_text()

    patched = re.findall(r"set_config\(\s*'search_path'\s*,\s*'[^']+'\s*,[^)]*\)", text)
    assert not patched, (
        f"schema-full.sql sets a non-empty search_path: {patched}\n\n"
        "pg_dump emits `set_config('search_path', '', false)` and re-emits it on "
        "every regeneration, so a hand-patched value survives only until the next "
        "one — and it masks unqualified references that then break every fresh "
        "install (#1204). Qualify the references instead."
    )
    assert re.search(r"set_config\(\s*'search_path'\s*,\s*''\s*,\s*false\s*\)", text), (
        "schema-full.sql no longer contains pg_dump's "
        "`SELECT pg_catalog.set_config('search_path', '', false);`.\n\n"
        "That line is the CVE-2018-1058 hardening: without it a restore running as "
        "superuser resolves unqualified names against a permissive search_path and "
        "can execute an attacker-planted object that shadows a pg_catalog one. It "
        "is emitted by pg_dump — if it is gone, this file was edited by hand or "
        "generated by something other than pg_dump."
    )
