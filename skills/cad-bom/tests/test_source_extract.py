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
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

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
        self.assertEqual(result["matches"], [{"page": 2, "line": "M4 x 8.1 6.3 5.7", "previous_line": "M3 x 5.7 4.6 4.0",
                                              "column_order": "unverified"}])

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

    def test_a_symlinked_sources_folder_is_refused_and_nothing_is_written_through_it(self) -> None:
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        part = self.hw / "parts" / "PIN-0001"
        part.mkdir(parents=True)
        (part / "sources").symlink_to(outside, target_is_directory=True)
        code, result = run(*self.args(HTML))
        self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, ["symlink_refused"]))
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_symlinked_target_file_is_refused_and_its_destination_keeps_its_bytes(self) -> None:
        sources = self.hw / "parts" / "PIN-0001" / "sources"
        sources.mkdir(parents=True)
        victim = Path(self._tmp.name) / "victim.txt"
        victim.write_text("keep me", encoding="utf-8")
        (sources / "thumb_screw_table.html").symlink_to(victim)
        code, result = run(*self.args(HTML))
        self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, ["symlink_refused"]))
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep me")

    def test_a_symlinked_parts_folder_is_refused(self) -> None:
        outside = Path(self._tmp.name) / "elsewhere"
        outside.mkdir()
        self.hw.mkdir()
        (self.hw / "parts").symlink_to(outside, target_is_directory=True)
        code, result = run(*self.args(HTML))
        self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, ["symlink_refused"]))
        self.assertEqual(list(outside.iterdir()), [])

    def test_an_existing_sidecar_is_never_replaced_and_no_target_is_left_behind(self) -> None:
        sources = self.hw / "parts" / "PIN-0001" / "sources"
        sources.mkdir(parents=True)
        sidecar = sources / "thumb_screw_table.html.source.json"
        sidecar.write_text('{"origin": "earlier"}\n', encoding="utf-8")
        code, result = run(*self.args(HTML))
        self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, ["sidecar_invalid"]))
        self.assertEqual(sidecar.read_text(encoding="utf-8"), '{"origin": "earlier"}\n')
        self.assertFalse((sources / "thumb_screw_table.html").exists())

    def test_a_conflicting_fetch_leaves_both_originals_untouched(self) -> None:
        self.assertEqual(run(*self.args(HTML))[0], 0)
        sources = self.hw / "parts" / "PIN-0001" / "sources"
        before = {p.name: p.read_bytes() for p in sources.iterdir()}
        other = Path(self._tmp.name) / "thumb_screw_table.html"
        other.write_text("<table><tr><td>other</td></tr></table>", encoding="utf-8")
        code, result = run(*self.args(other))
        self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, ["name_conflict"]))
        self.assertEqual({p.name: p.read_bytes() for p in sources.iterdir()}, before)

    def test_concurrent_runs_with_different_content_leave_one_consistent_winner(self) -> None:
        pages = []
        for index in range(6):
            page = Path(self._tmp.name) / f"page{index}.html"
            page.write_text(f"<table><tr><td>version {index}</td></tr></table>", encoding="utf-8")
            pages.append(page)
        script = HERE.parent / "scripts" / "source_extract.py"
        procs = [subprocess.Popen([sys.executable, "-B", str(script), "PIN-0001", str(page), "--hw", str(self.hw),
                                   "--name", "shared.html", "--accessed", "2026-11-27"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for page in pages]
        outputs = [proc.communicate() for proc in procs]
        codes = [proc.returncode for proc in procs]
        findings = [[f["code"] for f in json.loads(out)["findings"]] for out, _ in outputs]
        sources = self.hw / "parts" / "PIN-0001" / "sources"
        self.assertEqual(sorted(codes), [0, 1, 1, 1, 1, 1])
        self.assertEqual(sorted(findings), [[], ["name_conflict"], ["name_conflict"], ["name_conflict"],
                                            ["name_conflict"], ["name_conflict"]])
        sidecar = json.loads((sources / "shared.html.source.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["sha256"], hashlib.sha256((sources / "shared.html").read_bytes()).hexdigest())
        self.assertEqual(sorted(p.name for p in sources.iterdir()), ["shared.html", "shared.html.source.json"])

    def test_a_failed_store_never_unlinks_an_entry_another_writer_put_in_place(self) -> None:
        sources = self.hw / "parts" / "PIN-0001" / "sources"
        target = sources / "thumb_screw_table.html"
        real = extract._link_new

        def other_writer_takes_the_names(directory_fd: int, name: str, payload: bytes) -> object:
            if name.endswith(".source.json"):
                target.unlink()
                target.write_text("another writer's page", encoding="utf-8")
                (sources / name).write_text('{"sha256": "someone else"}', encoding="utf-8")
            return real(directory_fd, name, payload)

        with mock.patch.object(extract, "_link_new", other_writer_takes_the_names):
            code, result = run(*self.args(HTML))
        self.assertEqual(code, 1)
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "another writer's page")

    def test_a_part_folder_that_is_a_file_is_a_storage_finding(self) -> None:
        self.hw.mkdir()
        (self.hw / "parts").write_text("not a directory", encoding="utf-8")
        code, result = run(*self.args(HTML))
        self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, ["storage_error"]))

    def test_cells_and_rows_without_end_tags_still_expand_their_spans(self) -> None:
        page = Path(self._tmp.name) / "no_end_tags.html"
        page.write_text(
            "<table>\n"
            "<tr><td colspan=3>Thread d<td>M3<td>(M3,5)<td>M4\n"
            "<tr><td colspan=3>P<td>0,5<td>0,6<td>0,7\n"
            "<tr><td colspan=2 rowspan=3>dk<td>Nominal<td>12<td>14<td>16\n"
            "<tr><td>max.<td>12,35<td>14,35<td>16,35\n"
            "<tr><td>min.<td>11,65<td>13,65<td>15,65\n"
            "</table>\n", encoding="utf-8")
        code, result = run(*self.args(page, "--find", "dk"))
        self.assertEqual(code, 0)
        nominal, maximum, minimum = (by_header(m) for m in result["matches"])
        self.assertEqual(nominal, {"M3": "12", "(M3,5)": "14", "M4": "16"})
        self.assertEqual(maximum["M4"], "16,35")
        self.assertEqual(minimum["M4"], "15,65")

    def test_a_cell_closed_by_the_next_row_in_a_thead_without_end_tags_still_makes_a_header(self) -> None:
        page = Path(self._tmp.name) / "thead.html"
        page.write_text("<table><thead><tr><th>Size<th>dk<tbody><tr><td>M4<td>16</table>", encoding="utf-8")
        _, result = run(*self.args(page, "--find", "M4"))
        self.assertEqual([(c["header"], c["value"]) for c in result["matches"][0]["cells"]], [("Size", "M4"), ("dk", "16")])

    def test_a_pdf_result_says_in_a_field_that_column_order_is_not_extracted(self) -> None:
        _, result = run(*self.args(PDF, "--find", "M4 x"))
        self.assertEqual(result["column_order"], "unverified")
        self.assertEqual([m["column_order"] for m in result["matches"]], ["unverified"])
        _, table = run(*self.args(HTML, "--find", "dk"))
        self.assertNotIn("column_order", table)

    def test_a_name_that_is_not_one_plain_file_name_is_unsafe_and_writes_nothing(self) -> None:
        elsewhere = Path(self._tmp.name) / "elsewhere"
        elsewhere.mkdir()
        names = ["nested/page.html", "../page.html", "../../outside.html", "..", ".", "/abs/page.html",
                 " padded.html", "line\nbreak.html", "dir\\page.html", ".hidden.html"]
        for name in names:
            with self.subTest(name=name):
                code, result = run(*self.args(HTML, "--name", name))
                self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, ["unsafe_name"]))
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertEqual(sorted(p.name for p in Path(self._tmp.name).iterdir()), ["elsewhere"])

    def test_a_symlinked_project_root_is_refused(self) -> None:
        real = Path(self._tmp.name) / "real_hw"
        real.mkdir()
        self.hw.symlink_to(real, target_is_directory=True)
        code, result = run(*self.args(HTML))
        self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, ["symlink_refused"]))
        self.assertEqual(list(real.iterdir()), [])

    def test_a_stored_sidecar_that_does_not_fully_describe_the_content_is_invalid_not_trusted(self) -> None:
        digest = hashlib.sha256(HTML.read_bytes()).hexdigest()
        good = {"origin": "https://example.test/din653", "sha256": digest, "accessed": "2026-11-20",
                "bytes": HTML.stat().st_size, "kind": "html"}
        cases = {
            "empty object": ({}, "sidecar_invalid"),
            "only the hash": ({"sha256": digest}, "sidecar_invalid"),
            "bad date": ({**good, "accessed": "yesterday"}, "sidecar_invalid"),
            "wrong size": ({**good, "bytes": 1}, "sidecar_invalid"),
            "wrong kind": ({**good, "kind": "pdf"}, "sidecar_invalid"),
            "origin not text": ({**good, "origin": 7}, "sidecar_invalid"),
            "other content": ({**good, "sha256": "0" * 64}, "name_conflict"),
        }
        for label, (record, expected) in cases.items():
            with self.subTest(label):
                hw = Path(self._tmp.name) / label.replace(" ", "_") / "hw"
                sources = hw / "parts" / "PIN-0001" / "sources"
                sources.mkdir(parents=True)
                (sources / "thumb_screw_table.html").write_bytes(HTML.read_bytes())
                sidecar = sources / "thumb_screw_table.html.source.json"
                sidecar.write_text(json.dumps(record), encoding="utf-8")
                code, result = run("PIN-0001", str(HTML), "--hw", str(hw), "--accessed", "2026-11-27")
                self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, [expected]))
                self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8")), record)
        broken = Path(self._tmp.name) / "broken" / "hw"
        (broken / "parts" / "PIN-0001" / "sources").mkdir(parents=True)
        (broken / "parts" / "PIN-0001" / "sources" / "thumb_screw_table.html").write_bytes(HTML.read_bytes())
        (broken / "parts" / "PIN-0001" / "sources" / "thumb_screw_table.html.source.json").write_text("not json", encoding="utf-8")
        code, result = run("PIN-0001", str(HTML), "--hw", str(broken))
        self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, ["sidecar_invalid"]))

    def test_a_reused_store_reports_the_provenance_that_is_stored(self) -> None:
        first = Path(self._tmp.name) / "first" / "thumb_screw_table.html"
        first.parent.mkdir()
        first.write_bytes(HTML.read_bytes())
        self.assertEqual(run("PIN-0001", str(first), "--hw", str(self.hw), "--accessed", "2026-11-20")[0], 0)
        code, result = run(*self.args(HTML, "--accessed", "2026-12-01"))
        self.assertEqual(code, 0)
        self.assertEqual((result["origin"], result["accessed"], result["reused"]), (str(first), "2026-11-20", True))
        stored = json.loads((self.hw / "parts" / "PIN-0001" / "sources" / "thumb_screw_table.html.source.json").read_text(encoding="utf-8"))
        self.assertEqual((stored["origin"], stored["accessed"]), (str(first), "2026-11-20"))

    def test_a_fresh_store_is_not_marked_reused(self) -> None:
        _, result = run(*self.args(HTML))
        self.assertIs(result["reused"], False)

    def test_an_html_page_without_tables_is_refused_before_it_is_stored(self) -> None:
        page = Path(self._tmp.name) / "challenge.html"
        page.write_text("<html><body><h1>Just a moment...</h1><p>Checking your browser.</p></body></html>", encoding="utf-8")
        for extra in ((), ("--find", "dk")):
            with self.subTest(extra=extra):
                code, result = run(*self.args(page, *extra))
                self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, ["no_tables"]))
        self.assertFalse(self.hw.exists())

    def test_a_sidecar_with_a_repeated_key_or_unparseable_json_is_invalid(self) -> None:
        digest = hashlib.sha256(HTML.read_bytes()).hexdigest()
        size = HTML.stat().st_size
        whole = (f'"sha256": "{digest}", "accessed": "2026-11-20", "bytes": {size}, "kind": "html"')
        cases = {
            "repeated key": ('{"origin": "a", "origin": "b", ' + whole + "}").encode(),
            "deeply nested": b"[" * 200000,
            "not utf-8": b"\xff\xfe{",
        }
        for label, payload in cases.items():
            with self.subTest(label):
                hw = Path(self._tmp.name) / label.replace(" ", "_") / "hw"
                sources = hw / "parts" / "PIN-0001" / "sources"
                sources.mkdir(parents=True)
                (sources / "thumb_screw_table.html").write_bytes(HTML.read_bytes())
                sidecar = sources / "thumb_screw_table.html.source.json"
                sidecar.write_bytes(payload)
                code, result = run("PIN-0001", str(HTML), "--hw", str(hw))
                self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, ["sidecar_invalid"]))
                self.assertEqual(sidecar.read_bytes(), payload)

    def test_bad_arguments_exit_2(self) -> None:
        for argv in (["lower-1", str(HTML)], ["PIN-0001"], ["PIN-0001", str(HTML), "--bogus"]):
            with self.subTest(argv=argv):
                self.assertEqual(run(*argv)[0], 2)


class ChainingTests(unittest.TestCase):
    """A finding keeps the original error as its cause while the command line prints only the short detail."""

    def test_findings_chain_the_error_that_caused_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(extract.Finding) as missing:
                extract.read_source(str(Path(tmp) / "absent.pdf"))
            self.assertIsInstance(missing.exception.__cause__, FileNotFoundError)
        with self.assertRaises(extract.Finding) as broken:
            extract.pdf_pages(b"%PDF-1.4 truncated")
        self.assertIsNotNone(broken.exception.__cause__)
        self.assertEqual(broken.exception.code, "unreadable_file")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/short":
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"<table>")
            self.wfile.flush()
            return
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

    def test_an_http_error_finding_chains_the_http_error(self) -> None:
        with self.assertRaises(extract.Finding) as caught:
            extract.fetch(self.base + "/missing")
        self.assertEqual(caught.exception.code, "http_404")
        self.assertIsInstance(caught.exception.__cause__, urllib.error.HTTPError)

    def test_a_truncated_response_is_a_transfer_finding_and_stores_nothing(self) -> None:
        code, result = run("PIN-0001", self.base + "/short", "--hw", str(self.hw))
        self.assertEqual((code, [f["code"] for f in result["findings"]]), (1, ["transfer_error"]))
        self.assertFalse(self.hw.exists())

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
