# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for pagination helpers."""
import pytest
from src.modules.integrations.base.pagination import (
    OffsetPaginator, PagePaginator, CursorPaginator, LinkPaginator, NoPaginator,
)


class TestOffsetPaginator:
    def test_single_page(self):
        def fetch(offset, limit):
            if offset == 0:
                return [{"id": 1}, {"id": 2}], 2
            return [], 0
        paginator = OffsetPaginator(page_size=10, max_pages=100)
        all_records = [r for page in paginator.pages(fetch) for r in page]
        assert len(all_records) == 2

    def test_multiple_pages(self):
        data = {0: [{"id": i} for i in range(3)], 3: [{"id": i} for i in range(3, 5)]}
        def fetch(offset, limit):
            return data.get(offset, []), 5
        paginator = OffsetPaginator(page_size=3, max_pages=100)
        all_records = [r for page in paginator.pages(fetch) for r in page]
        assert len(all_records) == 5

    def test_stops_at_max_pages(self):
        call_count = 0
        def fetch(offset, limit):
            nonlocal call_count
            call_count += 1
            return [{"id": offset}], None
        paginator = OffsetPaginator(page_size=1, max_pages=3)
        all_records = [r for page in paginator.pages(fetch) for r in page]
        assert call_count == 3

    def test_empty_first_page(self):
        def fetch(offset, limit):
            return [], 0
        paginator = OffsetPaginator(page_size=10, max_pages=100)
        all_records = [r for page in paginator.pages(fetch) for r in page]
        assert len(all_records) == 0

    def test_stops_when_offset_reaches_total(self):
        def fetch(offset, limit):
            if offset == 0:
                return [{"id": 1}, {"id": 2}], 2
            return [{"id": 3}], 2
        paginator = OffsetPaginator(page_size=2, max_pages=100)
        all_records = [r for page in paginator.pages(fetch) for r in page]
        assert len(all_records) == 2


class TestCursorPaginator:
    def test_single_page_no_cursor(self):
        def fetch(cursor):
            return [{"id": 1}], None
        paginator = CursorPaginator(max_pages=100)
        all_records = [r for page in paginator.pages(fetch) for r in page]
        assert len(all_records) == 1

    def test_follows_cursor_chain(self):
        pages_data = {None: ([{"id": 1}], "c2"), "c2": ([{"id": 2}], "c3"), "c3": ([{"id": 3}], None)}
        def fetch(cursor):
            return pages_data[cursor]
        paginator = CursorPaginator(max_pages=100)
        all_records = [r for page in paginator.pages(fetch) for r in page]
        assert len(all_records) == 3

    def test_stops_at_max_pages(self):
        counter = {"n": 0}
        def fetch(cursor):
            counter["n"] += 1
            return [{"id": counter["n"]}], f"next_{counter['n']}"
        paginator = CursorPaginator(max_pages=2)
        all_records = [r for page in paginator.pages(fetch) for r in page]
        assert len(all_records) == 2

    def test_handles_numeric_cursor(self):
        def fetch(cursor):
            offset = cursor or 0
            if offset >= 6:
                return [], None
            return [{"id": offset}], offset + 3
        paginator = CursorPaginator(max_pages=100)
        all_records = [r for page in paginator.pages(fetch) for r in page]
        assert len(all_records) == 2


class TestLinkPaginator:
    def test_single_page_no_link(self):
        def fetch(url):
            return [{"id": 1}], None
        paginator = LinkPaginator(max_pages=100)
        all_records = [r for page in paginator.pages(fetch) for r in page]
        assert len(all_records) == 1

    def test_follows_next_links(self):
        pages_data = {
            None: ([{"id": 1}], "https://api.example.com/page2"),
            "https://api.example.com/page2": ([{"id": 2}], "https://api.example.com/page3"),
            "https://api.example.com/page3": ([{"id": 3}], None),
        }
        def fetch(url):
            return pages_data[url]
        paginator = LinkPaginator(max_pages=100)
        all_records = [r for page in paginator.pages(fetch) for r in page]
        assert len(all_records) == 3

    def test_stops_at_max_pages(self):
        counter = {"n": 0}
        def fetch(url):
            counter["n"] += 1
            return [{"id": counter["n"]}], f"https://api.example.com/page{counter['n'] + 1}"
        paginator = LinkPaginator(max_pages=2)
        all_records = [r for page in paginator.pages(fetch) for r in page]
        assert len(all_records) == 2


class TestTruncatedFlagDefaults:
    """`truncated` is a class attribute: safe to read before any iteration."""

    @pytest.mark.parametrize("cls,kwargs", [
        (OffsetPaginator, {}),
        (PagePaginator, {}),
        (CursorPaginator, {}),
        (LinkPaginator, {}),
        (NoPaginator, {}),
    ])
    def test_truncated_false_before_iterating(self, cls, kwargs):
        assert cls(**kwargs).truncated is False


class TestOffsetPaginatorTruncation:
    def _endless_fetch(self, offset, limit):
        return [{"id": offset}], None

    def test_cap_hit_sets_truncated(self):
        paginator = OffsetPaginator(page_size=1, max_pages=3)
        list(paginator.pages(self._endless_fetch))
        assert paginator.truncated is True

    def test_natural_end_of_data_not_truncated(self):
        def fetch(offset, limit):
            if offset == 0:
                return [{"id": 1}, {"id": 2}], 3
            return [{"id": 3}], 3
        paginator = OffsetPaginator(page_size=2, max_pages=100)
        list(paginator.pages(fetch))
        assert paginator.truncated is False

    def test_last_page_exactly_fills_cap_but_signals_end(self):
        """Second page is the last allowed one AND total says we're done:
        end-of-data, not a cap-hit."""
        def fetch(offset, limit):
            return [{"id": offset}, {"id": offset + 1}], 4
        paginator = OffsetPaginator(page_size=2, max_pages=2)
        records = [r for page in paginator.pages(fetch) for r in page]
        assert len(records) == 4
        assert paginator.truncated is False

    def test_exact_boundary_without_total_is_a_deliberate_false_positive(self):
        """Pins a decision, not an accident: with no `total` from the API,
        data ending exactly on the cap boundary is indistinguishable from a
        cap-hit, so we report truncated. Over-reporting a partial collection
        is far cheaper than under-reporting one."""
        def fetch(offset, limit):
            if offset >= 4:
                return [], None
            return [{"id": offset}, {"id": offset + 1}], None
        paginator = OffsetPaginator(page_size=2, max_pages=2)
        records = [r for page in paginator.pages(fetch) for r in page]
        assert len(records) == 4
        assert paginator.truncated is True

    def test_consumer_breaking_early_not_truncated(self):
        """An abandoned read is not a cap-hit: the else clause runs only when
        the generator is exhausted."""
        paginator = OffsetPaginator(page_size=1, max_pages=3)
        for _ in paginator.pages(self._endless_fetch):
            break
        assert paginator.truncated is False

    def test_second_pages_call_resets_flag(self):
        paginator = OffsetPaginator(page_size=1, max_pages=3)
        list(paginator.pages(self._endless_fetch))
        assert paginator.truncated is True
        list(paginator.pages(lambda offset, limit: ([], 0)))
        assert paginator.truncated is False


class TestPagePaginatorTruncation:
    def _endless_fetch(self, page, page_size):
        return [{"id": page}] * page_size, None

    def test_cap_hit_sets_truncated(self):
        paginator = PagePaginator(page_size=2, max_pages=3)
        list(paginator.pages(self._endless_fetch))
        assert paginator.truncated is True

    def test_natural_end_of_data_not_truncated(self):
        def fetch(page, page_size):
            if page == 1:
                return [{"id": 1}, {"id": 2}], None
            return [{"id": 3}], None  # short page: last one
        paginator = PagePaginator(page_size=2, max_pages=100)
        list(paginator.pages(fetch))
        assert paginator.truncated is False

    def test_last_page_exactly_fills_cap_but_signals_end(self):
        """The one allowed page fills the cap exactly AND total says done."""
        def fetch(page, page_size):
            return [{"id": 1}, {"id": 2}], 2
        paginator = PagePaginator(page_size=2, max_pages=1)
        records = [r for page in paginator.pages(fetch) for r in page]
        assert len(records) == 2
        assert paginator.truncated is False

    def test_exact_boundary_without_total_is_a_deliberate_false_positive(self):
        """Same pinned decision as OffsetPaginator: no `total`, data ends
        exactly on the cap boundary → reported truncated, conservatively."""
        def fetch(page, page_size):
            if page > 2:
                return [], None
            return [{"id": page}, {"id": page + 10}], None
        paginator = PagePaginator(page_size=2, max_pages=2)
        records = [r for page in paginator.pages(fetch) for r in page]
        assert len(records) == 4
        assert paginator.truncated is True

    def test_consumer_breaking_early_not_truncated(self):
        paginator = PagePaginator(page_size=2, max_pages=3)
        for _ in paginator.pages(self._endless_fetch):
            break
        assert paginator.truncated is False

    def test_second_pages_call_resets_flag(self):
        paginator = PagePaginator(page_size=2, max_pages=3)
        list(paginator.pages(self._endless_fetch))
        assert paginator.truncated is True
        list(paginator.pages(lambda page, page_size: ([], None)))
        assert paginator.truncated is False


class TestCursorPaginatorTruncation:
    def _endless_fetch(self, cursor):
        n = 0 if cursor is None else int(cursor)
        return [{"id": n}], str(n + 1)

    def test_cap_hit_sets_truncated(self):
        paginator = CursorPaginator(max_pages=3)
        list(paginator.pages(self._endless_fetch))
        assert paginator.truncated is True

    def test_natural_end_of_data_not_truncated(self):
        pages_data = {None: ([{"id": 1}], "c2"), "c2": ([{"id": 2}], None)}
        paginator = CursorPaginator(max_pages=100)
        list(paginator.pages(pages_data.get))
        assert paginator.truncated is False

    def test_last_page_exactly_fills_cap_but_signals_end(self):
        """The off-by-one: the page that fills the cap hands back no cursor —
        end-of-data, not truncation."""
        paginator = CursorPaginator(max_pages=1)
        records = [r for page in paginator.pages(lambda cursor: ([{"id": 1}], None)) for r in page]
        assert len(records) == 1
        assert paginator.truncated is False

    def test_consumer_breaking_early_not_truncated(self):
        paginator = CursorPaginator(max_pages=3)
        for _ in paginator.pages(self._endless_fetch):
            break
        assert paginator.truncated is False

    def test_second_pages_call_resets_flag(self):
        paginator = CursorPaginator(max_pages=3)
        list(paginator.pages(self._endless_fetch))
        assert paginator.truncated is True
        list(paginator.pages(lambda cursor: ([], None)))
        assert paginator.truncated is False


class TestLinkPaginatorTruncation:
    def _endless_fetch(self, url):
        n = 0 if url is None else int(url.rsplit("/page", 1)[1])
        return [{"id": n}], f"https://api.example.com/page{n + 1}"

    def test_cap_hit_sets_truncated(self):
        paginator = LinkPaginator(max_pages=3)
        list(paginator.pages(self._endless_fetch))
        assert paginator.truncated is True

    def test_natural_end_of_data_not_truncated(self):
        pages_data = {
            None: ([{"id": 1}], "https://api.example.com/p2"),
            "https://api.example.com/p2": ([{"id": 2}], None),
        }
        paginator = LinkPaginator(max_pages=100)
        list(paginator.pages(pages_data.get))
        assert paginator.truncated is False

    def test_last_page_exactly_fills_cap_but_signals_end(self):
        paginator = LinkPaginator(max_pages=1)
        records = [r for page in paginator.pages(lambda url: ([{"id": 1}], None)) for r in page]
        assert len(records) == 1
        assert paginator.truncated is False

    def test_consumer_breaking_early_not_truncated(self):
        paginator = LinkPaginator(max_pages=3)
        for _ in paginator.pages(self._endless_fetch):
            break
        assert paginator.truncated is False

    def test_second_pages_call_resets_flag(self):
        paginator = LinkPaginator(max_pages=3)
        list(paginator.pages(self._endless_fetch))
        assert paginator.truncated is True
        list(paginator.pages(lambda url: ([], None)))
        assert paginator.truncated is False


class TestNoPaginatorTruncation:
    def test_truncated_always_false(self):
        paginator = NoPaginator()
        list(paginator.pages(lambda: [{"id": 1}]))
        assert paginator.truncated is False
