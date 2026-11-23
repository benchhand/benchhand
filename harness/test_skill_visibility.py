# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Which `cad-*` skills a user can type as a `/` command in Claude Code.

Claude Code hides a skill from the `/` menu when its front matter says `user-invocable: false`;
the model can still load it. The shared `skills/*/SKILL.md` files carry the key, so the Claude
plugin route, which reads `skills/` directly, hides the helpers too. The key is not an Agent Skills
spec key, so strict validators reject the shared files: an accepted exception. `install.py` strips
the key for every harness but Claude. Only the entry point and two standalone tools stay typable,
so a new skill must be classified here before the suite passes.
"""

from __future__ import annotations

import json
import shutil
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
# Agent Skills spec keys, plus the one Claude Code extension the shared files carry on purpose.
SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
ALLOWED_KEYS = SPEC_KEYS | {KEY}

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

    def test_shared_sources_carry_the_key_on_exactly_the_hidden_skills(self) -> None:
        self.assertEqual(installed_with_key(SKILLS), HIDDEN)
        for name in sorted(HIDDEN):
            with self.subTest(skill=name):
                fields = front_matter(SKILLS / name / "SKILL.md")
                self.assertIs(fields[KEY], False)
                self.assertNotIn("disable-model-invocation", fields)

    def test_shared_front_matter_uses_only_spec_keys_and_the_claude_exception(self) -> None:
        for path in sorted(SKILLS.glob("cad-*/SKILL.md")):
            with self.subTest(skill=path.parent.name):
                self.assertLessEqual(set(front_matter(path)), ALLOWED_KEYS)


class InstalledSkillTests(unittest.TestCase):
    def install(self, harness: str) -> Path:
        temporary = tempfile.TemporaryDirectory(prefix="cad-visibility-")
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name)
        result = subprocess.run([sys.executable, str(INSTALLER), "--harness", harness, "--project", str(project)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return next(project.glob("*/skills"))

    def test_claude_install_keeps_the_key_on_exactly_the_hidden_skills(self) -> None:
        skills = self.install("claude")
        self.assertEqual(installed_with_key(skills), HIDDEN)
        for name in sorted(HIDDEN):
            with self.subTest(skill=name):
                self.assertEqual((skills / name / "SKILL.md").read_bytes(), (SKILLS / name / "SKILL.md").read_bytes())

    def test_other_harness_installs_carry_no_key_and_lose_nothing_else(self) -> None:
        for harness in ("codex", "opencode", "kiro"):
            skills = self.install(harness)
            with self.subTest(harness=harness):
                self.assertEqual(installed_with_key(skills), set())
                for name in sorted(HIDDEN):
                    source = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
                    installed = (skills / name / "SKILL.md").read_text(encoding="utf-8")
                    self.assertEqual(installed, source.replace(f"{KEY}: false\n", "", 1))
                    self.assertEqual(set(front_matter(skills / name / "SKILL.md")), {"name", "description"})


@unittest.skipUnless(shutil.which("claude"), "needs the claude CLI")
class PluginRouteTests(unittest.TestCase):
    """The plugin route reads `skills/` as it is, so the key in the shared files must hide the helpers."""

    def run_claude(self, project: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["claude", "-p", *args, "--plugin-dir", str(ROOT), "--max-turns", "3"],
                              cwd=project, capture_output=True, text=True, timeout=300)

    def test_plugin_lists_only_the_visible_skills_and_still_loads_a_hidden_one(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-plugin-")
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name)
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        init = self.run_claude(project, "hi", "--output-format", "stream-json", "--verbose")
        self.assertEqual(init.returncode, 0, init.stderr)
        events = [json.loads(line) for line in init.stdout.splitlines() if line.startswith("{")]
        commands = next(event["slash_commands"] for event in events if event.get("subtype") == "init")
        self.assertEqual({c for c in commands if c.startswith("benchhand:")}, {f"benchhand:{n}" for n in VISIBLE})
        loaded = self.run_claude(project, "Call the Skill tool with skill 'benchhand:cad-bom' and report only its first heading.")
        self.assertIn("CAD EBOM Roll-up", loaded.stdout)


if __name__ == "__main__":
    unittest.main()
