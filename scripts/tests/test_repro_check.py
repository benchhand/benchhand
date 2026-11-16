"""Comparison and allowlist logic of scripts/repro_check.py, on synthetic hw trees."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import repro_check as rc  # noqa: E402


class CompareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a, self.b = Path(self.tmp.name) / "a", Path(self.tmp.name) / "b"

    def put(self, rel: str, a: bytes, b: bytes | None = None) -> None:
        for root, data in ((self.a, a), (self.b, a if b is None else b)):
            target = root / "versions" / "v1" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def compare(self, allow: tuple[rc.Volatile, ...] = ()) -> rc.Report:
        return rc.compare(self.a, self.b, allow)

    def test_identical_trees_pass(self) -> None:
        self.put("parts/P/p.step", b"ISO\nDATA;\n")
        self.put("manifest.json", b'{"a": 1}')
        report = self.compare()
        self.assertTrue(report.ok)
        self.assertEqual(report.compared, 2)

    def test_changed_byte_is_reported_with_offset(self) -> None:
        self.put("p.stl", b"abcdef", b"abcXef")
        report = self.compare()
        self.assertFalse(report.ok)
        self.assertEqual([(m.path, m.detail) for m in report.mismatches], [("versions/v1/p.stl", "first differing byte at offset 3")])

    def test_file_present_on_one_side_only_is_a_mismatch(self) -> None:
        self.put("keep.json", b"{}")
        (self.b / "versions" / "v1" / "keep.json").unlink()
        report = self.compare()
        self.assertEqual([m.detail for m in report.mismatches], ["missing in B"])

    def test_json_key_allowlist_masks_only_that_key_and_is_reported(self) -> None:
        allow = (rc.Volatile("versions/*/manifest.json", "json-key", r"^\.built_at$", "wall-clock stamp"),)
        self.put("manifest.json", json.dumps({"built_at": "t1", "x": 1}).encode(), json.dumps({"built_at": "t2", "x": 1}).encode())
        report = self.compare(allow)
        self.assertTrue(report.ok)
        self.assertEqual([(u.path, u.rule.target) for u in report.allowlisted], [("versions/v1/manifest.json", r"^\.built_at$")])

    def test_json_key_allowlist_does_not_hide_other_keys(self) -> None:
        allow = (rc.Volatile("versions/*/manifest.json", "json-key", r"^\.built_at$", "wall-clock stamp"),)
        self.put("manifest.json", json.dumps({"built_at": "t1", "x": 1}).encode(), json.dumps({"built_at": "t1", "x": 2}).encode())
        self.assertFalse(self.compare(allow).ok)

    def test_json_key_masks_nested_occurrences(self) -> None:
        allow = (rc.Volatile("**/*.json", "json-key", r"\.wall_s$", "timing"),)
        self.put("m.json", b'{"steps": [{"wall_s": 1.5, "n": 1}]}', b'{"steps": [{"wall_s": 2.5, "n": 1}]}')
        self.assertTrue(self.compare(allow).ok)

    def test_line_regex_allowlist_masks_matching_lines(self) -> None:
        allow = (rc.Volatile("**/*.step", "line-regex", r"^FILE_NAME\(.*\);$", "header timestamp"),)
        self.put("p.step", b"A;\nFILE_NAME('x','2026-01-01');\nB;\n", b"A;\nFILE_NAME('x','2026-01-02');\nB;\n")
        self.assertTrue(self.compare(allow).ok)

    def test_json_key_pattern_can_be_scoped_to_the_parent_key(self) -> None:
        allow = (rc.Volatile("**/*.json", "json-key", r"^installed\.sha256$", "derived hash"),)
        self.put("m.json", b'{"installed": {"sha256": "a"}, "sha256": "x"}', b'{"installed": {"sha256": "b"}, "sha256": "x"}')
        self.assertTrue(self.compare(allow).ok)
        self.put("n.json", b'{"installed": {"p": 1}, "sha256": "x"}', b'{"installed": {"p": 1}, "sha256": "y"}')
        self.assertEqual([m.path for m in self.compare(allow).mismatches], ["versions/v1/n.json"])

    def test_allowlist_that_matches_nothing_is_not_reported(self) -> None:
        allow = (rc.Volatile("**/*.step", "line-regex", r"^FILE_NAME", "header timestamp"),)
        self.put("p.step", b"A;\n")
        self.assertEqual(self.compare(allow).allowlisted, [])

    def test_unparseable_json_falls_back_to_a_byte_mismatch(self) -> None:
        allow = (rc.Volatile("**/*.json", "json-key", r"\.k$", "r"),)
        self.put("bad.json", b"{oops", b"{oops!")
        self.assertFalse(self.compare(allow).ok)

    def test_every_allowlist_entry_documents_its_reason(self) -> None:
        for rule in rc.VOLATILE:
            self.assertTrue(rule.reason.strip(), rule)


if __name__ == "__main__":
    unittest.main()
