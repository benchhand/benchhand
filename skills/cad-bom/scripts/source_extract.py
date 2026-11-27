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
sha256 in part.yaml where it cites the file. The PN and the sources folder come from cad-record's layout
(`layout.resolve`, entry `record.part_sources`). No directory below HW_DIR may be a symbolic link, and
no file is replaced: the content is written to a temporary name and linked into place, the name and
its sidecar are both reserved, and an existing different file or sidecar is a name_conflict.

PDF: text per page (pypdf), as lines. HTML (stdlib parser): every <table> as a full grid, with each
colspan and rowspan cell repeated in every grid position it covers, so a label that spans two
columns cannot shift a value under the neighbouring size. The header of a column is its text in the
<thead> rows, or in the first row when the table has no <thead>.

--find TEXT matches case-insensitively. An HTML match is a grid row, printed whole with each cell's
column header, its table number and row number (both 1-based); a PDF match is a line with its page
and the line above it. A PDF has no grid: its result and each match carry `column_order: "unverified"`,
and the lead must check the column order against the header line (visually, in the stored PDF) before adopting a value.

stdout: JSON with pn, origin, stored (relative to HW_DIR), sha256, accessed, kind, pages or tables,
column_order (PDF only), matches and findings. stderr: one line per finding. Exit 0 = read, 1 = at least one finding,
2 = bad arguments. Findings: http_<status>, network_error, transfer_error, file_not_found, unreadable_file,
unsupported_type, no_text_layer, unsafe_name, symlink_refused, storage_error, name_conflict, no_match.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import io
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

LAYOUT_PATH = Path(__file__).resolve().parents[2] / "cad-record" / "scripts" / "layout.py"
URL = re.compile(r"^https?://", re.IGNORECASE)
HTML_MARK = re.compile(r"<\s*(?:table|html)\b", re.IGNORECASE)
TIMEOUT_SECONDS = 30
MAX_SPAN = 100
SIDECAR_SUFFIX = ".source.json"


def _cad_record(name: str, path: Path) -> Any:
    """A cad-record module under `name`, loaded once per process."""
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"{path} not found")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


LAYOUT = _cad_record("cad_record_layout", LAYOUT_PATH)


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
    except FileNotFoundError as error:
        raise Finding("file_not_found", f"no file {source}") from error
    except OSError as error:
        raise Finding("unreadable_file", f"{source}: {error}") from error


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "benchhand-source-extract/1"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - http(s) only
            data: bytes = response.read()
            return data
    except urllib.error.HTTPError as error:
        raise Finding(f"http_{error.code}", f"{url} answered {error.code} {error.reason}") from error
    except http.client.HTTPException as error:
        raise Finding("transfer_error", f"{url}: the response was cut short or malformed ({error!r})") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise Finding("network_error", f"{url}: {error}") from error


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
        raise Finding("unreadable_file", f"the PDF cannot be read: {error}") from error
    result = [[line for line in map(squeeze, lines) if line] for lines in pages]
    if not any(result):
        raise Finding("no_text_layer", "the PDF has no text layer (scanned or image only); supply a text PDF")
    return result


def find_in_pdf(pages: list[list[str]], needle: str) -> list[dict[str, Any]]:
    wanted = needle.lower()
    return [
        {"page": number, "line": line, "previous_line": lines[index - 1] if index else "", "column_order": "unverified"}
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
    cell: tuple[list[str], int, int] | None = None  # the open cell: text parts, colspan, rowspan

    def close_cell(self) -> None:
        """End the open cell, as `</td>`, `</th>`, the next cell, the next row or the end of the table does."""
        if self.cell is not None:
            parts, colspan, rowspan = self.cell
            self.cell = None
            self.rows[-1].append(Cell(squeeze("".join(parts)), colspan, rowspan))

    def end_head(self) -> None:
        if self.in_head:
            self.header_rows = len(self.rows)
            self.in_head = False


class TableParser(HTMLParser):
    """Collects every <table> (nested ones too) as rows of cells with their spans.

    Cells and rows close implicitly the way the HTML parsing rules say: a new cell, a new row, a row
    group or the end of the table ends the open cell, so a page that omits </td> and </tr> keeps every value."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[RawTable] = []
        self._open: list[RawTable] = []
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
        elif self._open:
            self._start_in_table(self._open[-1], tag, attrs)

    @staticmethod
    def _start_in_table(table: RawTable, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("thead", "tbody", "tfoot", "tr", "td", "th"):
            table.close_cell()
        if tag == "thead":
            table.in_head = True
        elif tag in ("tbody", "tfoot"):
            table.end_head()
        elif tag == "tr":
            table.rows.append([])
        elif tag in ("td", "th"):
            if not table.rows:
                table.rows.append([])
            table.cell = ([], TableParser._span(attrs, "colspan"), TableParser._span(attrs, "rowspan"))
        elif tag == "br" and table.cell is not None:
            table.cell[0].append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif not self._open:
            return
        elif tag == "table":
            self._open.pop().close_cell()
        elif tag in ("thead", "tr", "td", "th"):
            table = self._open[-1]
            table.close_cell()
            if tag == "thead":
                table.end_head()

    def handle_data(self, data: str) -> None:
        if self._open and self._open[-1].cell is not None and not self._skip:
            self._open[-1].cell[0].append(data)


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
    """The file name under sources/: the one given, else the last segment of the URL path or file path."""
    if override:
        name = override
    elif URL.match(source):
        name = urllib.parse.unquote(Path(urllib.parse.urlparse(source).path).name) or "index"
    else:
        name = Path(source).name
    return name if "." in name else f"{name}.{'pdf' if kind == 'pdf' else 'html'}"


def sources_folder(hw: Path, pn: str) -> Path:
    """The part's sources folder, by the shared layout entry (it checks the PN against its grammar)."""
    return Path(LAYOUT.resolve(hw, "record.part_sources", PN=pn))


def check_name(hw: Path, pn: str, name: str) -> None:
    """The name must be one file name that the layout accepts under the part's sources folder."""
    match = LAYOUT.classify(sources_folder(hw, pn) / name, hw)
    if (match is None or match.entry.id != "record.part_sources" or name.startswith(".") or not name.isprintable()
            or name != name.strip() or name.endswith(SIDECAR_SUFFIX)):
        raise Finding("unsafe_name", f"{name!r} is not a usable file name under sources/; pass --name")


def _is_link(directory_fd: int, name: str) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(name, dir_fd=directory_fd).st_mode)
    except OSError:
        return False


def _refuse_link(directory_fd: int, name: str) -> None:
    if _is_link(directory_fd, name):
        raise Finding("symlink_refused", f"{name} is a symbolic link; the output path may contain none")


def _open_directory(parent_fd: int, name: str) -> int:
    """Open (creating when absent) directory `name` below `parent_fd` without following a link."""
    try:
        os.mkdir(name, 0o777, dir_fd=parent_fd)
    except FileExistsError:
        pass
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as error:
        _refuse_link(parent_fd, name)
        raise Finding("storage_error", f"{name} is not a usable directory: {error}") from error


def _link_new(directory_fd: int, name: str, payload: bytes) -> bool:
    """Write `payload` under a temporary name and link it as `name`, never replacing; False when `name` exists."""
    temp = f".tmp-{uuid.uuid4().hex}"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=directory_fd)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        except FileExistsError:
            return False
        return True
    finally:
        os.unlink(temp, dir_fd=directory_fd)


def _read_existing(directory_fd: int, name: str) -> bytes:
    _refuse_link(directory_fd, name)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def _sidecar_digest(payload: bytes) -> object:
    try:
        return json.loads(payload).get("sha256")
    except (ValueError, AttributeError):
        return None


def _install(directory_fd: int, name: str, data: bytes, record: dict[str, Any]) -> None:
    """Reserve `name` and its sidecar without replacing either; an earlier identical store is accepted."""
    sidecar = name + SIDECAR_SUFFIX
    created = _link_new(directory_fd, name, data)
    try:
        if not created and _read_existing(directory_fd, name) != data:
            raise Finding("name_conflict", f"{name} exists with other content; pass --name to keep both")
        payload = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if not _link_new(directory_fd, sidecar, payload) and _sidecar_digest(_read_existing(directory_fd, sidecar)) != record["sha256"]:
            raise Finding("name_conflict", f"{sidecar} exists and does not describe this content; pass --name to keep both")
    except BaseException:
        if created:
            os.unlink(name, dir_fd=directory_fd)
        raise


def store(data: bytes, hw: Path, pn: str, name: str, record: dict[str, Any]) -> str:
    """Store `data` as sources/`name` with its sidecar; returns the path relative to `hw`.

    Every directory is opened with O_NOFOLLOW below `hw`, and files are linked into place, so a
    symbolic link anywhere on the output path is refused and no existing file is overwritten."""
    check_name(hw, pn, name)
    folder = sources_folder(hw, pn)
    descriptors: list[int] = []
    try:
        descriptors.append(os.open(hw.parent, os.O_RDONLY | os.O_DIRECTORY))
        for part in (hw.name, *folder.relative_to(hw).parts):
            descriptors.append(_open_directory(descriptors[-1], part))
        _install(descriptors[-1], name, data, record)
    except OSError as error:
        raise Finding("storage_error", f"cannot store {name} under {folder}: {error}") from error
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    return (folder / name).relative_to(hw).as_posix()


# ---- command line


def parse_args(argv: list[str]) -> argparse.Namespace:
    def part_number(text: str) -> str:
        try:
            LAYOUT.load().path("record.part_sources", {"PN": text})
        except LAYOUT.LayoutError as error:
            raise argparse.ArgumentTypeError(f"{text!r} is not a part number such as PIN-0001") from error
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
    hw = Path(os.path.abspath(args.hw))
    name_hint = file_name(args.source, "html", args.name)
    kind = classify(data, name_hint)
    pages = pdf_pages(data) if kind == "pdf" else []
    tables = html_tables(data) if kind == "html" else []
    accessed = (args.accessed or datetime.now(UTC).date()).isoformat()
    digest = hashlib.sha256(data).hexdigest()
    record = {"origin": args.source, "sha256": digest, "accessed": accessed, "bytes": len(data), "kind": kind}
    name = file_name(args.source, kind, args.name)
    result.update(kind=kind, sha256=digest, accessed=accessed, **({"pages": len(pages), "column_order": "unverified"} if kind == "pdf" else {"tables": len(tables)}))
    result["stored"] = store(data, hw, args.pn, name, record)
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
