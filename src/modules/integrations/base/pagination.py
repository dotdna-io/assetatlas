# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Pagination Helpers

Reusable pagination loop utilities for connectors.
Each paginator handles the iteration loop, safety cap, and termination.
Connectors provide the fetch function that knows the API's response shape.

Truncation signal: every paginator exposes ``truncated`` — True only when the
``max_pages`` cap, not the end of the data, stopped the loop. Read it *only*
after exhausting ``pages()``. ``pages()`` is a generator, so the flag resets
when iteration begins, not when ``pages()`` is called: a generator that is
created and never iterated leaves the previous run's value in place, so a
reused paginator must be read after its loop, never before. A consumer that
abandons the generator early never sets it: an incomplete read is not a
cap-hit.
"""
from typing import Callable, Iterator, List


class OffsetPaginator:
    """Offset/limit pagination (ServiceNow-style).

    ``truncated`` note: without a ``total`` from the API the only end-of-data
    signal this paginator accepts is an empty page, so *any* collection whose
    last records land on the ``max_pages``-th fetch reports truncated — a
    short final page included, because an offset API may legitimately return
    a short page mid-stream. That is a deliberate, conservative false
    positive — over-reporting a partial collection is far cheaper than
    under-reporting one (the asset pipeline reads the shortfall as deleted
    assets).

    fetch_fn signature: (offset: int, limit: int) -> (records: list, total_or_none: int|None)
    """

    truncated = False

    def __init__(self, page_size: int = 100, max_pages: int = 500):
        self.page_size = page_size
        self.max_pages = max_pages

    def pages(self, fetch_fn: Callable) -> Iterator[List[dict]]:
        self.truncated = False
        offset = 0
        for _ in range(self.max_pages):
            records, total = fetch_fn(offset, self.page_size)
            if not records:
                break
            yield records
            offset += len(records)
            if total is not None and offset >= total:
                break
        else:
            self.truncated = True


class PagePaginator:
    """Page-number pagination (PocketBase, DRF, GitLab, many CMS APIs).

    fetch_fn signature:
        (page: int, page_size: int) -> (records: list, total_or_none: int|None)

    The paginator increments `page` from `start_page` on each call. If `total`
    is returned, the loop also stops once we've collected enough records.

    ``truncated`` note: same conservative false positive as OffsetPaginator —
    without a ``total``, data ending exactly on the cap boundary reports as
    truncated. Deliberate: over-reporting beats silently under-reporting.
    """

    truncated = False

    def __init__(
        self,
        page_size: int = 100,
        start_page: int = 1,
        max_pages: int = 500,
    ):
        self.page_size = page_size
        self.start_page = start_page
        self.max_pages = max_pages

    def pages(self, fetch_fn: Callable) -> Iterator[List[dict]]:
        self.truncated = False
        page = self.start_page
        collected = 0
        for _ in range(self.max_pages):
            records, total = fetch_fn(page, self.page_size)
            if not records:
                break
            yield records
            collected += len(records)
            if total is not None and collected >= total:
                break
            # If the API returned fewer than page_size, this was the last
            # page — no need to make one more request just to confirm.
            if len(records) < self.page_size:
                break
            page += 1
        else:
            self.truncated = True


class CursorPaginator:
    """Cursor/token pagination (CrowdStrike scroll, Armis numeric offset).

    The cursor is opaque to the paginator — it passes whatever fetch_fn returns.

    Terminates when fetch_fn returns:
      * an empty record list, OR
      * a falsy cursor (None, 0, "", []), OR
      * the same cursor as the previous call (the API isn't advancing).

    The falsy check matters because some endpoints (notably CrowdStrike's
    /devices/combined/devices/v1 with sort) return ``meta.pagination.offset = 0``
    at end-of-data rather than omitting the field — strict ``is None`` lets
    the loop spin until ``max_pages``.

    fetch_fn signature: (cursor: Any|None) -> (records: list, next_cursor: Any|None)
    """

    truncated = False

    def __init__(self, max_pages: int = 500):
        self.max_pages = max_pages

    def pages(self, fetch_fn: Callable) -> Iterator[List[dict]]:
        self.truncated = False
        cursor = None
        for _ in range(self.max_pages):
            records, next_cursor = fetch_fn(cursor)
            if not records:
                break
            yield records
            if not next_cursor:
                break
            if next_cursor == cursor:
                break
            cursor = next_cursor
        else:
            self.truncated = True


class LinkPaginator:
    """URL-based pagination (Microsoft Graph @odata.nextLink, Qualys XML URL).

    fetch_fn signature: (url: str|None) -> (records: list, next_url: str|None)
    """

    truncated = False

    def __init__(self, max_pages: int = 500):
        self.max_pages = max_pages

    def pages(self, fetch_fn: Callable) -> Iterator[List[dict]]:
        self.truncated = False
        url = None
        for _ in range(self.max_pages):
            records, next_url = fetch_fn(url)
            if not records:
                break
            yield records
            if next_url is None:
                break
            url = next_url
        else:
            self.truncated = True


class NoPaginator:
    """Single-call pagination — fetch_fn called once, all records returned.

    ``truncated`` is always False (a class attribute, like the capped
    paginators) so callers can treat all five paginators uniformly.

    fetch_fn signature: () -> list[dict]
    """

    truncated = False

    def pages(self, fetch_fn: Callable) -> Iterator[List[dict]]:
        records = fetch_fn()
        if records:
            yield records
