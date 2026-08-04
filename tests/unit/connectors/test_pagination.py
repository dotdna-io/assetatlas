# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for pagination helpers."""
import pytest
from src.modules.integrations.base.pagination import (
    OffsetPaginator, CursorPaginator, LinkPaginator
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
