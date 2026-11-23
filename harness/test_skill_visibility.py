# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Which `cad-*` skills a user can type as a `/` command in Claude Code.

Claude Code hides a skill from the `/` menu when its front matter says `user-invocable: false`;
the model can still load it. That key is not an Agent Skills spec key, and strict validators
reject it, so the shared `skills/*/SKILL.md` files never carry it: `install.py --harness claude`
adds it to the hidden skills. Only the entry point and two standalone tools stay typable, so a new
skill must be classified here before the suite passes.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
INSTALLER = ROOT / "harness" / "install.py"
KEY = "user-invocable"
SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}

# The policy, written out apart from the code under test.
VISIBLE = {"cad-flow", "cad-viewer", "cad-fits"}
HIDDEN = {
    "cad-analysis", "cad-bom", "cad-concept", "cad-design", "cad-drawings", "cad-model",
    "cad-motion", "cad-recheck", "cad-record", "cad-render", "cad-review-dfm", "cad-review-mech",
}


def front_matter(path: Path) -> dict[str, Any]:
    return generate.skill_front_matter(path.read_text(encoding="utf-8"))


def installed_with_key(skills_dir: Path) -> set[str]:
    return {path.parent.name for path in skills_dir.glob("cad-*/SKILL.md") if KEY in front_matter(path)}


class SourceSkillTests(unittest.TestCase):
    def test_every_skill_is_classified_exactly_once(self) -> None:
        names = {path.parent.name for path in SKILLS.glob("cad-*/SKILL.md")}
        self.assertEqual(VISIBLE & HIDDEN, set())
        self.assertEqual(names, VISIBLE | HIDDEN, "classify a new skill in VISIBLE or HIDDEN")

    def test_the_generator_hides_exactly_the_policy_set(self) -> None:
        self.assertEqual(set(generate.HIDDEN_SKILLS), HIDDEN)

    def test_shared_front_matter_uses_only_agent_skills_spec_keys(self) -> None:
        for path in sorted(SKILLS.glob("cad-*/SKILL.md")):
            with self.subTest(skill=path.parent.name):
                self.assertLessEqual(set(front_matter(path)), SPEC_KEYS)


class InstalledSkillTests(unittest.TestCase):
    def install(self, harness: str) -> Path:
        temporary = tempfile.TemporaryDirectory(prefix="cad-visibility-")
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name)
        result = subprocess.run([sys.executable, str(INSTALLER), "--harness", harness, "--project", str(project)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return next(project.glob("*/skills"))

    def test_claude_install_hides_exactly_the_helper_skills(self) -> None:
        skills = self.install("claude")
        self.assertEqual(installed_with_key(skills), HIDDEN)
        for name in sorted(HIDDEN):
            with self.subTest(skill=name):
                fields = front_matter(skills / name / "SKILL.md")
                self.assertIs(fields[KEY], False)
                self.assertNotIn("disable-model-invocation", fields)

    def test_claude_install_keeps_the_rest_of_each_skill_byte_for_byte(self) -> None:
        skills = self.install("claude")
        for name in sorted(HIDDEN):
            with self.subTest(skill=name):
                source = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
                installed = (skills / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertEqual(installed.replace(f"{KEY}: false\n", "", 1), source)

    def test_other_harness_installs_carry_no_key(self) -> None:
        for harness in ("codex", "opencode", "kiro"):
            with self.subTest(harness=harness):
                self.assertEqual(installed_with_key(self.install(harness)), set())


if __name__ == "__main__":
    unittest.main()
