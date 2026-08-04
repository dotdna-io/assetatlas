# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the enrichment CSV validator."""

import io

import pytest
from werkzeug.datastructures import FileStorage

from src.modules.enrichment.services.csv_validator import validate_and_parse_csv


def _make_file(content: bytes, filename: str = "test.csv") -> FileStorage:
    """Helper to create a FileStorage object from bytes."""
    return FileStorage(
        stream=io.BytesIO(content),
        filename=filename,
        content_type="text/csv",
    )


# --- Happy path ---


class TestValidCSV:
    def test_basic_csv_parsed_correctly(self):
        csv_bytes = b"hostname,ip\nserver1,10.0.0.1\nserver2,10.0.0.2\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        assert rows == [
            {"hostname": "server1", "ip": "10.0.0.1"},
            {"hostname": "server2", "ip": "10.0.0.2"},
        ]

    def test_csv_with_whitespace_values(self):
        csv_bytes = b"name,value\n  hello ,  world \n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        assert rows[0]["name"] == "  hello "
        assert rows[0]["value"] == "  world "

    def test_csv_with_quoted_fields(self):
        csv_bytes = b'name,desc\n"Smith, John","A ""quoted"" value"\n'
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        assert rows[0]["name"] == "Smith, John"
        assert rows[0]["desc"] == 'A "quoted" value'

    def test_utf8_content(self):
        csv_bytes = "hostname,city\nserver1,M\u00fcnchen\n".encode("utf-8")
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        assert rows[0]["city"] == "M\u00fcnchen"

    def test_latin1_fallback(self):
        csv_bytes = "hostname,city\nserver1,M\u00fcnchen\n".encode("latin-1")
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        assert rows[0]["city"] == "M\u00fcnchen"

    def test_bom_stripped_from_first_header(self):
        csv_bytes = b"\xef\xbb\xbfhostname,ip\nserver1,10.0.0.1\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        assert "hostname" in rows[0]
        assert "\ufeffhostname" not in rows[0]


# --- Extension check ---


class TestExtensionCheck:
    def test_non_csv_extension_rejected(self):
        csv_bytes = b"hostname,ip\nserver1,10.0.0.1\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes, filename="test.xlsx"), max_size_bytes=1024)
        assert rows is None
        assert "extension" in err.lower() or ".csv" in err.lower()

    def test_no_extension_rejected(self):
        csv_bytes = b"hostname,ip\nserver1,10.0.0.1\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes, filename="testfile"), max_size_bytes=1024)
        assert rows is None
        assert err is not None

    def test_uppercase_csv_extension_accepted(self):
        csv_bytes = b"hostname,ip\nserver1,10.0.0.1\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes, filename="test.CSV"), max_size_bytes=1024)
        assert err is None
        assert len(rows) == 1

    def test_path_traversal_filename_handled(self):
        csv_bytes = b"hostname,ip\nserver1,10.0.0.1\n"
        rows, err = validate_and_parse_csv(
            _make_file(csv_bytes, filename="../../etc/passwd.csv"),
            max_size_bytes=1024,
        )
        # Should either accept (filename has .csv) or reject safely.
        # The key is that no path traversal happens (file is never written to disk).
        # Since it ends with .csv, it should be accepted.
        assert err is None
        assert len(rows) == 1


# --- Size check ---


class TestSizeCheck:
    def test_oversized_file_rejected(self):
        csv_bytes = b"a,b\n" + b"x,y\n" * 10000
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=100)
        assert rows is None
        assert "size" in err.lower()

    def test_empty_file_rejected(self):
        rows, err = validate_and_parse_csv(_make_file(b""), max_size_bytes=1024)
        assert rows is None
        assert "empty" in err.lower()


# --- Magic bytes check ---


class TestMagicBytesCheck:
    def test_elf_binary_rejected(self):
        content = b"\x7fELF" + b"\x00" * 100
        rows, err = validate_and_parse_csv(_make_file(content), max_size_bytes=1024)
        assert rows is None
        assert err is not None

    def test_mz_executable_rejected(self):
        content = b"MZ" + b"\x00" * 100
        rows, err = validate_and_parse_csv(_make_file(content), max_size_bytes=1024)
        assert rows is None
        assert err is not None

    def test_pk_zip_rejected(self):
        content = b"PK" + b"\x00" * 100
        rows, err = validate_and_parse_csv(_make_file(content), max_size_bytes=1024)
        assert rows is None
        assert err is not None

    def test_shebang_rejected(self):
        content = b"#!/bin/bash\necho pwned\n"
        rows, err = validate_and_parse_csv(_make_file(content), max_size_bytes=1024)
        assert rows is None
        assert err is not None


# --- Null byte check ---


class TestNullByteCheck:
    def test_null_bytes_rejected(self):
        content = b"hostname,ip\nserver1\x00,10.0.0.1\n"
        rows, err = validate_and_parse_csv(_make_file(content), max_size_bytes=1024)
        assert rows is None
        assert "null" in err.lower() or "byte" in err.lower()


# --- Row / column limits ---


class TestRowColumnLimits:
    def test_too_many_rows_rejected(self):
        header = b"a,b\n"
        data_rows = b"x,y\n" * 11
        rows, err = validate_and_parse_csv(
            _make_file(header + data_rows),
            max_size_bytes=1_000_000,
            max_rows=10,
        )
        assert rows is None
        assert "row" in err.lower()

    def test_exact_max_rows_accepted(self):
        header = b"a,b\n"
        data_rows = b"x,y\n" * 10
        rows, err = validate_and_parse_csv(
            _make_file(header + data_rows),
            max_size_bytes=1_000_000,
            max_rows=10,
        )
        assert err is None
        assert len(rows) == 10

    def test_too_many_columns_rejected(self):
        headers = ",".join(f"col{i}" for i in range(51))
        values = ",".join("x" for _ in range(51))
        csv_bytes = f"{headers}\n{values}\n".encode()
        rows, err = validate_and_parse_csv(
            _make_file(csv_bytes),
            max_size_bytes=1_000_000,
            max_columns=50,
        )
        assert rows is None
        assert "column" in err.lower()

    def test_single_column_rejected(self):
        csv_bytes = b"hostname\nserver1\nserver2\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert rows is None
        assert "column" in err.lower()

    def test_no_data_rows_rejected(self):
        csv_bytes = b"hostname,ip\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert rows is None
        assert "row" in err.lower() or "data" in err.lower() or "empty" in err.lower()


# --- CSV injection / sanitization ---


class TestCSVInjection:
    def test_equals_prefix_stripped(self):
        csv_bytes = b"name,formula\ntest,=cmd|' /C calc'\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        assert not rows[0]["formula"].startswith("=")

    def test_plus_prefix_stripped(self):
        csv_bytes = b"name,val\ntest,+cmd|' /C calc'\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        assert not rows[0]["val"].startswith("+")

    def test_at_prefix_stripped(self):
        csv_bytes = b"name,val\ntest,@SUM(A1:A10)\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        assert not rows[0]["val"].startswith("@")

    def test_pipe_prefix_stripped(self):
        csv_bytes = b"name,val\ntest,|cmd\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        assert not rows[0]["val"].startswith("|")

    def test_nested_injection_prefixes_stripped(self):
        csv_bytes = b"name,val\ntest,=+@|=-cmd\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        val = rows[0]["val"]
        assert val[0] not in ("=", "+", "-", "@", "|")

    def test_minus_prefix_stripped(self):
        csv_bytes = b"name,val\ntest,-cmd|' /C calc'\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        assert not rows[0]["val"].startswith("-")


# --- Cell length ---


class TestCellLength:
    def test_cell_length_limit_enforced(self):
        long_value = "x" * 1001
        csv_bytes = f"name,val\ntest,{long_value}\n".encode()
        rows, err = validate_and_parse_csv(
            _make_file(csv_bytes),
            max_size_bytes=1_000_000,
            max_cell_length=1000,
        )
        assert rows is None
        assert "cell" in err.lower() or "length" in err.lower()

    def test_cell_at_exact_limit_accepted(self):
        exact_value = "x" * 1000
        csv_bytes = f"name,val\ntest,{exact_value}\n".encode()
        rows, err = validate_and_parse_csv(
            _make_file(csv_bytes),
            max_size_bytes=1_000_000,
            max_cell_length=1000,
        )
        assert err is None
        assert len(rows) == 1


# --- Row padding / truncation ---


class TestRowPaddingTruncation:
    def test_short_row_padded(self):
        csv_bytes = b"a,b,c\n1\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        assert rows[0] == {"a": "1", "b": "", "c": ""}

    def test_long_row_truncated(self):
        csv_bytes = b"a,b\n1,2,3,4\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        assert rows[0] == {"a": "1", "b": "2"}

    def test_empty_rows_skipped(self):
        csv_bytes = b"a,b\n1,2\n\n\n3,4\n"
        rows, err = validate_and_parse_csv(_make_file(csv_bytes), max_size_bytes=1024)
        assert err is None
        assert len(rows) == 2
        assert rows[0] == {"a": "1", "b": "2"}
        assert rows[1] == {"a": "3", "b": "4"}
