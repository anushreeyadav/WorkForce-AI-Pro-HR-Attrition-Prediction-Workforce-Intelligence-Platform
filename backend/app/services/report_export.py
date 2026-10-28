"""Report file writers: CSV, XLSX (Office Open XML) and PDF, using only the Python standard library.

No third-party export package is installed, and these formats need only a small, well-defined subset:
- CSV: UTF-8 with BOM (so spreadsheet apps detect the encoding), RFC 4180 quoting.
- XLSX: one worksheet, typed cells (numbers, dates and booleans as values, text as inline strings), bold frozen
  header row. Formulas are never written.
- PDF 1.4: A4 landscape, built-in Helvetica (WinAnsi), header row repeated on every page, "Page i of n" footer,
  long cell text shortened with "...". Characters outside Windows-1252 are shown as "?".

Text from the database is untrusted. Any text value starting with =, +, -, @, tab or carriage return is
prefixed with an apostrophe in CSV and XLSX, so spreadsheet applications never evaluate it as a formula
(OWASP "CSV injection"). Numbers, dates and booleans are typed values and are never prefixed.
"""

import csv
import io
import re
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
from zoneinfo import ZoneInfo

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
KINDS = ("text", "int", "decimal", "date", "datetime", "bool")


@dataclass(frozen=True)
class Column:
    key: str
    header: str
    kind: str = "text"

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"Unknown column kind {self.kind}")


@dataclass
class Table:
    title: str
    columns: list[Column]
    rows: list[dict]
    timezone: ZoneInfo
    subtitle: list[str] = field(default_factory=list)


def safe_text(value: str) -> str:
    """Neutralise spreadsheet formula injection in an untrusted text value."""
    return "'" + value if value.startswith(FORMULA_PREFIXES) else value


def _local(value: datetime, tz: ZoneInfo) -> datetime:
    return value.astimezone(tz)


def display(value, kind: str, tz: ZoneInfo) -> str:
    """Text form used by CSV and PDF."""
    if value is None:
        return ""
    if kind == "datetime":
        return _local(value, tz).isoformat(timespec="seconds")
    if kind == "date":
        return value.isoformat()
    if kind == "bool":
        return "yes" if value else "no"
    if kind == "decimal":
        return f"{Decimal(value):.2f}" if isinstance(value, Decimal) else f"{value:.4f}".rstrip("0").rstrip(".")
    if kind == "int":
        return str(int(value))
    return safe_text(str(value))


# --- CSV ----------------------------------------------------------------------------------------------


def to_csv(table: Table) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([safe_text(c.header) for c in table.columns])
    for row in table.rows:
        writer.writerow([display(row.get(c.key), c.kind, table.timezone) for c in table.columns])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


# --- XLSX ---------------------------------------------------------------------------------------------

_EXCEL_EPOCH = datetime(1899, 12, 30)
# cellXfs indexes in styles.xml
_STYLE = {"header": 1, "date": 2, "datetime": 3, "decimal": 4}


def _column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _xml_text(value: str) -> str:
    return escape(_XML_ILLEGAL.sub("", value)[:32767])


def _cell(ref: str, value, kind: str, tz: ZoneInfo) -> str:
    if value is None:
        return ""
    if kind == "bool":
        return f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>'
    if kind == "int":
        return f'<c r="{ref}"><v>{int(value)}</v></c>'
    if kind == "decimal":
        return f'<c r="{ref}" s="{_STYLE["decimal"]}"><v>{value}</v></c>'
    if kind == "date":
        serial = (datetime(value.year, value.month, value.day) - _EXCEL_EPOCH).days
        return f'<c r="{ref}" s="{_STYLE["date"]}"><v>{serial}</v></c>'
    if kind == "datetime":
        local = _local(value, tz).replace(tzinfo=None)
        serial = (local - _EXCEL_EPOCH).total_seconds() / 86400
        return f'<c r="{ref}" s="{_STYLE["datetime"]}"><v>{serial:.8f}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_xml_text(safe_text(str(value)))}</t></is></c>'


def _sheet_name(title: str) -> str:
    return re.sub(r"[\[\]:*?/\\]", " ", title)[:31] or "Report"


def to_xlsx(table: Table) -> bytes:
    columns = table.columns
    widths = []
    for c in columns:
        longest = max([len(c.header)] + [len(display(r.get(c.key), c.kind, table.timezone)) for r in table.rows[:500]])
        widths.append(min(max(longest + 2, 10), 60))
    header = "".join(f'<c r="{_column_letter(i)}1" t="inlineStr" s="{_STYLE["header"]}"><is><t xml:space="preserve">'
                     f'{_xml_text(safe_text(c.header))}</t></is></c>' for i, c in enumerate(columns))
    body = [f'<row r="1">{header}</row>']
    for r, row in enumerate(table.rows, start=2):
        cells = "".join(_cell(f"{_column_letter(i)}{r}", row.get(c.key), c.kind, table.timezone) for i, c in enumerate(columns))
        body.append(f'<row r="{r}">{cells}</row>')
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
    last = f"{_column_letter(len(columns) - 1)}{len(table.rows) + 1}"
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<dimension ref="A1:{last}"/>'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
             'state="frozen"/></sheetView></sheetViews>'
             f'<cols>{cols}</cols><sheetData>{"".join(body)}</sheetData></worksheet>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f'<sheets><sheet name="{escape(_sheet_name(table.title))}" sheetId="1" r:id="rId1"/></sheets></workbook>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm"/></numFmts>'
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
              '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="5"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
              '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
              '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
              '<xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                     '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>')
    workbook_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                     '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                     '</Relationships>')
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as package:
        for name, data in (("[Content_Types].xml", content_types), ("_rels/.rels", root_rels),
                           ("xl/workbook.xml", workbook), ("xl/_rels/workbook.xml.rels", workbook_rels),
                           ("xl/styles.xml", styles), ("xl/worksheets/sheet1.xml", sheet)):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            package.writestr(info, data.encode("utf-8"))
    return buffer.getvalue()


# --- PDF ----------------------------------------------------------------------------------------------

PAGE_WIDTH, PAGE_HEIGHT, MARGIN = 842, 595, 36  # A4 landscape, points
FONT_SIZE, ROW_HEIGHT = 7, 10
CHAR_WIDTH = 0.52 * FONT_SIZE  # conservative average Helvetica advance


def _pdf_text(value: str) -> str:
    encoded = value.encode("cp1252", errors="replace").decode("cp1252")
    return encoded.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").replace("\r", " ").replace("\n", " ")


def _fit(value: str, width: float) -> str:
    limit = max(int(width / CHAR_WIDTH) - 1, 3)
    return value if len(value) <= limit else value[:limit - 3] + "..."


def to_pdf(table: Table, footer: str) -> bytes:
    columns = table.columns
    texts = [[display(row.get(c.key), c.kind, table.timezone) for c in columns] for row in table.rows]
    weights = [min(max([len(c.header)] + [len(t[i]) for t in texts[:500]]), 40) + 2 for i, c in enumerate(columns)]
    usable = PAGE_WIDTH - 2 * MARGIN
    widths = [usable * w / sum(weights) for w in weights]
    xs = [MARGIN + sum(widths[:i]) for i in range(len(columns))]

    top = PAGE_HEIGHT - MARGIN - 16 - 10 * len(table.subtitle) - 6  # below title and subtitle lines
    per_page = max(int((top - MARGIN - 14) / ROW_HEIGHT) - 1, 1)
    pages = [texts[i:i + per_page] for i in range(0, len(texts), per_page)] or [[]]

    streams = []
    for number, page_rows in enumerate(pages, start=1):
        ops = ["BT /F2 12 Tf", f"{MARGIN} {PAGE_HEIGHT - MARGIN - 12} Td ({_pdf_text(table.title)}) Tj ET"]
        for i, line in enumerate(table.subtitle):
            ops.append(f"BT /F1 8 Tf {MARGIN} {PAGE_HEIGHT - MARGIN - 26 - 10 * i} Td ({_pdf_text(line)}) Tj ET")
        y = top
        for i, c in enumerate(columns):
            ops.append(f"BT /F2 {FONT_SIZE} Tf {xs[i]:.1f} {y:.1f} Td ({_pdf_text(_fit(c.header, widths[i]))}) Tj ET")
        ops.append(f"0.5 w {MARGIN} {y - 3:.1f} m {PAGE_WIDTH - MARGIN} {y - 3:.1f} l S")
        for row in page_rows:
            y -= ROW_HEIGHT
            for i, value in enumerate(row):
                if value:
                    ops.append(f"BT /F1 {FONT_SIZE} Tf {xs[i]:.1f} {y:.1f} Td ({_pdf_text(_fit(value, widths[i]))}) Tj ET")
        if not texts:
            ops.append(f"BT /F1 9 Tf {MARGIN} {y - 16:.1f} Td (No rows match these filters.) Tj ET")
        ops.append(f"BT /F1 7 Tf {MARGIN} {MARGIN - 14} Td ({_pdf_text(f'{footer}   Page {number} of {len(pages)}')}) Tj ET")
        streams.append(zlib.compress("\n".join(ops).encode("cp1252", errors="replace")))

    objects: list[bytes] = []
    page_ids = [5 + 2 * i for i in range(len(pages))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    for i, stream in enumerate(streams):
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                       f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {page_ids[i] + 1} 0 R >>".encode())
        objects.append(f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n".encode() + stream + b"\nendstream")
    info_id = len(objects) + 1
    objects.append(f"<< /Title ({_pdf_text(table.title)}) /Producer (WorkForce AI Pro) >>".encode())

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()
