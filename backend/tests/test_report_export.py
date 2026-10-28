"""CSV / XLSX / PDF writers (standard library only). Synthetic values; no database."""

import csv
import io
import re
import zlib
from datetime import UTC, date, datetime
from decimal import Decimal
from xml.etree import ElementTree
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import pytest

from app.services.report_export import Column, Table, safe_text, to_csv, to_pdf, to_xlsx

TZ = ZoneInfo("Asia/Kolkata")
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
COLUMNS = [Column("name", "Full name"), Column("days", "Days", "int"), Column("pay", "Net pay", "decimal"),
           Column("joined", "Joined", "date"), Column("at", "Recorded at", "datetime"), Column("ok", "Observed", "bool")]
ROW = {"name": "Asha Rao", "days": -3, "pay": Decimal("1234.50"), "joined": date(2024, 1, 1),
       "at": datetime(2026, 3, 2, 4, 30, tzinfo=UTC), "ok": True}
DANGEROUS = ["=HYPERLINK(\"http://x\")", "+1+1", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"]


def table(rows, columns=COLUMNS, title="Test report") -> Table:
    return Table(title=title, columns=columns, rows=rows, timezone=TZ, subtitle=["Scope: organization"])


@pytest.mark.parametrize("value", DANGEROUS)
def test_formula_like_text_is_neutralised(value) -> None:
    assert safe_text(value) == "'" + value
    assert safe_text("Plain - text") == "Plain - text"


# --- CSV ---------------------------------------------------------------------------------------------


def test_csv_types_headers_and_injection() -> None:
    rows = [ROW] + [{**ROW, "name": v} for v in DANGEROUS] + [{"name": None, "days": None}]
    content = to_csv(table(rows))
    assert content.startswith(b"\xef\xbb\xbf")
    parsed = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    assert parsed[0] == ["Full name", "Days", "Net pay", "Joined", "Recorded at", "Observed"]
    assert parsed[1] == ["Asha Rao", "-3", "1234.50", "2024-01-01", "2026-03-02T10:00:00+05:30", "yes"]  # numbers not prefixed
    assert [r[0] for r in parsed[2:8]] == ["'" + v for v in DANGEROUS]
    assert parsed[8] == ["", "", "", "", "", ""]


def test_csv_empty_report_has_only_headers() -> None:
    parsed = list(csv.reader(io.StringIO(to_csv(table([])).decode("utf-8-sig"))))
    assert parsed == [["Full name", "Days", "Net pay", "Joined", "Recorded at", "Observed"]]


# --- XLSX --------------------------------------------------------------------------------------------


def read_xlsx(content: bytes) -> tuple[ZipFile, ElementTree.Element]:
    package = ZipFile(io.BytesIO(content))
    assert package.testzip() is None
    for name in package.namelist():
        if name.endswith((".xml", ".rels")):
            ElementTree.fromstring(package.read(name))  # every part is well-formed XML
    return package, ElementTree.fromstring(package.read("xl/worksheets/sheet1.xml"))


def cells(sheet) -> list[list[tuple[str | None, str]]]:
    out = []
    for row in sheet.findall(".//m:sheetData/m:row", NS):
        values = []
        for c in row.findall("m:c", NS):
            text = c.find("m:is/m:t", NS)
            value = c.find("m:v", NS)
            values.append((c.get("t"), text.text if text is not None else value.text))
        out.append(values)
    return out


def test_xlsx_package_typed_cells_and_no_formulas() -> None:
    rows = [ROW, {**ROW, "name": "=1+1", "ok": False}, {"name": "Bad\x01char", "days": None}]
    package, sheet = read_xlsx(to_xlsx(table(rows, title="Leave: report [x]")))
    assert set(package.namelist()) == {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels",
                                       "xl/styles.xml", "xl/worksheets/sheet1.xml"}
    workbook = ElementTree.fromstring(package.read("xl/workbook.xml"))
    assert workbook.find(".//m:sheet", NS).get("name") == "Leave  report  x "  # forbidden characters removed
    data = cells(sheet)
    assert [v for _, v in data[0]] == ["Full name", "Days", "Net pay", "Joined", "Recorded at", "Observed"]
    assert data[1][0] == ("inlineStr", "Asha Rao") and data[1][1] == (None, "-3") and data[1][2] == (None, "1234.50")
    assert data[1][3] == (None, "45292")  # 2024-01-01 as an Excel date serial
    assert abs(float(data[1][4][1]) - 46083.4166666) < 1e-6  # 2026-03-02 10:00 org-local
    assert data[1][5] == ("b", "1") and data[2][5] == ("b", "0")
    assert data[2][0] == ("inlineStr", "'=1+1")
    assert data[3][0] == ("inlineStr", "Badchar") and len(data[3]) == 1  # control character stripped, empty cells omitted
    assert not sheet.findall(".//m:f", NS)
    assert sheet.find(".//m:pane", NS).get("state") == "frozen"


# --- PDF ---------------------------------------------------------------------------------------------


def pdf_streams(content: bytes) -> list[str]:
    return [zlib.decompress(m.group(1)).decode("cp1252")
            for m in re.finditer(rb"stream\n(.*?)\nendstream", content, re.S)]


def assert_valid_pdf(content: bytes) -> int:
    assert content.startswith(b"%PDF-1.4") and content.rstrip().endswith(b"%%EOF")
    xref = int(re.search(rb"startxref\n(\d+)", content).group(1))
    assert content[xref:xref + 4] == b"xref"
    count = int(re.search(rb"xref\n0 (\d+)", content).group(1))
    offsets = [int(o) for o in re.findall(rb"(\d{10}) 00000 n", content)]
    assert len(offsets) == count - 1
    for number, offset in enumerate(offsets, start=1):
        assert content[offset:].startswith(f"{number} 0 obj".encode())
    return int(re.search(rb"/Type /Pages /Kids \[[^\]]*\] /Count (\d+)", content).group(1))


def test_pdf_paginates_and_repeats_headers() -> None:
    rows = [{**ROW, "name": f"Person {i} (team)"} for i in range(130)]
    content = to_pdf(table(rows), footer="Test report")
    pages = assert_valid_pdf(content)
    streams = pdf_streams(content)
    assert pages == len(streams) >= 3
    for number, stream in enumerate(streams, start=1):
        assert "(Full name) Tj" in stream and f"Page {number} of {pages}" in stream
    joined = "".join(streams)
    assert all(f"(Person {i} \\(team\\)) Tj" in joined for i in range(130))  # every row, parentheses escaped


def test_pdf_shortens_long_text_and_replaces_unsupported_characters() -> None:
    rows = [{"name": "x" * 500 + "TAIL", "days": 1}, {"name": "Ūnicode 名前", "days": 2}]
    streams = "".join(pdf_streams(to_pdf(table(rows, columns=COLUMNS[:2]), footer="f")))
    assert "TAIL" not in streams and "..." in streams
    assert "?" in streams


def test_pdf_empty_report() -> None:
    content = to_pdf(table([]), footer="f")
    assert assert_valid_pdf(content) == 1 and "No rows match these filters." in pdf_streams(content)[0]


def test_unknown_column_kind_rejected() -> None:
    with pytest.raises(ValueError):
        Column("x", "X", "formula")
