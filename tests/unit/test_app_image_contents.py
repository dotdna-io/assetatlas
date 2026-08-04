# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static checks that the app image ships the files the running code reads.

Every existing test for these code paths uses `tmp_path` fixtures or mocks, which
is exactly why two production bugs went unnoticed for months: the functions were
correct, the files simply were not in the image.

- #1093: `Dockerfile` never copied `monitoring/`, so `sync_prometheus_rules()`
  found no rules directory, wrote no `source='prometheus'` rows, and every alert
  Alertmanager delivered was accepted and dropped as `unknown_alertname`.
- #1092: `product_lines.csv` was LFS-tracked, so any consumer that resolved no
  LFS objects got a 130-byte pointer that the app-layer COPY laid over the base
  image's real file. The only symptom was a "Bad product line row" warning at
  boot. Note `release.yml` does set `lfs: true` on the app-image build, so the
  mechanism that put a pointer in a shipped image is not fully accounted for —
  which is the argument for not depending on LFS here at all.

These assert against the real repository tree, not fixtures. That is the point.
"""

import re
from pathlib import Path

import pytest

from src.modules.alerts.prometheus_sync import _parse_rules_from_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = PROJECT_ROOT / "Dockerfile"
RULES_DIR = PROJECT_ROOT / "monitoring" / "alerts"
KNOWLEDGE_BASE = PROJECT_ROOT / "data" / "device_knowledge_base"
KNOWLEDGE_BASE_CSVS = sorted(KNOWLEDGE_BASE.glob("*.csv"))

LFS_POINTER_MAGIC = b"version https://git-lfs.github.com/spec/v1"


def test_dockerfile_ships_prometheus_alert_rules():
    """The rules must be IN the image, not merely present in the repo.

    `MONITORING_RULES_DIR` defaults to the relative path 'monitoring/alerts',
    resolved against WORKDIR /app — so a missing COPY silently degrades to
    "directory does not exist, skipping sync" at boot.
    """
    dockerfile = DOCKERFILE.read_text()
    assert re.search(r"^COPY\b.*\bmonitoring/alerts/", dockerfile, re.MULTILINE), (
        "Dockerfile must COPY monitoring/alerts/ into the image — without it "
        "sync_prometheus_rules() skips and Prometheus alerts are dropped (#1093)"
    )


def test_dockerfile_does_not_ship_data_scrapers():
    """data_scrapers/ is build-time tooling, not runtime code.

    Nothing under src/ or ui/ imports it: device_knowledge/ and geolocation/
    are invoked by scripts/prepare-base-data.sh at image-BUILD time, and
    mac_vendor/ and eol_data/ run in CI against a database. A COPY here ships
    260 KB of never-executed code, and because the public mirror carries only
    the build-input half of the tree, it also makes a mirror-built image
    differ from a dev-built one for no reason.
    """
    dockerfile = DOCKERFILE.read_text()
    assert not re.search(r"^COPY\b.*\bdata_scrapers/", dockerfile, re.MULTILINE), (
        "Dockerfile must not COPY data_scrapers/ — no runtime module imports "
        "it; it is build-time tooling run by scripts/prepare-base-data.sh"
    )


def test_shipped_rule_files_parse_into_alert_rules():
    """The shipped YAML must actually yield rules.

    A rules directory that exists but parses to nothing fails the same way a
    missing one does, just less visibly.
    """
    rule_files = sorted(RULES_DIR.glob("*.yml")) + sorted(RULES_DIR.glob("*.yaml"))
    assert rule_files, f"no Prometheus rule files under {RULES_DIR}"

    rules = [r for f in rule_files for r in _parse_rules_from_file(str(f))]
    assert rules, f"{len(rule_files)} rule file(s) parsed to zero alert rules"

    # Every rule needs a slug: the webhook resolves incoming alerts by alertname
    # against exactly this value, so a blank one is an undeliverable alert.
    assert all(r["slug"] for r in rules)


def test_sync_and_webhook_agree_on_the_source_value():
    """The write side and the read side must use the same `source`.

    `sync_prometheus_rules()` tags its rows with a source literal baked into its
    SQL; `_lookup_rule_id()` filters on a literal in its own SQL. If those two
    ever drift, every alert is dropped as unknown_alertname with nothing logged
    as an error — the failure is total and silent, which is why it is worth
    pinning.

    Compare the two modules' SQL, NOT `_parse_rules_from_file`'s dict: that
    dict carries a "source" key, but the INSERT hardcodes the column value and
    never binds %(source)s, so the parser's copy is dead weight. Asserting on it
    would pass while the value that actually reaches the database drifted.
    """
    source_literal = re.compile(r"source\s*=\s*'([a-z_]+)'")
    alerts_pkg = PROJECT_ROOT / "src" / "modules" / "alerts"

    sync_src = (alerts_pkg / "prometheus_sync.py").read_text()
    written = set(source_literal.findall(sync_src))
    looked_up = set(
        source_literal.findall((alerts_pkg / "prometheus_webhook_service.py").read_text())
    )

    # Guard the regex itself. A reformat that stops it matching would otherwise
    # leave a green test asserting set() == set() — the same silent-pass failure
    # mode this whole module exists to close.
    assert written, "no `source = '...'` literal in prometheus_sync.py — this guard has gone stale"
    assert looked_up, (
        "no `source = '...'` literal in prometheus_webhook_service.py — this guard has gone stale"
    )

    assert written == looked_up, (
        f"sync writes source={written} but the webhook looks up "
        f"source={looked_up} — alerts would be dropped as unknown_alertname"
    )

    # The INSERT hardcodes the source column in its VALUES list rather than
    # binding %(source)s, so there is no `source =` there for the regex above to
    # find. Pin it separately: a drift confined to that one literal would create
    # rows the webhook can never resolve, and everything above would stay green.
    (agreed,) = written
    values_line = re.search(r"^\s*VALUES\s*\(.*$", sync_src, re.MULTILINE)
    assert values_line, "no VALUES line in prometheus_sync.py — this guard has gone stale"
    assert f"'{agreed}'" in values_line.group(0), (
        f"the alert_rules INSERT does not write source='{agreed}': "
        f"{values_line.group(0).strip()}"
    )


def test_knowledge_base_has_csvs_to_check():
    """Pin the collection, not just its members.

    pytest turns an empty parametrize list into a SKIP, not a failure — so if
    product_lines.csv were renamed or dropped from the tree, the pointer check
    below would evaporate and the suite would stay green. That is the same
    silent-pass shape as #1092 itself.
    """
    assert KNOWLEDGE_BASE_CSVS, (
        f"no CSVs under {KNOWLEDGE_BASE} — the LFS-pointer check below would "
        f"silently collect zero cases instead of failing"
    )


@pytest.mark.parametrize("csv_path", KNOWLEDGE_BASE_CSVS, ids=lambda p: p.name)
def test_knowledge_base_csv_is_content_not_an_lfs_pointer(csv_path):
    """A pointer here is worse than nothing: it OVERWRITES the base image's copy.

    Keep these files out of LFS (see .gitattributes) rather than relying on every
    consumer — CI, local builds, offline bundle builds — to remember `lfs: true`.
    """
    head = csv_path.read_bytes()[: len(LFS_POINTER_MAGIC)]
    assert head != LFS_POINTER_MAGIC, (
        f"{csv_path.name} is a git-LFS pointer, not content. It would overwrite "
        f"the base image's real file and degrade enrichment silently (#1092)."
    )


# ---------------------------------------------------------------------------
# teams-bridge image (#1069)
#
# The bridge terminates Teams webhooks, so it is the internet-reachable
# container. It used to be `FROM assetatlas-backend:latest`, which put a 255.7 MB
# ONNX embedding model, a 64.6 MB geolocation DB, chromadb, onnxruntime, numpy,
# pandas, psycopg2 and postgresql-client inside it — none of which it can import.
#
# It now builds from python:3.13-slim with four packages. That only stays true
# while the package keeps importing nothing from the rest of src/, so the AST
# walk below is the load-bearing test: adding `from src.api...` anywhere under
# src/teams_bridge/ would pass every other check and then fail at runtime, in
# the container an outsider can reach, on a customer's host.
# ---------------------------------------------------------------------------

TEAMS_DOCKERFILE = PROJECT_ROOT / "Dockerfile.teams-bridge"
TEAMS_REQUIREMENTS = PROJECT_ROOT / "requirements-teams-bridge.txt"
TEAMS_PACKAGE = PROJECT_ROOT / "src" / "teams_bridge"
RELEASE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release.yml"
BUILD_OFFLINE = PROJECT_ROOT / "deploy" / "build" / "build-offline.sh"

# release.yml is deliberately absent from the published mirror: #1340 excludes
# it because the public repo does not build images (the workflow is repo-guarded
# and disabled_manually there). tests/ still ships and tests.yml still runs, so
# the two guards below must skip on the mirror — but ONLY there. The marker is
# scripts/publish/: scrub.py excludes it unconditionally (it holds the scrubbing
# data and must never ship), so its presence positively identifies a dev
# checkout, where a missing release.yml is a real regression and these tests
# must FAIL, not skip. Do not "simplify" this to `not RELEASE_WORKFLOW.exists()`
# — that turns a deleted/renamed workflow on dev into a silent green run.
_ON_PUBLIC_MIRROR = not (PROJECT_ROOT / "scripts" / "publish").is_dir()
_SKIP_ON_MIRROR = pytest.mark.skipif(
    _ON_PUBLIC_MIRROR,
    reason="release.yml is excluded from the published mirror (#1340)",
)

# Third-party roots the bridge is allowed to import. botframework arrives
# transitively via botbuilder-core, exactly as it does in requirements-prod.txt.
TEAMS_ALLOWED_THIRD_PARTY = {"aiohttp", "botbuilder", "botframework", "prometheus_client"}

HEAVY_PACKAGES = ("torch", "transformers", "sentence-transformers", "sentence_transformers",
                  "chromadb", "onnxruntime", "catboost", "pandas", "numpy", "psycopg2")


def _teams_bridge_sources():
    return sorted(TEAMS_PACKAGE.rglob("*.py"))


def test_teams_bridge_has_sources_to_walk():
    """Collection pin — the import walk below is vacuous over an empty tree."""
    assert _teams_bridge_sources(), f"no .py files under {TEAMS_PACKAGE}"


def test_teams_bridge_does_not_inherit_the_app_image():
    """The whole point: no FROM assetatlas-backend / assetatlas-api."""
    text = TEAMS_DOCKERFILE.read_text()
    froms = [ln.strip() for ln in text.splitlines()
             if ln.strip().upper().startswith("FROM ")]
    assert froms, "Dockerfile.teams-bridge has no FROM"
    for line in froms:
        assert "assetatlas-backend" not in line and "assetatlas-api" not in line, (
            f"teams-bridge is inheriting the app image again ({line}) — that puts the "
            f"ML/data stack back inside the internet-facing container (#1069)"
        )


def test_teams_bridge_requirements_declare_no_heavy_packages():
    reqs = TEAMS_REQUIREMENTS.read_text().lower()
    declared = [ln.split("=")[0].split(">")[0].split("<")[0].strip()
                for ln in reqs.splitlines() if ln.strip() and not ln.startswith("#")]
    offenders = [p for p in HEAVY_PACKAGES if p in declared]
    assert not offenders, f"heavy packages declared for the bridge image: {offenders}"


def test_teams_bridge_imports_nothing_from_the_rest_of_src():
    """The invariant the slim image rests on.

    `src` is a PEP 420 namespace package and the image copies only
    src/teams_bridge/, so an import of any other src module resolves in dev and
    in the test suite, then raises ModuleNotFoundError inside the container.
    """
    import ast

    offenders = []
    for path in _teams_bridge_sources():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                if name == "src" or (name.startswith("src.")
                                     and not name.startswith("src.teams_bridge")):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {name}")
    assert not offenders, (
        "src/teams_bridge/ imports outside its own package; the slim image does not "
        "ship those modules and will fail at runtime:\n  " + "\n  ".join(offenders)
    )


def test_teams_bridge_third_party_imports_are_all_installed():
    """Every third-party root the code imports must be reachable from the reqs."""
    import ast
    import sys

    found = set()
    for path in _teams_bridge_sources():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root != "src" and root not in sys.stdlib_module_names:
                    found.add(root)

    unexpected = found - TEAMS_ALLOWED_THIRD_PARTY
    assert not unexpected, (
        f"src/teams_bridge/ imports third-party packages the slim image does not "
        f"install: {sorted(unexpected)}. Add them to requirements-teams-bridge.txt "
        f"and to TEAMS_ALLOWED_THIRD_PARTY, or the container fails at runtime."
    )


@_SKIP_ON_MIRROR
def test_release_builds_teams_bridge_rather_than_retagging_api():
    """release.yml must not fold teams-bridge back into the api retag loop."""
    text = RELEASE_WORKFLOW.read_text()
    assert "file: Dockerfile.teams-bridge" in text, (
        "release.yml no longer builds teams-bridge from its own Dockerfile"
    )
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("for svc in") and "teams-bridge" in stripped:
            raise AssertionError(
                f"teams-bridge is back in the api retag loop ({stripped}) — it would "
                f"ship the app image under the bridge's name again (#1069)"
            )


@_SKIP_ON_MIRROR
def test_installer_pull_message_matches_what_release_actually_shares():
    """Tie the operator-facing claim to the build that makes it true (#1123).

    The pull message exists for one reason: `docker images` reports a per-image
    total that counts shared layers once per row, so an operator reads the same
    ~592 MB on several rows and concludes the installer downloaded it that many
    times. The message is only reassuring while it is accurate — and it went
    stale the moment #1069 gave teams-bridge its own image, telling operators a
    transfer they had just watched happen had not happened.

    Nothing connected the sentence to the retag loop that defines it, so this
    derives the shared set from release.yml and checks the message against it.
    """
    m = re.search(r"^\s*for svc in (.+?);", RELEASE_WORKFLOW.read_text(), re.M)
    assert m, "release.yml no longer has the retag loop this test derives truth from"
    # api is the build target the loop retags off, so it belongs to the set.
    shared = {"api"} | set(m.group(1).split())

    install_sh = (PROJECT_ROOT / "deploy" / "installer" / "install.sh").read_text()
    claims = [ln.strip() for ln in install_sh.splitlines()
              if "log_info" in ln and "image under" in ln]
    assert len(claims) == 1, (
        f"expected exactly one 'image under N names' claim in install.sh, "
        f"found {len(claims)}: {claims}"
    )
    claim = claims[0]

    for svc in sorted(shared):
        assert svc in claim, (
            f"install.sh's pull message omits '{svc}', which release.yml does "
            f"publish off the shared image: {claim}"
        )
    assert "teams-bridge" not in claim, (
        "install.sh tells operators teams-bridge is part of the shared image "
        "again. It builds from its own Dockerfile (#1069) and transfers "
        f"separately — they can watch it finish first: {claim}"
    )
    # 'three names' must track len(shared); a fifth service would make the
    # count wrong in exactly the way #1123 was wrong.
    number = {2: "two", 3: "three", 4: "four", 5: "five"}[len(shared)]
    assert f"{number} names" in claim, (
        f"release.yml shares the image across {len(shared)} services "
        f"({sorted(shared)}) but install.sh says: {claim}"
    )


def test_offline_build_builds_the_bridge_and_refuses_a_bundle_missing_images():
    text = BUILD_OFFLINE.read_text()
    assert "-f \"$REPO_ROOT/Dockerfile.teams-bridge\"" in text, (
        "build-offline.sh no longer builds teams-bridge from its own Dockerfile"
    )
    assert "docker tag localhost/assetatlas-api:latest localhost/assetatlas-teams-bridge:latest" not in text, (
        "build-offline.sh is tagging api as teams-bridge again, which puts the ML "
        "stack back into offline bundles even though the online path builds it properly"
    )
    assert "missing_imgs" in text and "Refusing to write a bundle" in text, (
        "build-offline.sh can silently ship a bundle with app images missing again"
    )
