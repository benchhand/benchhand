"""Tests for the complexity ratchet, with ruff output injected."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import complexity_ratchet as cr  # noqa: E402

ROOT = Path("/repo")


def finding(code: str, path: str, func: str) -> dict[str, object]:
    return {
        "code": code,
        "filename": f"/repo/{path}",
        "message": f"`{func}` is too complex (11 > 10)",
    }


BASE = [
    finding("C901", "a.py", "f"),
    finding("PLR0912", "a.py", "f"),
    finding("C901", "b.py", "g"),
]
EXTRA = finding("C901", "c.py", "h")


class RatchetTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.baseline = Path(tmp.name) / "baseline.json"
        self.baseline.write_text(json.dumps(cr.make_baseline(cr.collect(BASE, ROOT))))

    def run_main(
        self, findings: list[dict[str, object]], *args: str
    ) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cr.main(
                ["--baseline", str(self.baseline), *args],
                run_ruff=lambda: findings,
                root=ROOT,
            )
        return code, out.getvalue()

    def test_rise_fails_and_names_new_offender(self) -> None:
        code, out = self.run_main([*BASE, EXTRA])
        self.assertEqual(code, 1)
        self.assertIn("C901 c.py::h", out)
        self.assertNotIn("a.py::f", out)

    def test_equal_passes_without_update_hint(self) -> None:
        code, out = self.run_main(BASE)
        self.assertEqual(code, 0)
        self.assertNotIn("--update", out)

    def test_moved_function_with_equal_counts_passes(self) -> None:
        code, _ = self.run_main([finding("C901", "z.py", "f"), *BASE[1:]])
        self.assertEqual(code, 0)

    def test_drop_passes_and_suggests_update(self) -> None:
        code, out = self.run_main(BASE[:2])
        self.assertEqual(code, 0)
        self.assertIn("--update", out)

    def test_update_lowers_baseline(self) -> None:
        code, _ = self.run_main(BASE[:2], "--update")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.baseline.read_text())["counts"]["C901"], 1)

    def test_update_refuses_to_raise(self) -> None:
        before = self.baseline.read_text()
        code, out = self.run_main([*BASE, EXTRA], "--update")
        self.assertEqual(code, 1)
        self.assertEqual(self.baseline.read_text(), before)
        self.assertIn("--allow-increase", out)

    def test_allow_increase_rewrites_upward(self) -> None:
        code, _ = self.run_main([*BASE, EXTRA], "--update", "--allow-increase")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.baseline.read_text())["counts"]["C901"], 3)


if __name__ == "__main__":
    unittest.main()
