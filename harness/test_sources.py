"""Shipped instruction text names no path on the machine it was written on.

Skills and agent prompts are installed into other people's harnesses and loaded into the model's
context. An absolute home, temp or worktree path there is a paste from an author's machine: it
means nothing to the reader, and once it carried a whole second skill into cad-design.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("skills/**/*.md", "agents/*.md")
LOCAL_PATH = re.compile(
    r"(?<![\w.$-])(?:/Users/[^/\s`\"')]+|/home/[^/\s`\"')]+|/private/var/folders/|/var/folders/"
    r"|/tmp/|[A-Za-z]:\\Users\\)"
    r"|/worktrees?/"
)


def local_paths(text: str) -> list[tuple[int, str]]:
    """Every (line number, matched text) of a machine-local path in ``text``."""
    return [(number, match[0]) for number, line in enumerate(text.splitlines(), 1)
            for match in LOCAL_PATH.finditer(line)]


class LocalPathTests(unittest.TestCase):
    def test_the_pattern_catches_home_temp_and_worktree_paths_and_spares_relative_ones(self) -> None:
        caught = [
            "[ /Users/sjors/tmp/worktrees/cad-agent/feat/skills/cad-fits/SKILL.md#7594]",
            "see /home/ci/cad-bench/out.json",
            "written to /private/var/folders/8d/T/opencode/x.step",
            "a copy in /tmp/build",
            r"C:\Users\me\cad\hw",
            "checkout at ../worktrees/feat-x/skills",
        ]
        spared = [
            "`hw/parts/<PN>/model.py`",
            "Resolve `$CAD_SKILLS/cad-flow/scripts/run.py`",
            "`~/.config/opencode/skills`",
            "https://example.com/Users/guide",
            "a work tree of the record",
            "the $HOME/.cache/uv folder",
        ]
        for text in caught:
            with self.subTest(text=text):
                self.assertTrue(local_paths(text), text)
        for text in spared:
            with self.subTest(text=text):
                self.assertEqual(local_paths(text), [], text)

    def test_no_skill_or_agent_source_names_a_machine_local_path(self) -> None:
        found = [f"{path.relative_to(ROOT)}:{number}: {match}"
                 for pattern in SOURCES for path in sorted(ROOT.glob(pattern))
                 for number, match in local_paths(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "\n".join(found))


if __name__ == "__main__":
    unittest.main()
