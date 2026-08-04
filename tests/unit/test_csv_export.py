# SPDX-License-Identifier: AGPL-3.0-or-later
from src.api.utils.csv_export import stream_csv

COLUMNS = [("hostname", "Hostname"), ("ip", "IP"), ("note", "Note")]


def test_header_and_rows_with_escaping_and_array_join():
    rows = [
        {"hostname": "web01", "ip": ["10.0.0.1", "10.0.0.2"], "note": "has,comma"},
        {"hostname": 'quo"te', "ip": None, "note": None},
    ]
    out = "".join(stream_csv(rows, COLUMNS))
    assert out == (
        "Hostname,IP,Note\r\n"
        'web01,10.0.0.1; 10.0.0.2,"has,comma"\r\n'
        '"quo""te",,\r\n'
    )


def test_empty_rows_yields_header_only():
    out = "".join(stream_csv([], COLUMNS))
    assert out == "Hostname,IP,Note\r\n"


def test_embedded_newline_is_quoted():
    out = "".join(stream_csv([{"hostname": "a\nb", "ip": "", "note": ""}], COLUMNS))
    assert out == "Hostname,IP,Note\r\n" '"a\nb",,\r\n'


def test_streams_one_chunk_per_row():
    chunks = list(stream_csv([{"hostname": "a"}, {"hostname": "b"}],
                             [("hostname", "Hostname")]))
    assert len(chunks) == 3  # header + 2 rows


# ---------------------------------------------------------------------------
# CSV / formula injection neutralization (#741)
#
# A cell whose stringified value begins with = + @ (or a leading TAB / CR), or
# with - NOT followed by an ASCII digit or '.', is prefixed with a single quote
# so a spreadsheet app (Excel / Sheets / LibreOffice) can't execute it as a
# formula on open. This convention is byte-for-byte identical to the frontend
# escapeCSVValue (ui/prod/src/pages/explorer/explorer-utils.ts); the neutralized
# CELL bytes below are mirrored verbatim in explorer-utils.test.ts. (The two
# serializers differ only in a pre-existing, unrelated way: stream_csv emits a
# trailing CRLF that generateCSV's join() does not — out of scope for #741.)
# ---------------------------------------------------------------------------

ONE = [("v", "V")]


def _csv(value):
    return "".join(stream_csv([{"v": value}], ONE))


def test_neutralizes_leading_equals():
    assert _csv("=1+1") == "V\r\n'=1+1\r\n"


def test_neutralizes_leading_at():
    assert _csv("@SUM(A1)") == "V\r\n'@SUM(A1)\r\n"


def test_leading_plus_is_neutralized_even_for_numbers():
    # '+' has no numeric exception (unlike '-'): "+1" is a formula in Excel.
    assert _csv("+1") == "V\r\n'+1\r\n"


def test_neutralizes_leading_tab():
    assert _csv("\t=1") == "V\r\n'\t=1\r\n"


def test_neutralizes_leading_cr_and_quotes_the_embedded_cr():
    # Leading CR triggers neutralization; the embedded CR also forces RFC-4180
    # quoting (csv.writer QUOTE_MINIMAL quotes on CR).
    assert _csv("\r=1") == 'V\r\n"\'\r=1"\r\n'


def test_negative_numbers_are_not_neutralized():
    # Legitimate negative numbers must round-trip unchanged.
    assert _csv("-5") == "V\r\n-5\r\n"
    assert _csv("-5.3") == "V\r\n-5.3\r\n"
    assert _csv("-.5") == "V\r\n-.5\r\n"


def test_leading_dash_then_nondigit_is_neutralized():
    assert _csv("-cmd|'/c calc'") == "V\r\n'-cmd|'/c calc'\r\n"
    assert _csv("-=1+1") == "V\r\n'-=1+1\r\n"  # classic DDE vector


def test_neutralization_precedes_quoting_when_value_has_comma():
    assert _csv("=a,b") == 'V\r\n"\'=a,b"\r\n'


def test_array_cell_is_joined_then_neutralized_as_a_whole():
    # join first, then a single leading quote — matches the frontend's
    # escapeCSVValue(val.join('; ')).
    assert _csv(["=a", "b"]) == "V\r\n'=a; b\r\n"


def test_dangerous_char_not_in_lead_position_is_untouched():
    assert _csv("a=b") == "V\r\na=b\r\n"


def test_safe_value_is_unchanged():
    assert _csv("host01") == "V\r\nhost01\r\n"


def test_header_label_is_also_neutralized_for_frontend_parity():
    # generateCSV runs header labels through escapeCSVValue too, so a custom
    # field display name like "=Total" must be neutralized on both sides or the
    # two outputs diverge.
    out = "".join(stream_csv([], [("v", "=Total")]))
    assert out == "'=Total\r\n"
