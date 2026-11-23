# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Which `cad-*` skills a user can type as a `/` command in Claude Code.

Claude Code hides a skill from the `/` menu when its front matter says `user-invocable: false`;
the model can still load it. Only the entry point and two standalone tools stay typable, so a new
skill must be classified here before the suite passes.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"

VISIBLE = {"cad-flow", "cad-viewer", "cad-fits"}
HIDDEN = {
    "cad-analysis", "cad-bom", "cad-concept", "cad-design", "cad-drawings", "cad-model",
    "cad-motion", "cad-recheck", "cad-record", "cad-render", "cad-review-dfm", "cad-review-mech",
}


def front_matter(path: Path) -> dict[str, str]:
    """Top-level `key: value` pairs of the YAML block that opens ``path``."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{path}: no front matter")
    block = lines[1:lines.index("---", 1)]
    return {key.strip(): value.strip() for key, _, value in (line.partition(":") for line in block)
            if key and not key.startswith((" ", "#"))}


def hidden_skills() -> set[str]:
    return {path.parent.name for path in SKILLS.glob("cad-*/SKILL.md")
            if front_matter(path).get("user-invocable", "true").lower() == "false"}


class SkillVisibilityTests(unittest.TestCase):
    def test_every_skill_is_classified_exactly_once(self) -> None:
        names = {path.parent.name for path in SKILLS.glob("cad-*/SKILL.md")}
        self.assertEqual(VISIBLE & HIDDEN, set())
        self.assertEqual(names, VISIBLE | HIDDEN, "classify a new skill in VISIBLE or HIDDEN")

    def test_the_hidden_skills_are_exactly_the_helpers(self) -> None:
        self.assertEqual(hidden_skills(), HIDDEN)

    def test_a_hidden_skill_stays_loadable_by_the_model(self) -> None:
        for name in sorted(HIDDEN):
            with self.subTest(skill=name):
                self.assertNotEqual(
                    front_matter(SKILLS / name / "SKILL.md").get("disable-model-invocation", "false").lower(),
                    "true")


if __name__ == "__main__":
    unittest.main()
