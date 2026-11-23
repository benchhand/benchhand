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

import importlib.util
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
        line = f"{KEY}: false\n".encode()
        for harness in ("codex", "opencode", "kiro"):
            skills = self.install(harness)
            with self.subTest(harness=harness):
                self.assertEqual(installed_with_key(skills), set())
                for name in sorted(HIDDEN):
                    source = (SKILLS / name / "SKILL.md").read_bytes()
                    installed = (skills / name / "SKILL.md").read_bytes()
                    self.assertEqual(installed, source.replace(line, b"", 1))
                    self.assertEqual(set(front_matter(skills / name / "SKILL.md")), {"name", "description"})


def load_install() -> Any:
    spec = importlib.util.spec_from_file_location("benchhand_install", INSTALLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StripUserInvocableTests(unittest.TestCase):
    def test_removes_only_the_key_line_and_keeps_crlf(self) -> None:
        install = load_install()
        source = b"---\r\nname: cad-x\r\nuser-invocable: false\r\ndescription: d\r\n---\r\nbody\r\nuser-invocable: false\r\n"
        expected = b"---\r\nname: cad-x\r\ndescription: d\r\n---\r\nbody\r\nuser-invocable: false\r\n"
        self.assertEqual(install.strip_user_invocable(source), expected)

    def test_file_without_the_key_is_unchanged(self) -> None:
        install = load_install()
        source = b"---\nname: cad-x\n---\nbody\n"
        self.assertEqual(install.strip_user_invocable(source), source)

    def test_unexpected_forms_fail_loudly(self) -> None:
        install = load_install()
        for front in ("user-invocable: true\n", "user-invocable: false # hidden\n", "user-invocable:  false\n",
                      "user-invocable: false\nuser-invocable: false\n"):
            with self.subTest(front=front), self.assertRaises(ValueError):
                install.strip_user_invocable(f"---\nname: x\n{front}---\nbody\n".encode())

    def test_install_without_pyyaml_keeps_crlf_bytes(self) -> None:
        install = load_install()
        source = b"---\r\nname: cad-x\r\nuser-invocable: false\r\n---\r\nbody\r\n"
        with tempfile.TemporaryDirectory(prefix="cad-crlf-") as temporary:
            skills = Path(temporary) / "skills"
            (skills / "cad-x").mkdir(parents=True)
            (skills / "cad-x" / "SKILL.md").write_bytes(source)
            install.SKILLS = skills
            for harness in ("codex", "opencode", "kiro"):
                with self.subTest(harness=harness):
                    pair = next(p for p in install.file_pairs((harness,), Path(temporary) / "out") if p.source.name == "SKILL.md")
                    self.assertEqual(pair.content, source.replace(b"user-invocable: false\r\n", b""))
            claude = next(p for p in install.file_pairs(("claude",), Path(temporary) / "out") if p.source.name == "SKILL.md")
            self.assertIsNone(claude.content)


@unittest.skipUnless(shutil.which("claude"), "local smoke test: needs the claude CLI and a logged-in account; CI has neither")
class PluginRouteTests(unittest.TestCase):
    """Local smoke test, not CI coverage: the plugin route reads `skills/` as it is, so the key must hide the helpers."""

    def run_claude(self, project: Path, *args: str) -> list[dict[str, Any]]:
        result = subprocess.run(["claude", "-p", *args, "--output-format", "stream-json", "--verbose",
                                 "--plugin-dir", str(ROOT), "--max-turns", "3"],
                                cwd=project, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]

    def test_plugin_lists_only_the_visible_skills_and_still_loads_a_hidden_one(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-plugin-")
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name)
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        events = self.run_claude(project, "hi")
        commands = next(event["slash_commands"] for event in events if event.get("subtype") == "init")
        self.assertEqual({c for c in commands if c.startswith("benchhand:")}, {f"benchhand:{n}" for n in VISIBLE})
        events = self.run_claude(project, "Call the Skill tool with skill 'benchhand:cad-bom', then stop.")
        calls = {block["id"] for event in events if event.get("type") == "assistant"
                 for block in event["message"]["content"]
                 if block.get("type") == "tool_use" and block.get("name") == "Skill"
                 and block["input"].get("skill") == "benchhand:cad-bom"}
        results = [block for event in events if event.get("type") == "user"
                   for block in event["message"]["content"]
                   if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") in calls]
        self.assertTrue(calls, "the model never called the Skill tool for cad-bom")
        self.assertTrue(results and not any(block.get("is_error") for block in results), results)


if __name__ == "__main__":
    unittest.main()
