# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["pypdf==7.2.0"]
# ///
"""Store a source document for a part and read a table row from it, exactly.

Usage: uv run --script "$CAD_SKILLS/cad-bom/scripts/source_extract.py" PN SOURCE [--hw HW_DIR] [--find TEXT]
       [--name FILE_NAME] [--accessed YYYY-MM-DD]

The lead runs this for each URL or file a sourcing researcher names; the researcher itself reads
local files only. SOURCE is an http(s) URL or a local file. The bytes go to
HW_DIR/parts/PN/sources/FILE_NAME (default HW_DIR "hw"; the name comes from the URL path or the file)
with a sidecar FILE_NAME.source.json holding origin, sha256, access date, size and kind. Record the
sha256 in part.yaml where it cites the file. A different file under an existing name is refused.

PDF: text per page (pypdf), as lines. HTML (stdlib parser): every <table> as a full grid, with each
colspan and rowspan cell repeated in every grid position it covers, so a label that spans two
columns cannot shift a value under the neighbouring size. The header of a column is its text in the
<thead> rows, or in the first row when the table has no <thead>.

--find TEXT matches case-insensitively. An HTML match is a grid row, printed whole with each cell's
column header, its table number and row number (both 1-based); a PDF match is a line with its page
and the line above it. A PDF has no grid: check the column order against the header line yourself.

stdout: JSON with pn, origin, stored (relative to HW_DIR), sha256, accessed, kind, pages or tables,
matches and findings. stderr: one line per finding. Exit 0 = read, 1 = at least one finding,
2 = bad arguments. Findings: http_<status>, network_error, file_not_found, unreadable_file,
unsupported_type, no_text_layer, name_conflict, no_match.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

PN = re.compile(r"^[A-Z]{2,5}-\d{4}$")
URL = re.compile(r"^https?://", re.IGNORECASE)
HTML_MARK = re.compile(r"<\s*(?:table|html)\b", re.IGNORECASE)
TIMEOUT_SECONDS = 30
MAX_SPAN = 100


class Finding(Exception):  # noqa: N818 - named for the report it becomes
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def squeeze(text: str) -> str:
    return " ".join(text.split())


# ---- reading the bytes


def read_source(source: str) -> bytes:
    if URL.match(source):
        return fetch(source)
    try:
        return Path(source).read_bytes()
    except FileNotFoundError:
        raise Finding("file_not_found", f"no file {source}") from None
    except OSError as error:
        raise Finding("unreadable_file", f"{source}: {error}") from None


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "benchhand-source-extract/1"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - http(s) only
            data: bytes = response.read()
            return data
    except urllib.error.HTTPError as error:
        raise Finding(f"http_{error.code}", f"{url} answered {error.code} {error.reason}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise Finding("network_error", f"{url}: {error}") from None


def classify(data: bytes, name: str) -> str:
    if data.startswith(b"%PDF"):
        return "pdf"
    if name.lower().endswith(".pdf"):
        raise Finding("unreadable_file", f"{name} is named a PDF but does not start with %PDF")
    if HTML_MARK.search(data.decode("utf-8", errors="replace")):
        return "html"
    raise Finding("unsupported_type", f"{name} is neither a PDF nor an HTML page with a table")


# ---- PDF


def pdf_pages(data: bytes) -> list[list[str]]:
    """Non-empty, whitespace-squeezed lines of each page."""
    from pypdf import PdfReader  # noqa: PLC0415 - imported where used so the HTML path needs no pypdf
    from pypdf.errors import PyPdfError  # noqa: PLC0415

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "").splitlines() for page in reader.pages]
    except (PyPdfError, ValueError, KeyError, TypeError, OSError) as error:
        raise Finding("unreadable_file", f"the PDF cannot be read: {error}") from None
    result = [[line for line in map(squeeze, lines) if line] for lines in pages]
    if not any(result):
        raise Finding("no_text_layer", "the PDF has no text layer (scanned or image only); supply a text PDF")
    return result


def find_in_pdf(pages: list[list[str]], needle: str) -> list[dict[str, Any]]:
    wanted = needle.lower()
    return [
        {"page": number, "line": line, "previous_line": lines[index - 1] if index else ""}
        for number, lines in enumerate(pages, 1)
        for index, line in enumerate(lines)
        if wanted in line.lower()
    ]


# ---- HTML tables


@dataclass
class Cell:
    text: str
    colspan: int
    rowspan: int


@dataclass
class RawTable:
    rows: list[list[Cell]]
    header_rows: int = 0
    in_head: bool = False


class TableParser(HTMLParser):
    """Collects every <table> (nested ones too) as rows of cells with their spans."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[RawTable] = []
        self._open: list[RawTable] = []
        self._cell: list[tuple[list[str], int, int]] = []
        self._skip = 0

    @staticmethod
    def _span(attrs: list[tuple[str, str | None]], name: str) -> int:
        value = dict(attrs).get(name) or "1"
        return min(int(value), MAX_SPAN) if value.isdigit() and int(value) > 0 else 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "table":
            table = RawTable(rows=[])
            self.tables.append(table)
            self._open.append(table)
        elif not self._open:
            return
        elif tag == "thead":
            self._open[-1].in_head = True
        elif tag == "tr":
            self._open[-1].rows.append([])
        elif tag in ("td", "th"):
            if not self._open[-1].rows:
                self._open[-1].rows.append([])
            self._cell.append(([], self._span(attrs, "colspan"), self._span(attrs, "rowspan")))
        elif tag == "br" and self._cell:
            self._cell[-1][0].append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "table" and self._open:
            self._open.pop()
        elif tag == "thead" and self._open:
            self._open[-1].in_head = False
        elif tag == "tr" and self._open and self._open[-1].in_head:
            self._open[-1].header_rows += 1
        elif tag in ("td", "th") and self._cell and self._open:
            parts, colspan, rowspan = self._cell.pop()
            self._open[-1].rows[-1].append(Cell(squeeze("".join(parts)), colspan, rowspan))

    def handle_data(self, data: str) -> None:
        if self._cell and not self._skip:
            self._cell[-1][0].append(data)


def expand(table: RawTable) -> list[list[str]]:
    """The grid: every cell's text in each (row, column) its colspan and rowspan cover."""
    placed: dict[tuple[int, int], str] = {}
    for row_index, row in enumerate(table.rows):
        column = 0
        for cell in row:
            while (row_index, column) in placed:
                column += 1
            for down in range(cell.rowspan):
                for across in range(cell.colspan):
                    placed.setdefault((row_index + down, column + across), cell.text)
            column += cell.colspan
    height = len(table.rows)
    width = max((c for _, c in placed), default=-1) + 1
    return [[placed.get((r, c), "") for c in range(width)] for r in range(height)]


def column_headers(grid: list[list[str]], header_rows: int) -> list[str]:
    count = max(header_rows, 1)
    headers = []
    for column in range(len(grid[0]) if grid else 0):
        parts: list[str] = []
        for row in grid[:count]:
            if not parts or row[column] != parts[-1]:
                parts.append(row[column])
        headers.append(" / ".join(p for p in parts if p))
    return headers


def html_tables(data: bytes) -> list[tuple[list[list[str]], list[str]]]:
    parser = TableParser()
    parser.feed(data.decode("utf-8", errors="replace"))
    parser.close()
    grids = [(expand(table), table.header_rows) for table in parser.tables]
    return [(grid, column_headers(grid, header_rows)) for grid, header_rows in grids if grid]


def find_in_tables(tables: list[tuple[list[list[str]], list[str]]], needle: str) -> list[dict[str, Any]]:
    wanted = needle.lower()
    return [
        {
            "table": number,
            "row": row_number,
            "text": " | ".join(row),
            "cells": [{"column": column, "header": headers[column], "value": value} for column, value in enumerate(row)],
        }
        for number, (grid, headers) in enumerate(tables, 1)
        for row_number, row in enumerate(grid, 1)
        if any(wanted in value.lower() for value in row)
    ]


# ---- storing


def file_name(source: str, kind: str, override: str | None) -> str:
    if override:
        name = override
    elif URL.match(source):
        name = Path(urllib.parse.urlparse(source).path).name or "index"
    else:
        name = Path(source).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip(".") or "source"
    return name if "." in name else f"{name}.{'pdf' if kind == 'pdf' else 'html'}"


def store(data: bytes, hw: Path, pn: str, name: str, record: dict[str, Any]) -> str:
    folder = hw / "parts" / pn / "sources"
    target = folder / name
    if target.exists() and target.read_bytes() != data:
        raise Finding("name_conflict", f"{target} exists with other content; pass --name to keep both")
    folder.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    sidecar = folder / f"{name}.source.json"
    sidecar.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target.relative_to(hw).as_posix()


# ---- command line


def parse_args(argv: list[str]) -> argparse.Namespace:
    def part_number(text: str) -> str:
        if not PN.match(text):
            raise argparse.ArgumentTypeError(f"{text!r} is not a part number such as PIN-0001")
        return text

    parser = argparse.ArgumentParser(description="store a source for a part and read table rows exactly")
    parser.add_argument("pn", type=part_number, help="part number, such as PIN-0001")
    parser.add_argument("source", help="http(s) URL or local file")
    parser.add_argument("--hw", type=Path, default=Path("hw"), help="record directory (default hw)")
    parser.add_argument("--find", help="print each row or line that contains this text")
    parser.add_argument("--name", help="file name under sources/ (default: from the source)")
    parser.add_argument("--accessed", type=date.fromisoformat, help="access date, YYYY-MM-DD (default today, UTC)")
    return parser.parse_args(argv)


def extract(args: argparse.Namespace, result: dict[str, Any]) -> None:
    """Fill `result` from the source; a Finding stops the run."""
    data = read_source(args.source)
    name_hint = file_name(args.source, "html", args.name)
    kind = classify(data, name_hint)
    pages = pdf_pages(data) if kind == "pdf" else []
    tables = html_tables(data) if kind == "html" else []
    accessed = (args.accessed or datetime.now(UTC).date()).isoformat()
    digest = hashlib.sha256(data).hexdigest()
    record = {"origin": args.source, "sha256": digest, "accessed": accessed, "bytes": len(data), "kind": kind}
    name = file_name(args.source, kind, args.name)
    result.update(kind=kind, sha256=digest, accessed=accessed, **({"pages": len(pages)} if kind == "pdf" else {"tables": len(tables)}))
    result["stored"] = store(data, args.hw, args.pn, name, record)
    if args.find is not None:
        matches = find_in_pdf(pages, args.find) if kind == "pdf" else find_in_tables(tables, args.find)
        result["matches"] = matches
        if not matches:
            raise Finding("no_match", f"{args.find!r} is in no {'line' if kind == 'pdf' else 'table row'} of {name}")


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as stop:
        return int(stop.code) if isinstance(stop.code, int) else 2
    result: dict[str, Any] = {"pn": args.pn, "origin": args.source, "findings": []}
    try:
        extract(args, result)
    except Finding as finding:
        result["findings"].append({"code": finding.code, "detail": finding.detail})
        print(f"source_extract: {finding.code}: {finding.detail}", file=sys.stderr)
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
