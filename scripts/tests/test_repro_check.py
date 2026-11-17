"""Comparison logic of scripts/repro_check.py, on synthetic hw trees."""

from __future__ import annotations

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

    def test_identical_trees_pass(self) -> None:
        self.put("parts/P/p.step", b"ISO\nDATA;\n")
        self.put("manifest.json", b'{"a": 1}')
        report = rc.compare(self.a, self.b)
        self.assertTrue(report.ok)
        self.assertEqual(report.compared, 2)

    def test_changed_byte_is_reported_with_offset(self) -> None:
        self.put("p.stl", b"abcdef", b"abcXef")
        report = rc.compare(self.a, self.b)
        self.assertEqual([(m.path, m.detail) for m in report.mismatches], [("versions/v1/p.stl", "first differing byte at offset 3")])

    def test_size_difference_is_reported(self) -> None:
        self.put("p.glb", b"abc", b"abcd")
        self.assertEqual([m.detail for m in rc.compare(self.a, self.b).mismatches], ["first differing byte at offset 3; sizes 3 vs 4"])

    def test_file_present_on_one_side_only_is_a_mismatch(self) -> None:
        self.put("keep.json", b"{}")
        (self.b / "versions" / "v1" / "keep.json").unlink()
        self.assertEqual([m.detail for m in rc.compare(self.a, self.b).mismatches], ["missing in B"])

    def test_json_difference_names_the_differing_paths(self) -> None:
        self.put("m.json", b'{"x": 1, "y": {"z": [1, 2]}}', b'{"x": 1, "y": {"z": [1, 3]}}')
        self.assertEqual([m.detail for m in rc.compare(self.a, self.b).mismatches], ["JSON differs at $.y.z[1]"])

    def test_step_header_difference_is_reported(self) -> None:
        self.put("p.step", b"A;\nFILE_NAME('x','2026-01-01');\n", b"A;\nFILE_NAME('x','2026-01-02');\n")
        self.assertEqual([m.path for m in rc.compare(self.a, self.b).mismatches], ["versions/v1/p.step"])


class NoNormalisationTests(unittest.TestCase):
    """Guards against JSON or manifest normalisation coming back: each pair differs only in what a normaliser hides."""

    def run_pair(self, rel: str, a: bytes, b: bytes) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            for name, data in (("a", a), ("b", b)):
                target = Path(tmp) / name / "versions" / "v1" / rel
                target.parent.mkdir(parents=True)
                target.write_bytes(data)
            return rc.main(["--compare", str(Path(tmp) / "a"), str(Path(tmp) / "b")])

    def test_json_equal_in_value_but_not_in_formatting_is_a_mismatch(self) -> None:
        self.assertEqual(self.run_pair("m.json", b'{"a": 1, "b": 2}', b'{"a":1,"b":2}'), 1)

    def test_json_equal_in_value_but_not_in_key_order_is_a_mismatch(self) -> None:
        self.assertEqual(self.run_pair("m.json", b'{"a": 1, "b": 2}', b'{"b": 2, "a": 1}'), 1)

    def test_manifests_differing_only_in_created_utc_are_a_mismatch(self) -> None:
        self.assertEqual(self.run_pair("manifest.json", b'{"created_utc": "t1", "n": 1}', b'{"created_utc": "t2", "n": 1}'), 1)

    def test_manifests_differing_only_in_import_s_are_a_mismatch(self) -> None:
        self.assertEqual(self.run_pair("manifest.json", b'{"tools": {"cadquery": {"import_s": 1.0}}}', b'{"tools": {"cadquery": {"import_s": 2.0}}}'), 1)


class SymlinkTests(unittest.TestCase):
    """Symlinks are never followed; any link that is not identical on both sides is a difference."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.a, self.b = root / "a" / "versions" / "v1", root / "b" / "versions" / "v1"
        for side in (self.a, self.b):
            side.mkdir(parents=True)
            (side / "f").write_bytes(b"same")
        (root / "real").mkdir()
        (root / "real" / "inner.step").write_bytes(b"data")

    def main(self) -> int:
        root = Path(self.tmp.name)
        return rc.main(["--compare", str(root / "a"), str(root / "b")])

    def test_linked_directory_on_one_side_is_a_mismatch(self) -> None:
        (self.a / "parts").symlink_to(Path(self.tmp.name) / "real", target_is_directory=True)
        self.assertEqual(self.main(), 1)

    def test_dangling_link_on_one_side_is_a_mismatch(self) -> None:
        (self.a / "gone").symlink_to(Path(self.tmp.name) / "nowhere")
        self.assertEqual(self.main(), 1)

    def test_links_with_different_targets_are_a_mismatch(self) -> None:
        (self.a / "l").symlink_to("f")
        (self.b / "l").symlink_to("other")
        self.assertEqual(self.main(), 1)

    def test_link_against_a_regular_file_is_a_mismatch(self) -> None:
        (self.a / "l").symlink_to("f")
        (self.b / "l").write_bytes(b"same")
        self.assertEqual(self.main(), 1)

    def test_identical_links_on_both_sides_pass(self) -> None:
        for side in (self.a, self.b):
            (side / "l").symlink_to("f")
            (side / "d").symlink_to(Path(self.tmp.name) / "real", target_is_directory=True)
        self.assertEqual(self.main(), 0)

    def test_report_names_the_link_difference(self) -> None:
        (self.a / "l").symlink_to("f")
        (self.b / "l").symlink_to("other")
        report = rc.compare(self.a.parents[1], self.b.parents[1])
        self.assertEqual([(m.path, m.detail) for m in report.mismatches], [("versions/v1/l", "symlink targets differ: 'f' vs 'other'")])


class MainTests(unittest.TestCase):
    def test_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, data in (("a", b"1"), ("b", b"1"), ("c", b"2")):
                (root / name / "versions" / "v1").mkdir(parents=True)
                (root / name / "versions" / "v1" / "f").write_bytes(data)
            (root / "empty").mkdir()
            self.assertEqual(rc.main(["--compare", str(root / "a"), str(root / "b")]), 0)
            self.assertEqual(rc.main(["--compare", str(root / "a"), str(root / "c")]), 1)
            self.assertEqual(rc.main(["--compare", str(root / "a"), str(root / "empty")]), 2)


if __name__ == "__main__":
    unittest.main()
