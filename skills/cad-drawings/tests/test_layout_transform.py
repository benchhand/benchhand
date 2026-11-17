#!/usr/bin/env python3
"""layout_check.parse_transform: the SVG transform-list grammar, no FreeCAD needed.

Run: uv run --no-project --with hypothesis python -B scripts/ci_shards.py run rest cad/skills/cad-drawings/tests/test_layout_transform.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import layout_check  # noqa: E402


class EmptyArgumentTests(unittest.TestCase):
    def test_a_function_without_arguments_is_a_value_error_not_an_index_error(self) -> None:
        # SVG 1.1 7.6 / CSS Transforms 1 section 10: translate and scale take at least one number and
        # rotate takes one or three, so "translate()" is a grammar error; the parser's one error type is ValueError.
        for text in ("translate()", "translate( )", "scale()", "rotate()", "translate(1) scale()"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                layout_check.parse_transform(text)


if __name__ == "__main__":
    unittest.main()
