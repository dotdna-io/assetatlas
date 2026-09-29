# SPDX-License-Identifier: AGPL-3.0-or-later
"""The public release notes are authored prose, and this is what holds them to it.

Public release bodies used to be `git log --pretty=%s` carried through the
scrubber: readers of dotdna-io/assetatlas got "fix: retry the release retag on a
transient GHCR 401" and "docs: CLAUDE.md audit — ...". No amount of filtering
turns a commit subject into a product sentence, so the notes became authored
files under docs/release-notes/ — and authored files rot unless something reads
them. That is this module.

The lint has to live in a library rather than inline in the publish shell for
exactly one reason: this test runs in `pytest (unit)`, a required check, so a
release note that names an internal file fails the release PR instead of the
publish job nobody is watching.
"""

import importlib.util
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOTES_DIR = PROJECT_ROOT / "docs" / "release-notes"

# Same marker as tests/unit/test_app_image_contents.py: scrub.py deletes both
# scripts/publish/ and docs/ from the published tree, and #1437 runs this suite
# against that scrubbed tree. Keying off scripts/publish/ (never shipped, by
# construction) means a missing docs/release-notes/ on the MIRROR skips while a
# missing one on a dev checkout still fails — which is the regression that
# matters here.
_ON_PUBLIC_MIRROR = not (PROJECT_ROOT / "scripts" / "publish").is_dir()
_SKIP_ON_MIRROR = pytest.mark.skipif(
    _ON_PUBLIC_MIRROR,
    reason="scripts/publish/ and docs/ are excluded from the published mirror",
)

if _ON_PUBLIC_MIRROR:
    release_notes = None
    NOTES_FILES = []
else:
    _spec = importlib.util.spec_from_file_location(
        "release_notes", PROJECT_ROOT / "scripts" / "publish" / "release_notes.py"
    )
    release_notes = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(release_notes)
    NOTES_FILES = sorted(NOTES_DIR.glob("*.md"))


GOOD = """### Improved
- Updates that skip one or more intermediate versions now ask for
  confirmation instead of stopping with an error.

### Fixed
- A database backup that fails during an update is reported and halts the
  update, rather than being passed over silently.
"""


# (id, body, substring the complaint must contain)
_REJECTED = [
    # The reference that started this: "#1234" in a release body is not inert
    # text — a body IS an autolinking context, so it resolves against the PUBLIC
    # repo's numbering and points at an unrelated issue.
    ("issue_ref", "### Fixed\n- Corrected the Explorer filter (#1234).\n", "issue"),
    ("sha", "### Fixed\n- Corrected in 7c81df2 the sort order.\n", "commit"),
    ("claude_md", "### Improved\n- Revised the CLAUDE.md guidance.\n", "internal"),
    ("claude_dir", "### Improved\n- Revised the .claude/rules layout.\n", "internal"),
    ("dev_repo", "### Fixed\n- See dotdna-assetatlas for the details.\n", "internal"),
    ("agents_md", "### Improved\n- Refreshed AGENTS.md for contributors.\n", "internal"),
    ("claude_code", "### Added\n- Claude Code integration for the pipeline.\n", "agent"),
    ("coding_agent", "### Added\n- Guidance for coding agent workflows.\n", "agent"),
    ("coauthored", "### Fixed\n- Co-Authored-By trailers are stripped.\n", "agent"),
    # The tell that someone pasted commit subjects back in.
    ("commit_prefix", "### Fixed\n- fix: retry the retag.\n", "commit-style"),
    ("unknown_heading", "### Internal\n- Reworked the build.\n", "heading"),
    ("empty_section", "### Fixed\n\n### Added\n- Added a thing.\n", "empty"),
    ("bullet_outside", "- Added a thing.\n", "outside"),
    ("lowercase", "### Fixed\n- corrected the sort order.\n", "capital"),
    ("no_period", "### Fixed\n- Corrected the sort order\n", "sentence"),
    ("stray_line", "### Fixed\n- Corrected the sort order.\nNot a bullet.\n", "unexpected"),
    ("empty_file", "", "empty"),
]


@_SKIP_ON_MIRROR
def test_clean_notes_lint_clean():
    assert release_notes.lint(GOOD) == []


@_SKIP_ON_MIRROR
@pytest.mark.parametrize(
    "body,expected", [c[1:] for c in _REJECTED], ids=[c[0] for c in _REJECTED]
)
def test_lint_rejects(body, expected):
    problems = release_notes.lint(body)
    assert problems, f"expected a complaint, got none for:\n{body}"
    assert any(expected in p.lower() for p in problems), (
        f"expected a complaint mentioning {expected!r}, got {problems}"
    )


# Vendor names are product vocabulary in THIS product — it ships Claude and
# Bedrock chat providers and cursor-paginated APIs. A lint that banned them
# outright would block the release note announcing them, so the ban covers
# unambiguous coding-agent vocabulary only ("Claude Code", "coding agent") and
# leaves ambiguous names to review. Losing this distinction is a regression.
_ALLOWED = [
    ("vendor_name", "### Added\n- Support for Claude models through Amazon Bedrock.\n"),
    ("code_span_start", "### Improved\n- `assetatlas-cli` now reports the channel.\n"),
    ("bold_start", "### Improved\n- **Explorer** loads saved filters faster.\n"),
    ("cursor", "### Added\n- Cursor-based pagination for the assets endpoint.\n"),
    ("version_number", "### Fixed\n- Upgrades from 2026.32.15 no longer stall.\n"),
    ("maintenance", "### Maintenance\n- No user-facing changes; internal tooling only.\n"),
]


@_SKIP_ON_MIRROR
@pytest.mark.parametrize("body", [c[1] for c in _ALLOWED], ids=[c[0] for c in _ALLOWED])
def test_lint_accepts(body):
    assert release_notes.lint(body) == []


@_SKIP_ON_MIRROR
def test_every_notes_file_is_named_for_a_version_or_is_the_style_guide():
    for path in NOTES_FILES:
        assert path.stem == "README" or release_notes.VERSION_RE.match(path.stem), (
            f"{path.name} is neither a version nor the style guide — available() "
            f"would silently ignore it and the release would publish without notes"
        )


@_SKIP_ON_MIRROR
@pytest.mark.parametrize(
    "path",
    [p for p in NOTES_FILES if p.stem != "README"],
    ids=[p.stem for p in NOTES_FILES if p.stem != "README"],
)
def test_committed_notes_lint_clean(path):
    problems = release_notes.lint(path.read_text(encoding="utf-8"))
    assert problems == [], f"{path.name}: " + "; ".join(problems)


@_SKIP_ON_MIRROR
def test_floor_version_has_notes():
    """A canary: an emptied notes dir must not read as "nothing to check".

    Every other test here iterates the directory, so deleting it turns this file
    green. The floor is the one version that is always expected to be there.
    """
    assert (NOTES_DIR / f"{release_notes.NOTES_FLOOR}.md").is_file(), (
        f"docs/release-notes/{release_notes.NOTES_FLOOR}.md is missing — the "
        f"notes corpus starts at the first version that got a public release"
    )


@_SKIP_ON_MIRROR
def test_build_changelog_is_newest_first_and_dates_each_version(tmp_path):
    notes = tmp_path / "docs" / "release-notes"
    notes.mkdir(parents=True)
    (notes / "2026.32.11.md").write_text("### Fixed\n- Corrected a thing.\n")
    (notes / "2026.37.1.md").write_text("### Added\n- Added a thing.\n")

    out = release_notes.build_changelog(tmp_path, date_of=lambda v: f"date-{v}")

    assert out.index("## 2026.37.1 — date-2026.37.1") < out.index(
        "## 2026.32.11 — date-2026.32.11"
    ), "sections must run newest first"
    assert "- Added a thing." in out and "- Corrected a thing." in out


@_SKIP_ON_MIRROR
def test_build_changelog_sorts_numerically_not_lexically(tmp_path):
    """2026.32.2 must not sort above 2026.32.15."""
    notes = tmp_path / "docs" / "release-notes"
    notes.mkdir(parents=True)
    for v in ("2026.32.2", "2026.32.15"):
        (notes / f"{v}.md").write_text("### Fixed\n- Corrected a thing.\n")

    out = release_notes.build_changelog(tmp_path, date_of=lambda v: "2026-01-01")

    assert out.index("## 2026.32.15") < out.index("## 2026.32.2")


@_SKIP_ON_MIRROR
def test_build_changelog_refuses_a_version_without_notes(tmp_path):
    notes = tmp_path / "docs" / "release-notes"
    notes.mkdir(parents=True)
    (notes / "2026.32.11.md").write_text("### Fixed\n- Corrected a thing.\n")

    with pytest.raises(release_notes.MissingNotes) as exc:
        release_notes.build_changelog(
            tmp_path, date_of=lambda v: "2026-01-01", require=("2026.37.9",)
        )
    assert "2026.37.9" in str(exc.value)


@_SKIP_ON_MIRROR
def test_build_changelog_refuses_notes_that_do_not_lint(tmp_path):
    """The publish path must not be able to ship what the PR gate would reject."""
    notes = tmp_path / "docs" / "release-notes"
    notes.mkdir(parents=True)
    (notes / "2026.32.11.md").write_text("### Fixed\n- Corrected it (#12).\n")

    with pytest.raises(release_notes.InvalidNotes) as exc:
        release_notes.build_changelog(tmp_path, date_of=lambda v: "2026-01-01")
    assert "2026.32.11" in str(exc.value)


@_SKIP_ON_MIRROR
def test_section_header_matches_what_the_publish_workflow_slices_on():
    """publish-public.yml cuts the release body with `index($0, "## <ver> ") == 1`.

    The header format and that awk expression are one contract split across two
    files; a header change that looks cosmetic here silently empties every
    release body (the workflow falls back to a "see the Releases page" stub).
    """
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "publish-public.yml"
    ).read_text(encoding="utf-8")
    assert 'pre="## ${VERSION} "' in workflow, (
        "publish-public.yml no longer slices on '## <version> ' — update "
        "release_notes.render_header() to match, or the bodies go empty"
    )
    header = release_notes.render_header("2026.37.2", "2026-09-10")
    assert header.startswith("## 2026.37.2 ")


@_SKIP_ON_MIRROR
def test_scaffold_is_a_starting_point_that_does_not_lint():
    """The skeleton must fail the lint until a human replaces the placeholder.

    A scaffold that passed would let `release.sh` be satisfied by a file nobody
    wrote — the exact failure this whole mechanism exists to prevent.
    """
    text = release_notes.scaffold("2026.37.4")
    assert release_notes.lint(text), "the scaffold must not satisfy the lint"


@_SKIP_ON_MIRROR
def test_check_reports_a_missing_file_rather_than_raising(tmp_path):
    problems = release_notes.check(tmp_path, "2026.37.4")
    assert problems and any("missing" in p.lower() for p in problems)


@_SKIP_ON_MIRROR
def test_allowed_headings_cover_the_categories_the_style_guide_promises():
    guide = (NOTES_DIR / "README.md").read_text(encoding="utf-8")
    for heading in release_notes.ALLOWED_HEADINGS:
        assert re.search(rf"\b{heading}\b", guide), (
            f"'{heading}' is accepted by the lint but absent from the style "
            f"guide — writers cannot follow a rule that is not written down"
        )
