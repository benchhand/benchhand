#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Install contract tests using only temporary project and home directories."""

from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
CAD = Path(__file__).resolve().parents[1]
INSTALLER = CAD / "harness/install.py"
HARNESS_PATHS = {
    "claude": (".claude/skills", ".claude/agents"),
    "codex": (".agents/skills", ".codex/agents"),
    "opencode": (".opencode/skills", ".opencode/agents"),
    "kiro": (".kiro/skills", ".kiro/agents"),
}
AGENT_FILES = {
    harness: sorted(path.name for path in (CAD / "harness" / harness / "agents").glob("*"))
    for harness in HARNESS_PATHS
}


def _install_module() -> object:
    spec = importlib.util.spec_from_file_location("benchhand_install", INSTALLER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _is_sys_path_edit(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"insert", "append"}
        and ast.unparse(node.func.value) == "sys.path"
    )


def _names_repo_root_dir(call: ast.AST) -> bool:
    """True when ``<...>.parents[n] / "harness"`` or ``/ "scripts"`` appears in the call."""
    return any(
        isinstance(n, ast.BinOp)
        and isinstance(n.op, ast.Div)
        and isinstance(n.right, ast.Constant)
        and n.right.value in {"harness", "scripts"}
        and isinstance(n.left, ast.Subscript)
        and isinstance(n.left.value, ast.Attribute)
        and n.left.value.attr == "parents"
        for n in ast.walk(call)
    )


def _repo_imports(tree: ast.AST) -> list[str]:
    """Return what a module pulls from repo-root harness/ or scripts/."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in {"harness", "scripts"}]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] in {"harness", "scripts"}:
                found.append(node.module)
        elif _is_sys_path_edit(node) and _names_repo_root_dir(node):
            found.append("sys.path edit")
    return found


class ShippedSkillFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.files = _install_module().skill_files()  # type: ignore[attr-defined]

    def test_no_shipped_file_is_a_test_file(self) -> None:
        bad = [
            str(path.relative_to(CAD))
            for path in self.files
            if path.match("test_*.py") or path.match("*_test.py") or "tests" in path.relative_to(CAD).parts
        ]
        self.assertEqual(bad, [])

    def test_no_shipped_python_reaches_into_harness_or_scripts(self) -> None:
        bad = {
            str(path.relative_to(CAD)): hits
            for path in self.files
            if path.suffix == ".py" and (hits := _repo_imports(ast.parse(path.read_text(encoding="utf-8"))))
        }
        self.assertEqual(bad, {})


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-install-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()

    def run_installer(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run([sys.executable, str(INSTALLER), *args], cwd=cwd or self.project,
                              env=env, capture_output=True, text=True, timeout=60)

    def run_ok(self, *args: str) -> str:
        result = self.run_installer(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_project_install_idempotence_exact_paths_and_uninstall(self) -> None:
        for harness, (skills_relative, agents_relative) in HARNESS_PATHS.items():
            with self.subTest(harness=harness):
                project = self.root / f"project-{harness}"
                project.mkdir()
                command = ["--harness", harness, "--project", str(project)]
                dry = self.run_installer(*command, "--dry-run")
                self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
                self.assertIn(str(project / skills_relative / "cad-flow/SKILL.md"), dry.stdout)
                for agent_file in AGENT_FILES[harness]:
                    self.assertIn(str(project / agents_relative / agent_file), dry.stdout)
                if harness == "opencode":
                    self.assertIn(str(project / ".opencode/commands/cad-flow.md"), dry.stdout)
                self.assertFalse((project / skills_relative).exists())

                installed = self.run_installer(*command)
                self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
                self.assertTrue((project / skills_relative / "cad-flow/SKILL.md").is_file())
                self.assertFalse(list((project / skills_relative).rglob("tests")))
                self.assertFalse(list((project / skills_relative).rglob("__pycache__")))
                for agent_file in AGENT_FILES[harness]:
                    self.assertTrue((project / agents_relative / agent_file).is_file())
                if harness == "opencode":
                    self.assertTrue((project / ".opencode/commands/cad-flow.md").is_file())

                second = self.run_installer(*command)
                self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
                self.assertIn("already installed; no files changed", second.stdout)
                removed = self.run_installer(*command, "--uninstall")
                self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
                self.assertFalse((project / skills_relative).exists())
                self.assertFalse((project / agents_relative).exists())
                if harness == "opencode":
                    self.assertFalse((project / ".opencode/commands").exists())
                self.assertFalse((project / ".benchhand-install.json").exists())

    def test_user_install_and_uninstall_use_contract_roots(self) -> None:
        for harness in HARNESS_PATHS:
            with self.subTest(harness=harness):
                command = ["--harness", harness, "--user"]
                if harness == "claude":
                    root, skills_relative, agents_relative = self.home / ".claude", "skills", "agents"
                elif harness == "codex":
                    root, skills_relative, agents_relative = self.home, ".agents/skills", ".codex/agents"
                elif harness == "opencode":
                    root, skills_relative, agents_relative = self.home / ".config/opencode", "skills", "agents"
                else:
                    root, skills_relative, agents_relative = self.home / ".kiro", "skills", "agents"
                dry = self.run_installer(*command, "--dry-run")
                self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
                self.assertIn(str(root / skills_relative / "cad-flow/SKILL.md"), dry.stdout)
                self.assertIn(str(root / agents_relative / AGENT_FILES[harness][0]), dry.stdout)
                if harness == "opencode":
                    self.assertIn(str(root / "commands/cad-flow.md"), dry.stdout)
                self.assertEqual(self.run_installer(*command).returncode, 0)
                if harness == "kiro":
                    for agent_file in AGENT_FILES[harness]:
                        profile = json.loads((root / agents_relative / agent_file).read_text(encoding="utf-8"))
                        self.assertTrue(profile["resources"])
                        for resource in profile["resources"]:
                            self.assertTrue(resource.startswith("skill://~/.kiro/skills/"), resource)
                            resource_path = self.home / resource.removeprefix("skill://~/")
                            self.assertTrue(resource_path.is_file(), f"{agent_file} resource is missing: {resource_path}")
                self.assertEqual(self.run_installer(*command, "--uninstall").returncode, 0)
                self.assertFalse((root / skills_relative).exists())
                self.assertFalse((root / agents_relative).exists())
                if harness == "opencode":
                    self.assertFalse((root / "commands").exists())

    def test_refuses_non_cad_file_and_does_not_partially_install(self) -> None:
        conflict = self.project / ".claude/agents/cad-analyst.md"
        conflict.parent.mkdir(parents=True)
        conflict.write_text("user agent\n", encoding="utf-8")
        result = self.run_installer("--harness", "claude", "--project", str(self.project))
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to overwrite non-cad file", result.stderr)
        self.assertEqual(conflict.read_text(encoding="utf-8"), "user agent\n")
        self.assertFalse((self.project / ".claude/skills/cad-flow/SKILL.md").exists())

    def test_modified_installed_file_is_not_overwritten_or_uninstalled(self) -> None:
        command = ["--harness", "claude", "--project", str(self.project)]
        self.assertEqual(self.run_installer(*command).returncode, 0)
        target = self.project / ".claude/skills/cad-flow/SKILL.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nlocal change\n", encoding="utf-8")
        rejected_install = self.run_installer(*command)
        self.assertEqual(rejected_install.returncode, 1)
        self.assertIn("installed CAD file was modified", rejected_install.stderr)
        rejected_uninstall = self.run_installer(*command, "--uninstall")
        self.assertEqual(rejected_uninstall.returncode, 1)
        self.assertIn("installed CAD file was modified", rejected_uninstall.stderr)
        self.assertTrue(target.is_file())

    def test_default_current_directory_is_project_but_checkout_is_refused_implicitly(self) -> None:
        command = ["--harness", "claude", "--dry-run"]
        result = self.run_installer(*command)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(str(self.project / ".claude/skills/cad-flow/SKILL.md"), result.stdout)
        checkout = self.run_installer(*command, cwd=CAD)
        self.assertEqual(checkout.returncode, 1)
        self.assertIn("pass --project explicitly", checkout.stderr)

    def test_refuses_missing_project_scope_and_symlink_destination(self) -> None:
        missing = self.run_installer("--harness", "codex", "--project", str(self.root / "missing"), "--dry-run")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("project directory must exist", missing.stderr)
        outside = self.root / "outside"
        outside.mkdir()
        symlink = self.project / ".kiro"
        symlink.symlink_to(outside, target_is_directory=True)
        unsafe = self.run_installer("--harness", "kiro", "--project", str(self.project), "--dry-run")
        self.assertEqual(unsafe.returncode, 1)
        self.assertIn("refusing symlink destination directory", unsafe.stderr)

    def test_tier_model_renders_only_the_chosen_harness_and_tier(self) -> None:
        project = self.root / "tiered"
        project.mkdir()
        self.run_ok("--harness", "all", "--project", str(project),
                    "--tier-model", "claude:cheap=haiku", "--tier-model", "opencode:cheap=anthropic/claude-haiku-4-5#low")
        claude = (project / ".claude/agents/cad-clerk.md").read_text(encoding="utf-8")
        self.assertIn('\nmodel: "haiku"\n', claude)
        self.assertNotIn("model: inherit", claude)
        opencode = (project / ".opencode/agents/cad-clerk.md").read_text(encoding="utf-8")
        self.assertIn('\nmodel: "anthropic/claude-haiku-4-5#low"\n', opencode)
        self.assertNotIn("inherit", opencode)
        for source in (CAD / "harness/codex/agents").iterdir():  # unmapped harness: byte-identical
            self.assertEqual((project / ".codex/agents" / source.name).read_bytes(), source.read_bytes())
        self.assertEqual((project / ".claude/agents/cad-analyst.md").read_bytes(), (CAD / "harness/claude/agents/cad-analyst.md").read_bytes())

    def test_tier_model_without_flag_installs_committed_bytes(self) -> None:
        self.run_ok("--harness", "all", "--project", str(self.project))
        for harness, (_skills, agents_relative) in HARNESS_PATHS.items():
            for source in (CAD / "harness" / harness / "agents").iterdir():
                self.assertEqual((self.project / agents_relative / source.name).read_bytes(), source.read_bytes())

    def test_tier_model_keeps_kiro_user_skill_uris(self) -> None:
        self.run_ok("--harness", "kiro", "--user", "--tier-model", "cheap=claude-haiku-4.5")
        profile = json.loads((self.home / ".kiro/agents/cad-clerk.json").read_text(encoding="utf-8"))
        self.assertEqual(profile["model"], "claude-haiku-4.5")
        self.assertTrue(all(r.startswith("skill://~/.kiro/skills/") for r in profile["resources"]))

    def test_tier_model_rejects_bad_specs_before_writing(self) -> None:
        for spec, message in (("premium=m", "unknown tier"), ("claude:cheap=inherit", "inherit"), ("cheap", "TIER=MODEL")):
            with self.subTest(spec=spec):
                result = self.run_installer("--harness", "all", "--project", str(self.project), "--tier-model", spec)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.project / ".claude").exists())


class InstallerWithoutPyYamlTests(unittest.TestCase):
    """Run install.py under `python -E -S`: no site-packages, so PyYAML cannot be imported."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-install-noyaml-")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)

    def run_bare(self, *args: str, harness: str = "claude") -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-E", "-S", str(INSTALLER), "--harness", harness, "--project", str(self.project), *args],
                              capture_output=True, text=True, timeout=60)

    def test_the_bare_interpreter_has_no_pyyaml(self) -> None:
        probe = subprocess.run([sys.executable, "-E", "-S", "-c", "import yaml"], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(probe.returncode, 0, "the isolated interpreter can import yaml; these tests would prove nothing")

    def test_default_install_does_not_need_pyyaml_except_for_claude(self) -> None:
        result = self.run_bare(harness="codex")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.project / ".codex/agents/cad-clerk.toml").is_file())

    def test_claude_install_without_pyyaml_fails_cleanly_and_writes_nothing(self) -> None:
        """Claude installs add `user-invocable: false` to the helper skills through the YAML loader."""
        result = self.run_bare()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("PyYAML", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_tier_model_without_pyyaml_fails_cleanly_and_writes_nothing(self) -> None:
        result = self.run_bare("--tier-model", "claude:cheap=haiku")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("PyYAML", result.stderr)
        self.assertIn("pip install", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(list(self.project.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
