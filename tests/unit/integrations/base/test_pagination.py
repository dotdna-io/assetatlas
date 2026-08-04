# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for new NoPaginator (single-call paginator) and PagePaginator."""
from src.modules.integrations.base.pagination import (
    CursorPaginator,
    NoPaginator,
    PagePaginator,
)


def test_yields_once_when_records_present():
    paginator = NoPaginator()
    pages = list(paginator.pages(lambda: [{"a": 1}, {"a": 2}]))
    assert pages == [[{"a": 1}, {"a": 2}]]


def test_does_not_yield_when_records_empty():
    paginator = NoPaginator()
    pages = list(paginator.pages(lambda: []))
    assert pages == []


def test_does_not_yield_when_records_none():
    paginator = NoPaginator()
    pages = list(paginator.pages(lambda: None))
    assert pages == []


# ----------------------------- PagePaginator ---------------------------------


def test_page_paginator_walks_pages_until_total_reached():
    """3 pages of 2 records, total=5 → stop after collecting 6 (≥5)."""
    pages_data = [
        ([{"id": 1}, {"id": 2}], 5),
        ([{"id": 3}, {"id": 4}], 5),
        ([{"id": 5}, {"id": 6}], 5),  # over-fetch — should stop after this
        ([{"id": 7}], 5),  # never reached
    ]
    calls = []

    def fetch(page, size):
        calls.append((page, size))
        return pages_data[page - 1] if page <= len(pages_data) else ([], None)

    paginator = PagePaginator(page_size=2, start_page=1, max_pages=10)
    pages = list(paginator.pages(fetch))
    assert len(pages) == 3
    assert sum(len(p) for p in pages) == 6
    assert calls == [(1, 2), (2, 2), (3, 2)]


def test_page_paginator_stops_on_empty_page():
    """If a page returns empty, the loop terminates."""
    def fetch(page, size):
        return ([{"id": page}], None) if page < 3 else ([], None)

    pages = list(PagePaginator(page_size=1).pages(fetch))
    assert len(pages) == 2


def test_page_paginator_respects_start_page():
    """start_page=0 (some APIs are 0-indexed)."""
    seen = []
    def fetch(page, size):
        seen.append(page)
        return ([{"x": page}], None) if page < 2 else ([], None)

    list(PagePaginator(page_size=1, start_page=0).pages(fetch))
    assert seen[0] == 0
    assert seen[1] == 1


def test_page_paginator_respects_max_pages_safety():
    """If the API never signals empty/total, max_pages caps the loop."""
    def fetch(page, size):
        return ([{"x": page}], None)

    pages = list(PagePaginator(page_size=1, max_pages=3).pages(fetch))
    assert len(pages) == 3


def test_page_paginator_short_circuits_on_partial_last_page():
    """When the API doesn't return total, a partial page (len < page_size)
    is the last one — paginator should stop without one more empty round-trip."""
    pages_data = [
        ([{"id": 1}, {"id": 2}, {"id": 3}], None),  # full page
        ([{"id": 4}, {"id": 5}], None),              # partial — should stop
    ]
    calls = []

    def fetch(page, size):
        calls.append(page)
        return pages_data[page - 1] if page <= len(pages_data) else ([], None)

    pages = list(PagePaginator(page_size=3).pages(fetch))
    assert sum(len(p) for p in pages) == 5
    assert calls == [1, 2]  # exactly 2 calls — no third fetch confirming empty


# ----------------------------- CursorPaginator -------------------------------


def test_cursor_paginator_walks_until_cursor_none():
    """Happy path: opaque string token until the API returns None."""
    pages_data = [
        ([{"id": 1}, {"id": 2}], "tok-2"),
        ([{"id": 3}, {"id": 4}], "tok-3"),
        ([{"id": 5}], None),  # end of data
    ]
    calls = []

    def fetch(cursor):
        calls.append(cursor)
        idx = 0 if cursor is None else int(cursor.split("-")[1]) - 1
        return pages_data[idx]

    paginator = CursorPaginator(max_pages=10)
    pages = list(paginator.pages(fetch))
    assert sum(len(p) for p in pages) == 5
    assert calls == [None, "tok-2", "tok-3"]


def test_cursor_paginator_terminates_on_zero_cursor():
    """Regression: CrowdStrike's combined endpoint returns
    meta.pagination.offset = 0 once you walk off the end. The paginator
    MUST treat falsy-non-None values as end-of-data — otherwise the same
    request is issued max_pages times. (production 2026-05-13 bug.)"""
    calls = []

    def fetch(cursor):
        calls.append(cursor)
        # API echoes the same 2 records and offset=0 on every call.
        return [{"id": 1}, {"id": 2}], 0

    paginator = CursorPaginator(max_pages=100)
    pages = list(paginator.pages(fetch))
    assert len(pages) == 1, "should yield exactly one page, not loop 100 times"
    assert len(calls) == 1


def test_cursor_paginator_terminates_on_empty_string_cursor():
    """Some APIs signal end-of-data with an empty string token rather than
    null. Same falsy-but-not-None semantics."""
    calls = []

    def fetch(cursor):
        calls.append(cursor)
        return [{"id": 1}], ""

    pages = list(CursorPaginator(max_pages=50).pages(fetch))
    assert len(pages) == 1
    assert len(calls) == 1


def test_cursor_paginator_terminates_on_cursor_stagnation():
    """Defense in depth: if the API returns the same non-falsy cursor twice
    in a row, the paginator must stop instead of looping forever."""
    calls = []

    def fetch(cursor):
        calls.append(cursor)
        return [{"id": 1}], "stuck-cursor"

    pages = list(CursorPaginator(max_pages=100).pages(fetch))
    # First call: cursor=None, fetch returns "stuck-cursor".
    # Second call: cursor="stuck-cursor", fetch returns "stuck-cursor" again
    # → stagnation → break before a third call.
    assert len(pages) == 2
    assert calls == [None, "stuck-cursor"]


def test_cursor_paginator_respects_max_pages_safety():
    """If the API returns a fresh non-falsy cursor every time, max_pages
    still caps the loop."""
    counter = {"n": 0}

    def fetch(cursor):
        counter["n"] += 1
        return [{"id": counter["n"]}], f"tok-{counter['n']}"

    pages = list(CursorPaginator(max_pages=3).pages(fetch))
    assert len(pages) == 3


def test_cursor_paginator_stops_on_empty_records():
    """If the API returns no records on a page, the paginator stops
    immediately (don't yield, don't follow the next cursor)."""
    calls = []

    def fetch(cursor):
        calls.append(cursor)
        return [], "would-have-followed"

    pages = list(CursorPaginator(max_pages=10).pages(fetch))
    assert pages == []
    assert calls == [None]
