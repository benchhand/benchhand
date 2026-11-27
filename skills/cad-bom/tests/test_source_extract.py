#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""source_extract.py tests. Fixtures are local files; the HTTP cases use a server on 127.0.0.1.

Expected values are typed from the fixtures: `thumb_screw_table.html` copies the DIN 653 layout of
https://fasteners.eu/standards/din/653 (label cells span two or three columns), and
`insert_table.pdf` holds the CNC Kitchen M4 insert row (length 8.1, outside diameter 6.3, hole 5.7)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from modload import load_module  # noqa: E402

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
HTML = FIXTURES / "thumb_screw_table.html"
PDF = FIXTURES / "insert_table.pdf"
NO_TEXT_PDF = FIXTURES / "no_text.pdf"
extract = load_module("cad_source_extract", HERE.parent / "scripts" / "source_extract.py")


def run(*argv: str) -> tuple[int, dict]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = extract.main(list(argv))
    return code, (json.loads(out.getvalue()) if out.getvalue() else {})


def by_header(match: dict) -> dict[str, str]:
    """The value under each size header of one matched row (label columns excluded)."""
    return {c["header"]: c["value"] for c in match["cells"] if c["header"] != "Thread d"}


class ExtractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.hw = Path(self._tmp.name) / "hw"

    def args(self, source: Path | str, *more: str) -> list[str]:
        return ["PIN-0001", str(source), "--hw", str(self.hw), "--accessed", "2026-11-27", *more]

    def test_spanned_label_cells_do_not_shift_values_between_sizes(self) -> None:
        code, result = run(*self.args(HTML, "--find", "dk"))
        self.assertEqual(code, 0)
        self.assertEqual([(m["table"], m["row"]) for m in result["matches"]], [(2, 3), (2, 4), (2, 5)])
        nominal, maximum, minimum = (by_header(m) for m in result["matches"])
        self.assertEqual(nominal, {"M3": "12", "(M3,5)": "14", "M4": "16"})
        self.assertEqual(maximum, {"M3": "12,35", "(M3,5)": "14,35", "M4": "16,35"})
        self.assertEqual(minimum, {"M3": "11,65", "(M3,5)": "13,65", "M4": "15,65"})

    def test_a_matched_row_repeats_the_spanned_label_in_each_column_it_covers(self) -> None:
        _, result = run(*self.args(HTML, "--find", "dk"))
        self.assertEqual(result["matches"][1]["text"], "dk | dk | max. | 12,35 | 14,35 | 16,35")
        self.assertEqual([c["header"] for c in result["matches"][1]["cells"]],
                         ["Thread d", "Thread d", "Thread d", "M3", "(M3,5)", "M4"])

    def test_pdf_match_gives_page_and_verbatim_line_with_the_line_above(self) -> None:
        code, result = run(*self.args(PDF, "--find", "M4 x"))
        self.assertEqual(code, 0)
        self.assertEqual(result["kind"], "pdf")
        self.assertEqual(result["matches"], [{"page": 2, "line": "M4 x 8.1 6.3 5.7", "previous_line": "M3 x 5.7 4.6 4.0"}])

    def test_pdf_page_without_the_text_is_not_matched_on_the_wrong_page(self) -> None:
        _, result = run(*self.args(PDF, "--find", "Outside diameter"))
        self.assertEqual([m["page"] for m in result["matches"]], [1])

    def test_source_is_stored_with_sha256_and_access_date(self) -> None:
        code, result = run(*self.args(HTML))
        self.assertEqual(code, 0)
        stored = self.hw / "parts" / "PIN-0001" / "sources" / "thumb_screw_table.html"
        self.assertEqual(stored.read_bytes(), HTML.read_bytes())
        self.assertEqual(result["sha256"], hashlib.sha256(HTML.read_bytes()).hexdigest())
        self.assertEqual(result["accessed"], "2026-11-27")
        self.assertEqual(result["stored"], "parts/PIN-0001/sources/thumb_screw_table.html")
        record = json.loads(stored.with_name("thumb_screw_table.html.source.json").read_text(encoding="utf-8"))
        self.assertEqual(record, {"origin": str(HTML), "sha256": result["sha256"], "accessed": "2026-11-27",
                                  "bytes": HTML.stat().st_size, "kind": "html"})
        self.assertEqual(result["tables"], 2)

    def test_storing_the_same_name_twice_is_idempotent_but_a_different_file_is_refused(self) -> None:
        self.assertEqual(run(*self.args(HTML))[0], 0)
        self.assertEqual(run(*self.args(HTML))[0], 0)
        other = Path(self._tmp.name) / "thumb_screw_table.html"
        other.write_text(HTML.read_text(encoding="utf-8") + "<!-- changed -->", encoding="utf-8")
        code, result = run(*self.args(other))
        self.assertEqual(code, 1)
        self.assertEqual([f["code"] for f in result["findings"]], ["name_conflict"])

    def test_a_find_without_a_match_is_a_finding(self) -> None:
        code, result = run(*self.args(HTML, "--find", "ds"))
        self.assertEqual((code, result["matches"]), (1, []))
        self.assertEqual([f["code"] for f in result["findings"]], ["no_match"])

    def test_unreadable_and_missing_files_are_findings_not_usage_errors(self) -> None:
        broken = Path(self._tmp.name) / "broken.pdf"
        broken.write_bytes(b"this is not a pdf")
        cases = {
            "unreadable_file": broken,
            "file_not_found": Path(self._tmp.name) / "absent.pdf",
        }
        for code_name, source in cases.items():
            with self.subTest(code_name):
                code, result = run(*self.args(source, "--find", "M4"))
                self.assertEqual(code, 1)
                self.assertEqual([f["code"] for f in result["findings"]], [code_name])
        self.assertFalse((self.hw / "parts" / "PIN-0001" / "sources" / "broken.pdf").exists())

    def test_a_pdf_without_a_text_layer_is_a_finding(self) -> None:
        code, result = run(*self.args(NO_TEXT_PDF, "--find", "M4"))
        self.assertEqual(code, 1)
        self.assertEqual([f["code"] for f in result["findings"]], ["no_text_layer"])

    def test_unrecognised_content_is_a_finding(self) -> None:
        plain = Path(self._tmp.name) / "notes.txt"
        plain.write_text("just words", encoding="utf-8")
        code, result = run(*self.args(plain))
        self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, ["unsupported_type"]))

    def test_bad_arguments_exit_2(self) -> None:
        for argv in (["lower-1", str(HTML)], ["PIN-0001"], ["PIN-0001", str(HTML), "--bogus"]):
            with self.subTest(argv=argv):
                self.assertEqual(run(*argv)[0], 2)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        status = {"/forbidden": 403, "/missing": 404, "/limited": 429}.get(self.path, 200)
        body = HTML.read_bytes() if status == 200 else b"no"
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


class FetchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.hw = Path(self._tmp.name) / "hw"

    def test_http_errors_are_named_findings_and_store_nothing(self) -> None:
        for path, code_name in (("/forbidden", "http_403"), ("/missing", "http_404"), ("/limited", "http_429")):
            with self.subTest(path=path):
                code, result = run("PIN-0001", self.base + path, "--hw", str(self.hw))
                self.assertEqual(code, 1)
                self.assertEqual([f["code"] for f in result["findings"]], [code_name])
        self.assertFalse(self.hw.exists())

    def test_unreachable_host_is_a_network_finding(self) -> None:
        code, result = run("PIN-0001", "http://127.0.0.1:1/x.html", "--hw", str(self.hw))
        self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, ["network_error"]))

    def test_a_fetched_page_is_stored_under_its_url_name_with_the_url_as_origin(self) -> None:
        url = self.base + "/standards/din/653"
        code, result = run("PIN-0001", url, "--hw", str(self.hw), "--accessed", "2026-11-27", "--find", "dk")
        self.assertEqual(code, 0)
        self.assertEqual(result["stored"], "parts/PIN-0001/sources/653.html")
        sidecar = self.hw / "parts" / "PIN-0001" / "sources" / "653.html.source.json"
        self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8"))["origin"], url)
        self.assertEqual(by_header(result["matches"][1])["M4"], "16,35")


if __name__ == "__main__":
    unittest.main()
