# SPDX-License-Identifier: AGPL-3.0-or-later
"""Security-hardened CSV validator for enrichment file uploads.

Validates uploaded CSV files against security threats including
executable payloads, CSV injection, oversized content, and
malformed data. Files are never written to disk.
"""

import csv
import io
import logging
import re
from typing import List, Optional, Tuple

from werkzeug.datastructures import FileStorage

logger = logging.getLogger(__name__)

# Executable magic byte signatures to reject
_MAGIC_SIGNATURES = [
    b"\x7fELF",  # Linux ELF binary
    b"MZ",       # Windows PE executable
    b"PK",       # ZIP archive (also Office docs, JARs)
    b"#!",       # Shebang (shell script)
]

# CSV injection prefixes to strip from cell values
_INJECTION_PREFIXES = {"=", "+", "-", "@", "|"}

# Control characters to remove (excluding \t, \n, \r which are valid in CSV)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def validate_and_parse_csv(
    file: FileStorage,
    max_size_bytes: int,
    max_rows: int = 100_000,
    max_columns: int = 50,
    max_cell_length: int = 1_000,
) -> Tuple[Optional[List[dict]], Optional[str]]:
    """Validate and parse a CSV file upload.

    Args:
        file: Werkzeug FileStorage from the upload.
        max_size_bytes: Maximum allowed file size in bytes.
        max_rows: Maximum number of data rows (excluding header).
        max_columns: Maximum number of columns.
        max_cell_length: Maximum length of any single cell value.

    Returns:
        (rows, None) on success where rows is a list of dicts,
        or (None, error_message) on failure.
    """
    # 1. Extension check
    filename = file.filename or ""
    if not filename.lower().endswith(".csv"):
        return None, "File must have a .csv extension"

    # 2. Read content into memory (never write to disk)
    raw_bytes = file.stream.read()

    # 3. Size check
    if len(raw_bytes) > max_size_bytes:
        return None, f"File size {len(raw_bytes)} bytes exceeds maximum of {max_size_bytes} bytes"

    # 4. Empty file check
    if len(raw_bytes) == 0:
        return None, "File is empty"

    # 5. Magic bytes check — reject executable signatures
    for sig in _MAGIC_SIGNATURES:
        if raw_bytes[:len(sig)] == sig:
            return None, "File contains executable content (rejected by magic bytes check)"

    # 6. Null byte check
    if b"\x00" in raw_bytes:
        return None, "File contains null bytes"

    # 7. Decode (try UTF-8 first, fall back to Latin-1)
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1")

    # 8. Parse with csv.reader on StringIO
    reader = csv.reader(io.StringIO(text))

    # Read header
    try:
        headers = next(reader)
    except StopIteration:
        return None, "File is empty (no header row)"

    # 9. Strip BOM from first header if present
    if headers and headers[0].startswith("\ufeff"):
        headers[0] = headers[0].lstrip("\ufeff")

    # Strip whitespace from headers
    headers = [h.strip() for h in headers]

    # 10. Column count check
    num_cols = len(headers)
    if num_cols < 2:
        return None, f"CSV must have at least 2 columns, found {num_cols}"
    if num_cols > max_columns:
        return None, f"CSV has {num_cols} columns, maximum allowed is {max_columns}"

    # 11. Parse and sanitize rows
    rows: List[dict] = []
    for row_idx, raw_row in enumerate(reader, start=2):  # row 1 is header
        # Skip entirely empty rows
        if not raw_row or all(cell.strip() == "" for cell in raw_row):
            continue

        # Row count check
        if len(rows) >= max_rows:
            return None, f"CSV exceeds maximum of {max_rows} data rows"

        # Pad or truncate row to match header length
        if len(raw_row) < num_cols:
            raw_row = raw_row + [""] * (num_cols - len(raw_row))
        elif len(raw_row) > num_cols:
            raw_row = raw_row[:num_cols]

        # Sanitize each cell
        sanitized = []
        for cell in raw_row:
            # Cell length check
            if len(cell) > max_cell_length:
                return None, (
                    f"Cell at row {row_idx} exceeds maximum length "
                    f"of {max_cell_length} characters"
                )

            # Strip CSV injection prefixes
            while cell and cell[0] in _INJECTION_PREFIXES:
                cell = cell[1:]

            # Remove control characters
            cell = _CONTROL_CHAR_RE.sub("", cell)

            sanitized.append(cell)

        rows.append(dict(zip(headers, sanitized)))

    # 12. Must have at least one data row
    if len(rows) == 0:
        return None, "CSV has no data rows"

    logger.info(
        "CSV validated successfully: %d rows, %d columns, file=%s",
        len(rows),
        num_cols,
        filename,
    )

    return rows, None
