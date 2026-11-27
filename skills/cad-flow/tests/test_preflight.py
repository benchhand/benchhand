#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Focused tests for CAD preflight: result semantics, runtime probes, warm-up plan and record, FreeCAD failures."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "preflight.py"
spec = importlib.util.spec_from_file_location("cad_preflight", SCRIPT)
assert spec is not None and spec.loader is not None
preflight = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = preflight
spec.loader.exec_module(preflight)
scriptenv = preflight.scriptenv



class HarnessInstallTests(unittest.TestCase):
    def test_receipt_must_include_installed_skills_and_agents_at_contract_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            skill = project / ".agents/skills/cad-flow/SKILL.md"
            agent = project / ".codex/agents/cad-analyst.toml"
            skill.parent.mkdir(parents=True)
            agent.parent.mkdir(parents=True)
            skill.write_text("skill", encoding="utf-8")
            agent.write_text("agent", encoding="utf-8")
            sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            (project / ".benchhand-install.json").write_text(json.dumps({"format": 1, "files": {
                ".agents/skills/cad-flow/SKILL.md": sha(skill),
                ".codex/agents/cad-analyst.toml": sha(agent)}}), encoding="utf-8")
            check = preflight._harness_check("codex", project, Path(temporary) / "home", {"HOME": temporary})
            self.assertEqual(check.status, "pass")
            self.assertIn("1 cad-* skill", check.detail)
            (project / ".codex/agents/cad-analyst.toml").unlink()
            self.assertEqual(preflight._harness_check("codex", project, Path(temporary) / "home", {}).status, "warn")

    def test_opencode_receipt_uses_installer_project_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            skill = project / ".opencode/skills/cad-flow/SKILL.md"
            agent = project / ".opencode/agents/cad-analyst.md"
            for path in (skill, agent):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("installed", encoding="utf-8")
            sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            (project / ".benchhand-install.json").write_text(json.dumps({"format": 1, "files": {
                ".opencode/skills/cad-flow/SKILL.md": sha(skill),
                ".opencode/agents/cad-analyst.md": sha(agent)}}), encoding="utf-8")
            check = preflight._harness_check("opencode", project, Path(temporary) / "home", {})
            self.assertEqual(check.status, "pass")
            self.assertEqual(preflight._harness_check("claude", project, Path(temporary) / "home", {}).status,
                             "warn")
class ReportTests(unittest.TestCase):
    def test_required_failure_sets_exit_code_and_summary(self) -> None:
        report = preflight.Report((
            preflight.Check("uv", "uv", "pass", True, "uv 1.0", ""),
            preflight.Check("freecad", "FreeCAD", "fail", True, "missing", "install it"),
        ))
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(report.counts, {"pass": 1, "fail": 1, "warn": 0})

    def test_report_json_includes_elapsed_time(self) -> None:
        report = preflight.Report((preflight.Check("uv", "uv", "pass", True, "uv", "", 1.25),))
        self.assertEqual(report.as_json()["checks"][0]["elapsed_s"], 1.25)
        self.assertIn("uv 1.2s", report.summary)

    def test_required_failure_keeps_json_fix_and_text_hint(self) -> None:
        report = preflight.Report((preflight.Check("freecad", "FreeCAD", "fail", True, "missing", "install it"),))
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(report.as_json()["checks"][0]["fix"], "install it")
        self.assertIn("Fix: install it", preflight.render_text(report))



def completed(args, stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args, returncode, stdout, "")


def runtime(identifier: str):
    return next(item for item in preflight.RUNTIMES if item.id == identifier)


class RuntimeProbeTests(unittest.TestCase):
    SITE = "/cache/archive-v0/AbC/lib/python3.12/site-packages"

    def test_kernel_probe_imports_in_the_environment_shared_by_dependency_set(self) -> None:
        kernel = runtime("cadquery")
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            return completed(args, 'CAD_PREFLIGHT:{"version": "2.8.0", "file": "%s/cadquery/__init__.py"}\n' % self.SITE)

        with patch.object(preflight.subprocess, "run", side_effect=run):
            check = preflight._runtime_check("uv", kernel)
        self.assertEqual(check.status, "pass", check.detail)
        self.assertEqual(len(calls), 1, "a shared environment needs no separate sync")
        self.assertEqual(calls[0][:-2], scriptenv.python_command(kernel.script, uv="uv"))
        self.assertEqual(calls[0][-2], "-c")
        self.assertIn("import cadquery", calls[0][-1])
        self.assertIn(f"{self.SITE} (environment shared by dependency set)", check.detail)

    def test_the_cad_model_probe_imports_jsonschema_because_guideline_discovery_needs_it(self) -> None:
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            return completed(args, 'CAD_PREFLIGHT:{"version": "8.0", "file": "%s/OCP/__init__.py"}\n' % self.SITE)

        with patch.object(preflight.subprocess, "run", side_effect=run):
            preflight._runtime_check("uv", runtime("cad-model"))
        self.assertIn("jsonschema", runtime("cad-model").imports)
        self.assertIn("jsonschema", calls[0][-1])

    def test_heavy_scripts_probe_the_environment_shared_by_dependency_set(self) -> None:
        for identifier in ("cad-model", "ocp-viewer", "playwright-chromium"):
            with self.subTest(identifier=identifier):
                runtime_item = preflight.CHROMIUM if identifier == "playwright-chromium" else runtime(identifier)
                calls = []

                def run(args, **kwargs):
                    calls.append(args)
                    return completed(args, 'CAD_PREFLIGHT:{"version": "%s", "file": "%s/OCP/__init__.py"}\n'
                                     % (preflight.scriptenv.pinned(runtime_item.script, runtime_item.distribution),
                                        self.SITE))

                with patch.object(preflight.subprocess, "run", side_effect=run):
                    check = preflight._runtime_check("uv", runtime_item)
                self.assertEqual(check.status, "pass", check.detail)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][:-2], preflight.scriptenv.python_command(runtime_item.script, uv="uv"))
                self.assertIn(f"{self.SITE} (environment shared by dependency set)", check.detail)

    def test_heavy_runtime_uses_the_dependency_set_command(self) -> None:
        for identifier in ("cad-model", "ocp-viewer", "playwright-chromium"):
            with self.subTest(identifier=identifier):
                item = preflight.CHROMIUM if identifier == "playwright-chromium" else runtime(identifier)
                prefix, timeout = preflight._environment("uv", item)
                self.assertEqual(prefix, preflight.scriptenv.python_command(item.script, uv="uv"))
                self.assertEqual(timeout, preflight.SYNC_TIMEOUT + preflight.IMPORT_TIMEOUT)

    def test_a_version_other_than_the_header_pin_fails(self) -> None:
        def run(args, **kwargs):
            return completed(args, 'CAD_PREFLIGHT:{"version": "0.12.0", "file": "%s/build123d/__init__.py"}\n' % self.SITE)

        with patch.object(preflight.subprocess, "run", side_effect=run):
            check = preflight._runtime_check("uv", runtime("build123d"))
        self.assertEqual(check.status, "fail")
        self.assertIn("run_build123d.py pins 0.13.0", check.detail)

    def test_a_failed_import_reports_uv_last_error_line(self) -> None:
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, "", "resolving\nerror: no wheel for cadquery-ocp\n")

        with patch.object(preflight.subprocess, "run", side_effect=run):
            check = preflight._runtime_check("uv", runtime("cadquery"))
        self.assertEqual(check.status, "fail")
        self.assertIn("error: no wheel for cadquery-ocp", check.detail)

    def test_missing_ocp_shared_library_reports_system_package_fix(self) -> None:
        def run(environment, code):
            raise RuntimeError("ImportError: libGL.so.1: cannot open shared object file: No such file or directory")

        with patch.object(preflight, "_python", side_effect=run):
            check = preflight._runtime_check("uv", runtime("cad-model"))
        self.assertEqual(check.status, "fail")
        self.assertIn("libglvnd-glx", check.fix)
        self.assertNotIn("first run needs network access", check.fix)

    def test_chromium_install_runs_playwright_in_the_environment_shared_by_dependency_set(self) -> None:
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            return completed(args, 'CAD_PREFLIGHT:{"version": "1.64.1", "chromium": "150.0", "path": "/c/chrome"}\n')

        with patch.object(preflight.subprocess, "run", side_effect=run):
            check = preflight._chromium_check("uv", install=True)
        self.assertEqual(check.status, "pass", check.detail)
        self.assertIn([*preflight.scriptenv.python_command(preflight.CHROMIUM.script, uv="uv"), "-m",
                       "playwright", "install", "chromium"], calls)
        self.assertIn("p.chromium.launch(headless=True)", calls[-1][-1])

    def test_chromium_passes_with_any_playwright_version_and_reports_the_one_imported(self) -> None:
        def run(args, **kwargs):
            return completed(args, 'CAD_PREFLIGHT:{"version": "1.65.2", "chromium": "150.0", "path": "/c/chrome"}\n')

        with patch.object(preflight.subprocess, "run", side_effect=run):
            check = preflight._chromium_check("uv")
        self.assertEqual(check.status, "pass", check.detail)
        self.assertIn("Playwright 1.65.2 launches Chromium 150.0", check.detail)

    def test_cold_chromium_reports_the_missing_browser_not_playwrights_banner(self) -> None:
        # Before the warm-up installs Chromium, Playwright's launch error ends in a boxed banner;
        # the report must carry the exception line that names the missing executable.
        stderr = "\n".join((
            "Traceback (most recent call last):",
            '  File "<string>", line 5, in <module>',
            '  File "/cache/archive-v0/x/lib/python3.12/site-packages/playwright/_impl/_connection.py", '
            "line 636, in wrap_api_call",
            "    raise rewrite_error(error, f\"{parsed_st['apiName']}: {error}\") from None",
            "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at "
            "/pw/chromium_headless_shell-1248/chrome-headless-shell-mac-arm64/chrome-headless-shell",
            "╔════════════════════════════════════════════════════════════╗",
            "║ Looks like Playwright was just installed or updated.       ║",
            "║ Please run the following command to download new browsers: ║",
            "║                                                            ║",
            "║     playwright install                                     ║",
            "║                                                            ║",
            "║ <3 Playwright Team                                         ║",
            "╚════════════════════════════════════════════════════════════╝",
        )) + "\n"

        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, "", stderr)

        with patch.object(preflight.subprocess, "run", side_effect=run):
            check = preflight._chromium_check("uv")
        self.assertEqual(check.status, "fail")
        self.assertIn("Executable doesn't exist at /pw/chromium_headless_shell-1248/", check.detail)
        self.assertNotIn("╚", check.detail)
        self.assertIn("preflight.py --warm", check.fix)


class WarmPlanTests(unittest.TestCase):
    def plan(self, warm: bool) -> tuple[list[str], dict[str, object]]:
        order: list[str] = []
        seen: dict[str, object] = {}

        def passing(identifier: str) -> preflight.Check:
            order.append(identifier)
            return preflight.Check(identifier, identifier, "pass", True, "", "")

        def chromium(uv, install=False):
            seen["install"] = install
            return passing("playwright-chromium")

        def vendor(environ, fill=False):
            seen["fill"] = fill
            return passing("three-vendor-cache")

        cad_checks = [
            preflight.Check(identifier, identifier, "pass", True, "", "")
            for identifier in ("freecad-command", "freecad-version", "techdraw-templates")
        ]
        with patch.object(preflight, "_uv_check", side_effect=lambda env: (passing("uv"), "uv")), \
                patch.object(preflight, "_runtime_check", side_effect=lambda uv, item: passing(item.id)), \
                patch.object(preflight, "_chromium_check", side_effect=chromium), \
                patch.object(preflight, "_vendor_check", side_effect=vendor), \
                patch.object(preflight, "_harness_check", side_effect=lambda name, *a: passing(f"harness-{name}")), \
                patch("sys.stderr"):
            report = preflight.run_checks({"HOME": "/nowhere"}, Path("/p"), Path("/nowhere"), "codex",
                                          warm=warm, cad_checks=cad_checks)
        self.assertEqual(order, ["uv", "cadquery", "build123d", "cad-model", "ocp-viewer",
                                 "playwright-chromium", "three-vendor-cache", "harness-codex"])
        return order, seen

    def test_warm_first_loads_every_runtime_after_cad_tool_and_fetches_what_checks_need(self) -> None:
        order, seen = self.plan(warm=True)
        self.assertEqual(order, ["uv", "cadquery", "build123d", "cad-model", "ocp-viewer",
                                 "playwright-chromium", "three-vendor-cache", "harness-codex"])
        self.assertEqual(seen, {"install": True, "fill": True})

    def test_plain_preflight_installs_and_fetches_nothing(self) -> None:
        self.assertEqual(self.plan(warm=False)[1], {"install": False, "fill": False})


class WarmRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.environ = {"XDG_CACHE_HOME": folder.name, "HOME": folder.name}
        self.path = preflight.warm_record_path(self.environ)

    def report(self, status: str) -> dict:
        checks = tuple(preflight.Check(name, name, status if name == "cadquery" else "pass", True, "d", "fix it")
                       for name in preflight.WARM_IDS)
        return preflight.Report(checks).as_json()

    def test_the_record_is_keyed_by_this_plugin_copy_under_the_cache_home(self) -> None:
        self.assertTrue(self.path.is_relative_to(Path(self.environ["XDG_CACHE_HOME"]) / "cad-flow" / "warm"))

    def test_warm_records_running_then_its_report(self) -> None:
        states = []

        def checks(*args, warm=False):
            states.append((preflight._read_record(self.path)["state"], warm))
            return preflight.Report((preflight.Check("uv", "uv", "pass", True, "", ""),))

        with patch.object(preflight, "run_checks", side_effect=checks):
            preflight.warm(self.environ)
        record = preflight._read_record(self.path)
        self.assertEqual(states, [("running", True)])
        self.assertEqual((record["state"], record["pid"], record["report"]["exit_code"]), ("finished", os.getpid(), 0))

    def test_status_distinguishes_missing_running_stopped_and_finished(self) -> None:
        self.assertEqual(preflight.warm_status(self.environ)[0], preflight.EXIT_NO_WARM)
        preflight._write_record(self.path, {"state": "running", "pid": os.getpid(), "started_at": "t0"})
        self.assertEqual(preflight.warm_status(self.environ)[0], preflight.EXIT_RUNNING)
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        preflight._write_record(self.path, {"state": "running", "pid": gone.pid, "started_at": "t0"})
        code, text = preflight.warm_status(self.environ)
        self.assertEqual(code, preflight.EXIT_NO_WARM)
        self.assertIn("stopped without a report", text)
        for status, expected in (("pass", 0), ("fail", 1)):
            preflight._write_record(self.path, {"state": "finished", "pid": 1, "started_at": "t0",
                                                "finished_at": "t1", "report": self.report(status)})
            code, text = preflight.warm_status(self.environ)
            with self.subTest(status=status):
                self.assertEqual(code, expected)
                self.assertIn("Warm-up finished at t1", text)

    def test_background_start_detaches_the_warm_up_and_returns_once_it_runs(self) -> None:
        spawned = []

        class Child:
            pid = 424242

            def __init__(self, command, **kwargs):
                spawned.append((command, kwargs))
                preflight._write_record(self_path, {"state": "running", "pid": self.pid, "started_at": "t0"})

            def poll(self):
                return None

        self_path = self.path
        with patch.object(preflight.subprocess, "Popen", Child):
            started = preflight.start_background(self.environ, harness="codex")
        self.assertTrue(started["started"], started)
        command, kwargs = spawned[0]
        self.assertEqual(command[1:], [str(SCRIPT), "--warm", "--harness", "codex"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(started["log"], str(self.path.with_suffix(".log")))

    def test_background_warm_up_outlives_the_launchers_ephemeral_uv_environment(self) -> None:
        # `uv run` deletes its ephemeral environment once the launcher exits; the detached child
        # must neither run from that environment nor hand its VIRTUAL_ENV to the uv calls it makes.
        launcher_env = Path(self.environ["HOME"]) / "builds-v0" / ".tmp-launcher"
        spawned = []

        class Child:
            pid = 424243

            def __init__(self, command, **kwargs):
                spawned.append((command, kwargs))
                preflight._write_record(self_path, {"state": "running", "pid": self.pid, "started_at": "t0"})

            def poll(self):
                return None

        self_path = self.path
        base_interpreter = sys.executable
        environ = {**self.environ, "VIRTUAL_ENV": str(launcher_env), "PATH": os.environ.get("PATH", "")}
        with patch.object(preflight.subprocess, "Popen", Child), \
                patch.object(preflight.sys, "_base_executable", base_interpreter, create=True), \
                patch.object(preflight.sys, "executable", str(launcher_env / "bin" / "python")):
            started = preflight.start_background(environ)
        self.assertTrue(started["started"], started)
        command, kwargs = spawned[0]
        self.assertEqual(command[0], base_interpreter)
        self.assertNotIn("VIRTUAL_ENV", kwargs["env"])
        self.assertEqual(kwargs["env"]["PATH"], environ["PATH"])

    def test_a_running_warm_up_is_reused_instead_of_started_twice(self) -> None:
        preflight._write_record(self.path, {"state": "running", "pid": os.getpid(), "started_at": "t0"})
        with patch.object(preflight.subprocess, "Popen") as popen:
            started = preflight.start_background(self.environ)
        popen.assert_not_called()
        self.assertEqual((started["started"], started["pid"]), (False, os.getpid()))


class SkillTreeChangeTests(unittest.TestCase):
    def test_report_completes_when_concept_scripts_vanish_during_the_cold_first_load(self) -> None:
        # A cold first run spends minutes first-loading the runtimes. An uninstall, reinstall or
        # plugin update in that window removes sibling skill files; the vendor check that follows
        # must still verify the (cold, empty) cache and report, not raise ModuleNotFoundError.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(preflight.SKILLS, root / "skills", ignore=shutil.ignore_patterns("__pycache__"))
            with patch.dict(sys.modules), patch.object(sys, "path", list(sys.path)):
                for name in ("serve", "cad_record_layout", "freecad_runtime", "cadmodel", "cadmodel.scriptenv"):
                    sys.modules.pop(name, None)
                copy_spec = importlib.util.spec_from_file_location(
                    "cad_preflight_copy", root / "skills" / "cad-flow" / "scripts" / "preflight.py")
                assert copy_spec is not None and copy_spec.loader is not None
                copy = importlib.util.module_from_spec(copy_spec)
                sys.modules[copy_spec.name] = copy
                copy_spec.loader.exec_module(copy)

                def passing(identifier: str):
                    return copy.Check(identifier, identifier, "pass", True, "", "")

                def first_load(uv, runtime):
                    shutil.rmtree(root / "skills" / "cad-concept", ignore_errors=True)
                    return passing(runtime.id)

                with patch.object(copy, "_uv_check", return_value=(passing("uv"), "uv")), \
                        patch.object(copy, "_runtime_check", side_effect=first_load), \
                        patch.object(copy, "_chromium_check", side_effect=lambda uv, install=False: passing("playwright-chromium")), \
                        patch("sys.stderr"):
                    report = copy.run_checks(
                        {"XDG_CACHE_HOME": str(root / "cache"), "HOME": folder}, root, root, "claude",
                        cad_checks=[passing(name) for name in ("freecad-command", "freecad-version", "techdraw-templates")])
            self.assertFalse((root / "skills" / "cad-concept").exists())
        vendor = next(check for check in report.checks if check.id == "three-vendor-cache")
        self.assertEqual((vendor.status, vendor.required), ("fail", True))
        self.assertIn("missing module(s)", vendor.detail)
        self.assertIn("preflight.py --warm", vendor.fix)
        self.assertEqual(report.exit_code, 1)


class MainTests(unittest.TestCase):
    def test_main_returns_report_exit_code_and_json(self) -> None:
        report = preflight.Report((preflight.Check("uv", "uv", "fail", True, "missing", "install"),))
        with patch.object(preflight, "run_checks", return_value=report), patch("sys.stdout") as stdout:
            result = preflight.main(["--json", "--harness", "codex"])
        self.assertEqual(result, 1)
        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertIn('"exit_code": 1', output)

    def test_invalid_status_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            preflight.Check("bad", "bad", "unknown", True, "", "")

class CadToolLookupTests(unittest.TestCase):
    def test_metadata_defines_name_version_probe_and_release(self) -> None:
        tool = preflight.CAD_TOOL
        self.assertEqual((tool.name, tool.required_version), ("FreeCAD", "1.1.4"))
        self.assertEqual(tool.version_args, ("--version",))
        self.assertEqual(tool.download_url, "https://github.com/FreeCAD/FreeCAD/releases/tag/1.1.4")

    def test_lookup_metadata_is_authoritative(self) -> None:
        self.assertEqual(tuple(value for _, value in preflight.CAD_TOOL.lookup_candidates),
                         ("FREECADCMD", "freecadcmd", "FreeCADCmd",
                          "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
                          ".local/bin/freecadcmd"))

    def test_configured_command_takes_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = root / "configured"
            configured.touch()
            with patch.object(preflight.shutil, "which", return_value="/path/freecadcmd"):
                found = preflight.find_freecadcmd({"FREECADCMD": str(configured), "PATH": "/bin"}, root)
        self.assertEqual(found, configured)

    def test_path_command_precedes_app_bundle_and_user_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "FreeCAD.app" / "Contents" / "Resources" / "bin" / "freecadcmd"
            app.parent.mkdir(parents=True)
            app.touch()
            user = root / ".local" / "bin" / "freecadcmd"
            user.parent.mkdir(parents=True)
            user.touch()
            path_command = root / "path-freecadcmd"
            with patch.object(preflight.shutil, "which", side_effect=[str(path_command)]):
                found = preflight.find_freecadcmd({"PATH": "/path"}, root, "darwin", app)
        self.assertEqual(found, path_command)

    def test_macos_bundle_precedes_user_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "FreeCAD.app" / "Contents" / "Resources" / "bin" / "freecadcmd"
            app.parent.mkdir(parents=True)
            app.touch()
            user = root / ".local" / "bin" / "freecadcmd"
            user.parent.mkdir(parents=True)
            user.touch()
            user.chmod(0o755)
            with patch.object(preflight.shutil, "which", return_value=None):
                found = preflight.find_freecadcmd({"PATH": ""}, root, "darwin", app)
        self.assertEqual(found, app)

    def test_user_command_is_last_supported_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = root / ".local" / "bin" / "freecadcmd"
            command.parent.mkdir(parents=True)
            command.touch()
            command.chmod(0o755)
            with patch.object(preflight.shutil, "which", return_value=None):
                found = preflight.find_freecadcmd({"PATH": ""}, root, "linux")
        self.assertEqual(found, command)

    def test_required_freecad_that_is_missing_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(preflight.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "CAD_REQUIRE_FREECAD=1 .*freecadcmd"):
                    preflight.find_freecadcmd({"PATH": "", "CAD_REQUIRE_FREECAD": "1"}, Path(temporary), "linux")

    def test_required_freecad_rejects_an_unusable_override_without_falling_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            not_executable = root / "freecadcmd"
            not_executable.touch()
            not_executable.chmod(0o644)
            for configured in (root / "missing", not_executable):
                with self.subTest(configured=configured.name):
                    environ = {"FREECADCMD": str(configured), "PATH": "/path", "CAD_REQUIRE_FREECAD": "1"}
                    with patch.object(preflight.shutil, "which", return_value="/path/freecadcmd"):
                        with self.assertRaisesRegex(RuntimeError, f"CAD_REQUIRE_FREECAD=1 .*{configured}"):
                            preflight.find_freecadcmd(environ, root, "linux")

    def test_required_freecad_that_is_present_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            command = Path(temporary) / "freecadcmd"
            command.touch()
            command.chmod(0o755)
            with patch.object(preflight.shutil, "which", return_value=str(command)):
                found = preflight.find_freecadcmd({"PATH": temporary, "CAD_REQUIRE_FREECAD": "1"},
                                                  Path(temporary), "linux")
        self.assertEqual(found, command)

    def test_installer_managed_appimage_location_is_not_searched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            managed = root / "opt" / "freecad-1.1.4" / "squashfs-root" / "usr" / "bin" / "freecadcmd"
            managed.parent.mkdir(parents=True)
            managed.touch()
            managed.chmod(0o755)
            with patch.object(preflight.shutil, "which", return_value=None):
                found = preflight.find_freecadcmd({"PATH": ""}, root, "linux")
        self.assertIsNone(found)

    def test_preflight_and_runtime_share_authoritative_home_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = root / ".local" / "bin" / "freecadcmd"
            command.parent.mkdir(parents=True)
            command.write_text("#!/bin/sh\n", encoding="utf-8")
            command.chmod(0o755)
            environ = {"HOME": str(root), "PATH": str(root / "empty")}
            self.assertEqual(preflight.find_freecadcmd(environ, platform_name="linux"), command)
            self.assertEqual(preflight.freecad_runtime.find_freecadcmd(environ, home=root, platform_name="linux"), command)

class FreeCADTests(unittest.TestCase):
    def test_missing_configured_freecad_fails_with_install_hint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-preflight-missing-freecad-") as directory:
            root = Path(directory)
            path = root / "empty-path"
            path.mkdir()
            missing = root / "missing" / "freecadcmd"
            checks = preflight._timed_freecad_checks({"FREECADCMD": str(missing), "PATH": str(path)})
        command = checks[0]
        self.assertEqual(command.status, "fail")
        self.assertIn("FreeCAD 1.1.4 freecadcmd was not found", command.detail)
        self.assertIn("Install FreeCAD 1.1.4", command.fix)
        self.assertIn("https://github.com/FreeCAD/FreeCAD/releases/tag/1.1.4", command.fix)
        for candidate in ("FREECADCMD", "PATH", "macOS app bundle", "~/.local/bin/freecadcmd"):
            self.assertIn(candidate, command.fix)
        self.assertEqual(len(checks), 2)
        self.assertEqual(checks[1].status, "fail")


class FEASolverCheckTests(unittest.TestCase):
    """The fea-solvers check reports the ccx and Gmsh that FreeCAD resolves in its own process (fea_solvers.probe)."""

    PROBED = {"freecad": {"path": "/b/bin/freecadcmd", "version": "1.1.4"}, "resource_dir": "/b/share/",
              "calculix": {"path": "/b/bin/ccx", "version": "2.23", "source": "FreeCAD's lookup"},
              "gmsh": {"path": "/b/bin/gmsh", "version": "4.15.0", "source": "FreeCAD's lookup"}}

    def solver_check(self, probe: dict, enabled: bool = True) -> preflight.Check:
        fea = preflight.FeaSetting(enabled)
        with tempfile.TemporaryDirectory(prefix="cad-preflight-fea-") as directory:
            command = Path(directory) / "freecadcmd"
            command.write_text("#!/bin/sh\n", encoding="utf-8")
            command.chmod(0o755)
            with patch.object(preflight, "cad_tool_version", return_value=("1.1.4", "FreeCAD 1.1.4")), \
                    patch.object(preflight.freecad_runtime, "techdraw_templates",
                                 return_value=(directory, frozenset(preflight.EXPECTED_TEMPLATES))), \
                    patch.object(preflight.fea_solvers, "probe", **probe) as probed, patch("sys.stderr"):
                checks = preflight._timed_freecad_checks({"FREECADCMD": str(command), "PATH": ""}, fea=fea)
        if enabled:
            self.assertEqual(probed.call_args.args[0], command)
        else:
            probed.assert_not_called()
        return next(check for check in checks if check.id == "fea-solvers")

    def test_templates_listed_inside_freecad_pass_after_its_mount_is_gone(self) -> None:
        # A FREECADCMD wrapper for an AppImage: the probe listed the templates under the process's mount.
        gone = "/tmp/.mount_gone/usr/share/Mod/TechDraw/Templates"
        probed = {**self.PROBED, "techdraw_templates": {"dir": gone, "files": list(preflight.EXPECTED_TEMPLATES)}}
        with tempfile.TemporaryDirectory(prefix="cad-preflight-fea-") as directory:
            command = Path(directory) / "freecadcmd"
            command.write_text("#!/bin/sh\n", encoding="utf-8")
            command.chmod(0o755)
            with patch.object(preflight, "cad_tool_version", return_value=("1.1.4", "FreeCAD 1.1.4")), \
                    patch.object(preflight.fea_solvers, "probe", return_value=probed), patch("sys.stderr"):
                checks = preflight._timed_freecad_checks({"FREECADCMD": str(command), "PATH": ""})
        self.assertFalse(Path(gone).exists())
        check = next(check for check in checks if check.id == "techdraw-templates")
        self.assertEqual(check.status, "pass", check.detail)
        self.assertIn(gone, check.detail)

    def test_probed_solvers_pass_with_their_versions_and_paths(self) -> None:
        check = self.solver_check({"return_value": self.PROBED})
        self.assertEqual((check.status, check.required), ("pass", True))
        for text in ("CalculiX ccx 2.23 at /b/bin/ccx", "Gmsh 4.15.0 at /b/bin/gmsh", "FreeCAD's lookup"):
            self.assertIn(text, check.detail)

    def test_a_solver_freecad_cannot_resolve_fails_with_its_reason(self) -> None:
        probed = {**self.PROBED, "calculix": {"error": "ccx not found: $CCX is unset"}}
        check = self.solver_check({"return_value": probed})
        self.assertEqual(check.status, "fail")
        self.assertIn("ccx not found: $CCX is unset", check.detail)
        self.assertIn("CCX", check.fix)

    def test_a_freecad_that_cannot_run_the_probe_fails(self) -> None:
        check = self.solver_check({"side_effect": preflight.fea_solvers.ProbeError("wrote no result marker")})
        self.assertEqual(check.status, "fail")
        self.assertIn("wrote no result marker", check.detail)

    def test_without_fea_a_missing_solver_is_info_and_nothing_is_probed(self) -> None:
        check = self.solver_check({"return_value": {**self.PROBED, "calculix": {"error": "ccx not found"}}},
                                  enabled=False)
        self.assertEqual((check.status, check.required), ("info", False))
        self.assertIn("FEA is not enabled", check.detail)
        self.assertIn("ccx and Gmsh", check.detail)

    def test_with_fea_a_missing_solver_fails(self) -> None:
        check = self.solver_check({"return_value": {**self.PROBED, "gmsh": {"error": "gmsh not found"}}},
                                  enabled=True)
        self.assertEqual((check.status, check.required), ("fail", True))


class InfoStatusTests(unittest.TestCase):
    def test_the_report_schema_rejects_negative_counts_and_durations(self) -> None:
        import jsonschema

        schema = json.loads((SCRIPT.parents[2] / "cad-record" / "schemas" / "preflight-report.schema.json")
                            .read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        report = preflight.Report((preflight.Check("uv", "uv", "pass", True, "d", "", 0.5),)).as_json()
        validator.validate(report)
        for mutate in (lambda r: r["counts"].__setitem__("pass", -1), lambda r: r["counts"].__setitem__("fail", -1),
                       lambda r: r["counts"].__setitem__("warn", -1),
                       lambda r: r["checks"][0].__setitem__("elapsed_s", -0.1)):
            broken = json.loads(json.dumps(report))
            mutate(broken)
            with self.assertRaises(jsonschema.ValidationError):
                validator.validate(broken)

    def test_the_json_report_with_every_status_matches_its_schema(self) -> None:
        import jsonschema

        schema = json.loads((SCRIPT.parents[2] / "cad-record" / "schemas" / "preflight-report.schema.json")
                            .read_text(encoding="utf-8"))
        report = preflight.Report(tuple(
            preflight.Check(status, status, status, status != "info", "detail", "fix", 0.5)
            for status in ("pass", "fail", "warn", "info")))
        jsonschema.Draft202012Validator(schema).validate(report.as_json())
        bogus = report.as_json()
        bogus["checks"][0]["status"] = "skipped"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(schema).validate(bogus)
    def test_an_info_check_is_not_counted_and_does_not_fail_the_report(self) -> None:
        report = preflight.Report((
            preflight.Check("uv", "uv", "pass", True, "uv", ""),
            preflight.Check("fea-solvers", "FEA", "info", True, "not enabled", ""),
        ))
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.counts, {"pass": 1, "fail": 0, "warn": 0})

    def test_run_checks_continues_past_an_info_check(self) -> None:
        cad_checks = [preflight.Check("fea-solvers", "FEA", "info", True, "not enabled", "")]
        ran = []
        ok = preflight.Check("uv", "uv", "pass", True, "", "")
        with patch.object(preflight, "_uv_check", side_effect=lambda env: (ran.append("uv") or ok, None)), \
                patch.object(preflight, "_vendor_check", return_value=ok), \
                patch.object(preflight, "_harness_check", return_value=ok), patch("sys.stderr"):
            report = preflight.run_checks({"HOME": "/nowhere"}, Path("/p"), Path("/nowhere"), "codex",
                                          cad_checks=cad_checks)
        self.assertEqual(ran, ["uv"])
        self.assertGreater(len(report.checks), 1)


class StandaloneRunChecksFEATests(unittest.TestCase):
    """The public run_checks path, with the project's own product.yaml choosing FEA."""

    def run_in_project(self, product: str | None) -> preflight.Report:
        with tempfile.TemporaryDirectory(prefix="cad-preflight-run-") as directory:
            root = Path(directory)
            command = root / "freecadcmd"
            command.write_text("#!/bin/sh\n", encoding="utf-8")
            command.chmod(0o755)
            if product is not None:
                (root / "hw").mkdir()
                (root / "hw" / "product.yaml").write_text(product, encoding="utf-8")
            missing = {**FEASolverCheckTests.PROBED, "calculix": {"error": "ccx not found: $CCX is unset"}}
            with patch.object(preflight, "cad_tool_version", return_value=("1.1.4", "FreeCAD 1.1.4")), \
                    patch.object(preflight.freecad_runtime, "techdraw_templates",
                                 return_value=(directory, frozenset(preflight.EXPECTED_TEMPLATES))), \
                    patch.object(preflight.fea_solvers, "probe", return_value=missing), \
                    patch.object(preflight, "_uv_check", return_value=(
                        preflight.Check("uv", "uv", "pass", True, "", ""), None)), \
                    patch.object(preflight, "_vendor_check", return_value=preflight.Check(
                        "three-vendor-cache", "v", "pass", True, "", "")), \
                    patch("sys.stderr"):
                return preflight.run_checks({"FREECADCMD": str(command), "PATH": "", "HOME": directory},
                                            cwd=root, home=root, harness="codex")

    def test_a_missing_solver_fails_the_run_when_the_project_enables_fea(self) -> None:
        report = self.run_in_project("flow:\n  profile:\n    fea:\n      enabled: true\n")
        solver = next(check for check in report.checks if check.id == "fea-solvers")
        self.assertEqual((solver.status, solver.required), ("fail", True))
        self.assertIn("ccx not found", solver.detail)
        self.assertEqual(report.exit_code, 1)

    def test_a_malformed_profile_fails_by_name_through_run_checks(self) -> None:
        for text in ("- a\n- b\n", "flow: [a]\n", "flow:\n", "flow:\n  profile:\n",
                     "flow:\n  profile: null\n", "flow:\n  profile:\n    fea:\n", "flow:\n  profile:\n    fea: []\n",
                     "when: 2026-13-45\n"):
            with self.subTest(text=text):
                report = self.run_in_project(text)
                named = [check for check in report.checks if check.id == "fea-profile"]
                self.assertEqual([(c.status, c.required) for c in named], [("fail", True)])
                self.assertIn("product.yaml", named[0].detail)
                self.assertEqual(report.exit_code, 1)

    def test_a_null_document_and_an_unreadable_folder_are_not_absent_configuration(self) -> None:
        for text in ("null\n", "~\n", ""):
            with self.subTest(text=text):
                named = [c for c in self.run_in_project(text).checks if c.id == "fea-profile"]
                self.assertEqual([c.status for c in named], ["fail"])
                self.assertIn("product.yaml", named[0].detail)
        with tempfile.TemporaryDirectory(prefix="cad-preflight-denied-") as directory:
            hw = Path(directory) / "hw"
            hw.mkdir()
            (hw / "product.yaml").write_text("name: widget\n", encoding="utf-8")
            hw.chmod(0)
            try:
                setting = preflight.project_fea(Path(directory))
            finally:
                hw.chmod(0o755)
        self.assertFalse(setting.enabled)
        self.assertIn("product.yaml", setting.invalid or "")

    def test_hw_that_is_a_file_is_a_finding_not_absent_configuration(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-preflight-hwfile-") as directory:
            (Path(directory) / "hw").write_text("not a folder\n", encoding="utf-8")
            setting = preflight.project_fea(Path(directory))
        self.assertFalse(setting.enabled)
        self.assertIn("product.yaml", setting.invalid or "")

    def test_absent_keys_are_not_malformed(self) -> None:
        for text in (None, "name: widget\n", "flow:\n  stage: ground\n", "flow:\n  profile:\n    other: 1\n"):
            with self.subTest(text=text):
                report = self.run_in_project(text)
                self.assertFalse([check for check in report.checks if check.id == "fea-profile"])

    def test_an_invalid_profile_is_named_when_freecad_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-preflight-nofc-") as directory:
            root = Path(directory)
            (root / "hw").mkdir()
            (root / "hw" / "product.yaml").write_text("flow: [a]\n", encoding="utf-8")
            with patch("sys.stderr"):
                report = preflight.run_checks({"FREECADCMD": str(root / "missing"), "PATH": "", "HOME": directory},
                                              cwd=root, home=root, harness="codex")
        by_id = {check.id: check.status for check in report.checks}
        self.assertEqual(by_id["freecad-command"], "fail")
        self.assertEqual(by_id["fea-profile"], "fail")

    def test_the_same_missing_solver_is_info_when_the_project_has_no_fea(self) -> None:
        report = self.run_in_project(None)
        solver = next(check for check in report.checks if check.id == "fea-solvers")
        self.assertEqual((solver.status, solver.required), ("info", False))
        self.assertGreater(len(report.checks), 3)


class FEASettingTests(unittest.TestCase):
    def setting(self, text: str | bytes | None, as_directory: bool = False) -> preflight.FeaSetting:
        with tempfile.TemporaryDirectory(prefix="cad-preflight-profile-") as directory:
            root = Path(directory)
            if as_directory:
                (root / "hw" / "product.yaml").mkdir(parents=True)
            elif text is not None:
                (root / "hw").mkdir()
                product = root / "hw" / "product.yaml"
                product.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
            result = preflight.project_fea(root)
            if result.invalid:
                self.assertIn(str(root / "hw" / "product.yaml"), result.invalid)
            return result

    def test_missing_configuration_is_off_and_valid(self) -> None:
        for text in (None, "name: widget\n", "flow:\n  stage: ground\n", "flow:\n  profile:\n    other: 1\n",
                     "flow:\n  profile:\n    fea:\n      enabled: false\n"):
            with self.subTest(text=text):
                self.assertEqual(self.setting(text), preflight.FeaSetting(False, None))

    def test_boolean_true_enables_fea(self) -> None:
        text = "flow:\n  profile:\n    fea:\n      enabled: true\n      mode: fast\n"
        self.assertEqual(self.setting(text), preflight.FeaSetting(True, None))

    def test_invalid_configuration_is_named_with_its_path_and_cause(self) -> None:
        for text, cause in (
            ('flow:\n  profile:\n    fea:\n      enabled: "true"\n', "boolean"),
            ("flow:\n  profile:\n    fea: on-demand\n", "fea"),
            ("flow:\n  profile:\n    fea:\n      mode: fast\n", "enabled"),
            ("flow:\n  profile: [a, b]\n", "profile"),
            ("flow: [unclosed\n", "cannot read"),
            (b"flow: \xff\xfe\n", "cannot read"),
        ):
            with self.subTest(text=text):
                result = self.setting(text)
                self.assertFalse(result.enabled)
                self.assertIsNotNone(result.invalid)
                self.assertIn(cause, result.invalid)

    def test_an_unreadable_product_is_invalid(self) -> None:
        result = self.setting(None, as_directory=True)
        self.assertFalse(result.enabled)
        self.assertIn("cannot read", result.invalid)

    def test_the_setting_agrees_with_the_cad_record_gate(self) -> None:
        path = SCRIPT.parents[2] / "cad-record" / "scripts" / "flow_profile_gate.py"
        gate_spec = importlib.util.spec_from_file_location("test_gate_agreement", path)
        assert gate_spec is not None and gate_spec.loader is not None
        gate = importlib.util.module_from_spec(gate_spec)
        sys.modules[gate_spec.name] = gate
        gate_spec.loader.exec_module(gate)
        for enabled in (True, False):
            text = f"flow:\n  profile:\n    fea:\n      enabled: {str(enabled).lower()}\n"
            product = __import__("yaml").safe_load(text)
            self.assertEqual(gate.fea_setting(gate.profile_of(product))[0], enabled)
            self.assertEqual(self.setting(text).enabled, enabled)


class WarmWorkerWithoutPyYAMLTests(unittest.TestCase):
    """The detached warm-up runs on the base interpreter, which may have no PyYAML."""

    def test_the_warm_up_runs_without_pyyaml_and_reads_no_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-preflight-noyaml-") as directory:
            root = Path(directory)
            (root / "hw").mkdir()
            (root / "hw" / "product.yaml").write_text("flow:\n  profile:\n    fea:\n      enabled: true\n",
                                                      encoding="utf-8")
            code = ("import runpy, sys; sys.modules['yaml'] = None; "
                    f"sys.argv = ['preflight.py', '--warm']; runpy.run_path({str(SCRIPT)!r}, run_name='__main__')")
            result = subprocess.run(
                [sys.executable, "-B", "-c", code], cwd=root, capture_output=True, text=True, check=False,
                env={"FREECADCMD": str(root / "missing"), "PATH": "", "HOME": directory,
                     "XDG_CACHE_HOME": directory})
        self.assertNotIn("ImportError", result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        self.assertEqual(result.returncode, 1, result.stderr)  # FreeCAD is missing: a report, not a crash
        self.assertIn("FreeCAD", result.stdout)


class WarmStatusPerProjectTests(unittest.TestCase):
    """The warm-up is shared by every project of this plugin copy; solver readiness is the project's."""

    COMPLETE = ("freecad-command", "freecad-version", "techdraw-templates", "uv",
                *(runtime.id for runtime in preflight.RUNTIMES), preflight.CHROMIUM.id, "three-vendor-cache")

    def finished_record(self, directory: str, checks: tuple | None = None) -> dict:
        environ = {"XDG_CACHE_HOME": directory, "HOME": directory}
        checks = checks if checks is not None else tuple(
            preflight.Check(identifier, identifier, "pass", True, "ok", "") for identifier in self.COMPLETE)
        preflight._write_record(preflight.warm_record_path(environ), {
            "state": "finished", "pid": 1, "started_at": "t0", "finished_at": "t1",
            "report": preflight.Report(checks).as_json()})
        return environ

    def status(self, product: str | None, checks: tuple | None = None, record: bool = True) -> tuple[int, str]:
        with tempfile.TemporaryDirectory(prefix="cad-preflight-status-") as directory:
            environ = self.finished_record(directory, checks) if record else {
                "XDG_CACHE_HOME": directory, "HOME": directory}
            project = Path(directory) / "project"
            project.mkdir()
            if product is not None:
                (project / "hw").mkdir()
                (project / "hw" / "product.yaml").write_text(product, encoding="utf-8")
            missing = {**FEASolverCheckTests.PROBED, "calculix": {"error": "ccx not found: $CCX is unset"}}
            command = Path(directory) / "freecadcmd"
            command.write_text("#!/bin/sh\n", encoding="utf-8")
            command.chmod(0o755)
            with patch.object(preflight.fea_solvers, "probe", return_value=missing), patch("sys.stderr"):
                return preflight.warm_status({**environ, "FREECADCMD": str(command), "PATH": ""}, cwd=project)

    def test_a_warm_up_taken_with_fea_off_does_not_pass_a_project_that_enables_fea(self) -> None:
        code, text = self.status("flow:\n  profile:\n    fea:\n      enabled: true\n")
        self.assertEqual(code, 1, text)
        self.assertIn("ccx not found", text)

    def test_the_same_warm_up_passes_a_project_without_fea(self) -> None:
        code, text = self.status(None)
        self.assertEqual(code, 0, text)
        self.assertIn("FEA is not enabled", text)

    def test_an_older_warm_up_that_stopped_at_missing_solvers_is_not_a_pass(self) -> None:
        # Before this change the fea-solvers failure ended the warm-up before uv, the runtimes, Chromium and the
        # vendor cache were checked. Without its failure the record is not a full warm-up.
        old = (*(preflight.Check(i, i, "pass", True, "ok", "") for i in
                 ("freecad-command", "freecad-version", "techdraw-templates")),
               preflight.Check("fea-solvers", "FEA solvers", "fail", True, "ccx not found", "install"))
        code, text = self.status(None, checks=old)
        self.assertEqual(code, preflight.EXIT_NO_WARM, text)
        self.assertIn("incomplete", text)
        self.assertIn("--warm", text)

    def test_a_recorded_required_failure_still_exits_1(self) -> None:
        failed = tuple(preflight.Check(i, i, "fail" if i == "uv" else "pass", True, "d", "fix")
                       for i in self.COMPLETE)
        self.assertEqual(self.status(None, checks=failed)[0], 1)

    def test_an_invalid_profile_is_reported_when_no_warm_up_is_recorded(self) -> None:
        code, text = self.status("flow:\n  profile:\n    fea:\n      enabled: maybe\n", record=False)
        self.assertEqual(code, 1, text)
        self.assertIn("flow.profile.fea", text)
        self.assertIn("No warm-up is recorded", text)

    def test_a_running_warm_up_does_not_hide_an_invalid_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-preflight-running-") as directory:
            environ = {"XDG_CACHE_HOME": directory, "HOME": directory}
            preflight._write_record(preflight.warm_record_path(environ),
                                    {"state": "running", "pid": os.getpid(), "started_at": "t0"})
            project = Path(directory) / "project"
            (project / "hw").mkdir(parents=True)
            (project / "hw" / "product.yaml").write_text("flow: [a]\n", encoding="utf-8")
            code, text = preflight.warm_status(environ, wait_s=0.1, cwd=project)
            self.assertEqual(code, 1, text)
            self.assertIn("flow", text)
            (project / "hw" / "product.yaml").write_text("name: widget\n", encoding="utf-8")
            self.assertEqual(preflight.warm_status(environ, wait_s=0.1, cwd=project)[0], preflight.EXIT_RUNNING)

    def test_a_profile_repaired_between_two_reads_still_reports_the_first_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-preflight-race-") as directory:
            environ = {"XDG_CACHE_HOME": directory, "HOME": directory}
            preflight._write_record(preflight.warm_record_path(environ),
                                    {"state": "running", "pid": os.getpid(), "started_at": "t0"})
            broken = preflight.FeaSetting(False, f"{directory}/hw/product.yaml: flow.profile must be a mapping")
            reads = [broken, preflight.FeaSetting(False, None), preflight.FeaSetting(False, None)]
            with patch.object(preflight, "project_fea", side_effect=reads):
                code, text = preflight.warm_status(environ, wait_s=0.1, cwd=Path(directory))
        self.assertEqual(code, 1, text)
        self.assertIn("flow.profile must be a mapping", text)

    def test_info_is_no_evidence_for_a_mandatory_warm_up_check(self) -> None:
        checks = tuple(preflight.Check(i, i, "info" if i == "uv" else "pass", i != "uv", "d", "")
                       for i in self.COMPLETE)
        code, text = self.status(None, checks=checks)
        self.assertEqual(code, preflight.EXIT_NO_WARM, text)
        self.assertIn("uv", text)

    def test_a_cached_row_with_a_wrong_field_type_is_unreadable(self) -> None:
        good = preflight.Report(tuple(preflight.Check(i, i, "pass", True, "ok", "") for i in self.COMPLETE)).as_json()["checks"]
        for field, value in (("required", "no"), ("required", 0), ("elapsed_s", "1.5"), ("elapsed_s", -1),
                             ("elapsed_s", True), ("detail", 5), ("id", ""), ("extra", 1)):
            rows = [*good[:-1], {**good[-1], field: value}]
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory(prefix="cad-preflight-bad-") as directory:
                environ = {"XDG_CACHE_HOME": directory, "HOME": directory}
                preflight._write_record(preflight.warm_record_path(environ), {
                    "state": "finished", "pid": 1, "started_at": "t0", "finished_at": "t1",
                    "report": {"checks": rows}})
                project = Path(directory) / "project"
                project.mkdir()
                code, text = preflight.warm_status(environ, cwd=project)
                self.assertEqual(code, preflight.EXIT_NO_WARM, text)
                self.assertIn("unreadable", text)

    def test_a_failed_mandatory_check_marked_optional_still_fails_the_status(self) -> None:
        checks = tuple(preflight.Check(i, i, "fail" if i == "uv" else "pass", i != "uv", "d", "fix")
                       for i in self.COMPLETE)
        self.assertEqual(self.status(None, checks=checks)[0], 1)

    def test_a_malformed_record_is_not_a_warm_up(self) -> None:
        good = [asdict_check for asdict_check in preflight.Report(tuple(
            preflight.Check(i, i, "pass", True, "ok", "") for i in self.COMPLETE)).as_json()["checks"]]
        for rows in ([*good[:-1], "uv"], [*good[:-1], {"id": "uv"}], [*good[:-1], {**good[-1], "status": "bogus"}],
                     "not a list"):
            with self.subTest(rows=str(rows)[:40]), tempfile.TemporaryDirectory(prefix="cad-preflight-bad-") as directory:
                environ = {"XDG_CACHE_HOME": directory, "HOME": directory}
                preflight._write_record(preflight.warm_record_path(environ), {
                    "state": "finished", "pid": 1, "started_at": "t0", "finished_at": "t1",
                    "report": {"checks": rows}})
                project = Path(directory) / "project"
                project.mkdir()
                code, text = preflight.warm_status(environ, cwd=project)
                self.assertEqual(code, preflight.EXIT_NO_WARM, text)
                self.assertIn("unreadable", text)

    def test_no_warm_up_and_a_valid_profile_still_exits_2(self) -> None:
        self.assertEqual(self.status(None, record=False)[0], preflight.EXIT_NO_WARM)

    def test_an_invalid_profile_fails_the_status_by_name(self) -> None:
        code, text = self.status("flow:\n  profile:\n    fea:\n      enabled: maybe\n")
        self.assertEqual(code, 1, text)
        self.assertIn("flow.profile.fea", text)


class FEAProfileCheckTests(unittest.TestCase):
    def test_an_invalid_profile_fails_by_name_and_nothing_is_probed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-preflight-fea-") as directory:
            command = Path(directory) / "freecadcmd"
            command.write_text("#!/bin/sh\n", encoding="utf-8")
            command.chmod(0o755)
            invalid = preflight.FeaSetting(False, "/p/hw/product.yaml: flow.profile.fea.enabled must be a boolean")
            with patch.object(preflight, "cad_tool_version", return_value=("1.1.4", "FreeCAD 1.1.4")), \
                    patch.object(preflight.freecad_runtime, "techdraw_templates",
                                 return_value=(directory, frozenset(preflight.EXPECTED_TEMPLATES))), \
                    patch.object(preflight.fea_solvers, "probe") as probed, patch("sys.stderr"):
                checks = preflight._timed_freecad_checks({"FREECADCMD": str(command), "PATH": ""}, fea=invalid)
        probed.assert_not_called()
        named = next(check for check in checks if check.id == "fea-profile")
        self.assertEqual((named.status, named.required), ("fail", True))
        self.assertIn("/p/hw/product.yaml", named.detail)
        self.assertIn("must be a boolean", named.detail)
        self.assertEqual(preflight.Report(tuple(checks)).exit_code, 1)

    def test_the_warm_up_reads_no_project_and_adds_no_fea_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-preflight-fea-") as directory:
            command = Path(directory) / "freecadcmd"
            command.write_text("#!/bin/sh\n", encoding="utf-8")
            command.chmod(0o755)
            with patch.object(preflight, "cad_tool_version", return_value=("1.1.4", "FreeCAD 1.1.4")), \
                    patch.object(preflight.freecad_runtime, "techdraw_templates",
                                 return_value=(directory, frozenset(preflight.EXPECTED_TEMPLATES))), \
                    patch.object(preflight.fea_solvers, "probe") as probed, patch("sys.stderr"):
                checks = preflight._timed_freecad_checks({"FREECADCMD": str(command), "PATH": ""}, fea=None)
        probed.assert_not_called()
        self.assertFalse([check for check in checks if check.id.startswith("fea-")])


if __name__ == "__main__":
    unittest.main()
