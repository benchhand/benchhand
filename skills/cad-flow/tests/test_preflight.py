#!/usr/bin/env python3
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
            (project / ".cad-agent-install.json").write_text(json.dumps({"format": 1, "files": {
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
            (project / ".cad-agent-install.json").write_text(json.dumps({"format": 1, "files": {
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
        return preflight.Report((preflight.Check("cadquery", "cadquery", status, True, "d", "fix it"),)).as_json()

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

class BeadsPreflightTests(unittest.TestCase):
    """The bd and beads-hooks checks, run through `run_checks` with fake `bd`/`git` executables on PATH."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.project = self.home / "project"
        self.project.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.git_dir = str(Path(shutil.which("git") or "/usr/bin/git").parent)
        self.global_config = self.root / "global.gitconfig"
        self.system_config = self.root / "system.gitconfig"
        self.global_config.write_text("", encoding="utf-8")
        self.system_config.write_text("", encoding="utf-8")
        self.pin_global_config = True

    def environ(self, real_git: bool = True) -> dict[str, str]:
        path = os.pathsep.join([str(self.bin), *([self.git_dir] if real_git else [])])
        environ = {"PATH": path, "HOME": str(self.home), "GIT_CONFIG_SYSTEM": str(self.system_config)}
        if self.pin_global_config:
            environ["GIT_CONFIG_GLOBAL"] = str(self.global_config)
        return environ

    def script(self, name: str, body: str, shebang: str = "#!/bin/sh") -> None:
        path = self.bin / name
        path.write_text(f"{shebang}\n{body}\n", encoding="utf-8")
        path.chmod(0o755)

    def fake_bd(self, version_out: str = "bd version 1.3.1 (c1c4b64)", version_rc: int = 0, where_rc: int = 0,
                hang_on: str = "") -> None:
        sleep = "/bin/sleep 30;"
        where_out = 'echo "Error: no beads database found" >&2;' if where_rc else "echo ok;"
        self.script("bd", "\n".join([
            'case "$1" in',
            f'  --version) {sleep if hang_on == "version" else ""} printf %s "$FAKE_VERSION"; exit {version_rc};;',
            f'  where) {sleep if hang_on == "where" else ""} {where_out} exit {where_rc};;',
            "esac"]))
        self.version_out = version_out

    def probe(self, real_git: bool = True) -> tuple[dict[str, "preflight.Check"], int]:
        """Both checks through run_checks, and the exit code of a report holding just those two."""
        environ = self.environ(real_git)
        environ["FAKE_VERSION"] = getattr(self, "version_out", "")
        passing = preflight.Check("freecad-command", "freecad-command", "pass", True, "", "")
        with patch("sys.stderr"):
            report = preflight.run_checks(environ, self.project, self.home, "codex", cad_checks=[passing])
        found = {check.id: check for check in report.checks}
        return found, preflight.Report((found["bd"], found["beads-hooks"])).exit_code

    def beads(self, present: bool = True) -> None:
        if present:
            (self.project / ".beads").mkdir(exist_ok=True)
        else:
            shutil.rmtree(self.project / ".beads", ignore_errors=True)

    def git_init(self, hooks_path: str | None = None, config: str = "--local") -> None:
        subprocess.run(["git", "init", "-q", str(self.project)], check=True, env=self.environ())
        if hooks_path is not None:
            subprocess.run(["git", "config", config, "core.hooksPath", hooks_path], cwd=self.project, check=True,
                           env=self.environ())

    def test_run_checks_reports_both_after_uv_and_before_the_runtimes(self) -> None:
        passing = preflight.Check("freecad-command", "freecad-command", "pass", True, "", "")
        with patch("sys.stderr"):
            report = preflight.run_checks(self.environ(), self.project, self.home, "codex", cad_checks=[passing])
        ids = [check.id for check in report.checks]
        self.assertEqual(ids[:4], ["freecad-command", "uv", "bd", "beads-hooks"])
        self.assertEqual(ids[4], "cadquery")

    def test_without_bd_and_without_beads_the_file_ledger_is_an_optional_warning(self) -> None:
        checks, exit_code = self.probe()
        bd = checks["bd"]
        self.assertEqual((bd.status, bd.required, exit_code), ("warn", False, 0))
        self.assertIn("file ledger", bd.detail)

    def test_beads_without_bd_fails(self) -> None:
        self.beads()
        checks, exit_code = self.probe()
        self.assertEqual((checks["bd"].status, checks["bd"].required, exit_code), ("fail", True, 1))
        self.assertIn("Install bd 1.3.1", checks["bd"].fix)

    def test_bd_versions_follow_semver_order_against_1_3_1(self) -> None:
        self.beads()
        for version, status in (("1.2.9", "fail"), ("1.3.0", "fail"), ("1.3.1-rc.1", "fail"),
                                ("1.3.1-alpha", "fail"), ("1.3.1", "pass"), ("1.3.1+build.5", "pass"),
                                ("1.3.2-rc.1", "pass"), ("1.4.0", "pass"), ("2.0.0", "pass")):
            with self.subTest(version):
                self.fake_bd(f"bd version {version} (abc1234)")
                checks, exit_code = self.probe()
                self.assertEqual((checks["bd"].status, checks["bd"].required, exit_code),
                                 (status, True, 0 if status == "pass" else 1))
                if status == "fail":
                    self.assertIn(f"bd {version} is older than 1.3.1", checks["bd"].detail)

    def test_unusable_version_output_fails_when_beads_exists_and_only_warns_without_it(self) -> None:
        outputs = {"blank": "", "garbage": "command not found", "no version": "bd version", "two parts": "bd version 1.3",
                   "wrong word": "version 1.3.1", "trailing text": "bd version 1.3.1 (abc) extra",
                   "two lines": "bd version 1.3.1 (abc)\nbd version 1.2.0 (def)", "v prefix": "bd version v1.3.1 (abc)"}
        for with_beads in (True, False):
            for label, output in outputs.items():
                with self.subTest(beads=with_beads, output=label):
                    self.beads(with_beads)
                    self.fake_bd(output)
                    checks, exit_code = self.probe()
                    expected = ("fail", True, 1) if with_beads else ("warn", False, 0)
                    self.assertEqual((checks["bd"].status, checks["bd"].required, exit_code), expected)
                    self.assertIn("Unrecognised `bd --version` output", checks["bd"].detail)

    def test_a_version_probe_that_exits_non_zero_never_counts_as_a_version(self) -> None:
        for with_beads in (True, False):
            with self.subTest(beads=with_beads):
                self.beads(with_beads)
                self.fake_bd("bd version 9.9.9 (abc)", version_rc=3)
                checks, exit_code = self.probe()
                expected = ("fail", True, 1) if with_beads else ("warn", False, 0)
                self.assertEqual((checks["bd"].status, checks["bd"].required, exit_code), expected)
                self.assertIn("exited 3", checks["bd"].detail)

    def test_version_probe_exceptions_fail_with_beads_and_warn_without(self) -> None:
        cases = {"cannot launch": lambda: self.script("bd", "", shebang="#!/nonexistent/interpreter"),
                 "times out": lambda: self.fake_bd(hang_on="version")}
        for with_beads in (True, False):
            for label, install in cases.items():
                with self.subTest(beads=with_beads, case=label):
                    self.beads(with_beads)
                    install()
                    with patch.object(preflight, "PROBE_TIMEOUT_S", 0.5):
                        checks, exit_code = self.probe()
                    expected = ("fail", True, 1) if with_beads else ("warn", False, 0)
                    self.assertEqual((checks["bd"].status, checks["bd"].required, exit_code), expected)

    def test_a_store_that_bd_where_cannot_find_fails(self) -> None:
        self.beads()
        self.fake_bd(where_rc=1)
        checks, exit_code = self.probe()
        self.assertEqual((checks["bd"].status, checks["bd"].required, exit_code), ("fail", True, 1))
        self.assertIn("`bd where` failed: Error: no beads database found", checks["bd"].detail)

    def test_a_bd_where_that_hangs_fails_instead_of_hanging_preflight(self) -> None:
        self.beads()
        self.fake_bd(hang_on="where")
        with patch.object(preflight, "BD_WHERE_TIMEOUT_S", 0.5):
            checks, exit_code = self.probe()
        self.assertEqual((checks["bd"].status, checks["bd"].required, exit_code), ("fail", True, 1))
        self.assertIn("`bd where` did not run", checks["bd"].detail)

    def test_bd_without_beads_uses_the_file_ledger_and_passes(self) -> None:
        self.fake_bd()
        checks, exit_code = self.probe()
        self.assertEqual((checks["bd"].status, exit_code), ("pass", 0))
        self.assertIn("file ledger", checks["bd"].detail)

    def test_hooks_path_pointing_at_beads_hooks_fails_for_relative_absolute_and_tilde_values(self) -> None:
        self.git_init()
        for value in (".beads/hooks", "./.beads/hooks/", "../project/.beads/hooks", str(self.project / ".beads" / "hooks"),
                      "~/project/.beads/hooks"):
            with self.subTest(value):
                subprocess.run(["git", "config", "core.hooksPath", value], cwd=self.project, check=True,
                               env=self.environ())
                checks, exit_code = self.probe()
                hooks = checks["beads-hooks"]
                self.assertEqual((hooks.status, hooks.required, exit_code), ("fail", True, 1))
                self.assertIn("local scope", hooks.detail)
                self.assertIn(f"`git config --file {self.project / '.git' / 'config'} --unset core.hooksPath`", hooks.fix)
                self.assertIn("delete `.beads/hooks/`", hooks.fix)
                self.assertIn(".gitignore", hooks.fix)

    def test_other_hooks_paths_unset_values_and_non_repositories_pass(self) -> None:
        self.assertEqual(self.probe()[0]["beads-hooks"].status, "pass")  # not a repository, nothing in global config
        self.git_init()
        self.assertEqual(self.probe()[0]["beads-hooks"].status, "pass")  # key absent
        for value in (".githooks", str(self.root / "elsewhere" / ".beads" / "hooks"), "~/other/.beads/hooks"):
            with self.subTest(value):
                subprocess.run(["git", "config", "core.hooksPath", value], cwd=self.project, check=True,
                               env=self.environ())
                checks, exit_code = self.probe()
                hooks = checks["beads-hooks"]
                self.assertEqual((hooks.status, exit_code), ("pass", 0))

    def test_a_global_or_system_hooks_path_fails_with_an_unset_for_that_scope(self) -> None:
        target = str(self.project / ".beads" / "hooks")
        for scope, config in (("global", self.global_config), ("system", self.system_config)):
            with self.subTest(scope):
                self.git_init()
                config.write_text(f"[core]\n\thooksPath = {target}\n", encoding="utf-8")
                checks, exit_code = self.probe()
                hooks = checks["beads-hooks"]
                self.assertEqual((hooks.status, hooks.required, exit_code), ("fail", True, 1))
                self.assertIn(f"{scope} scope", hooks.detail)
                self.assertIn(str(config), hooks.detail)
                self.assertIn(f"git config --file {config} --unset core.hooksPath", hooks.fix)
                config.write_text("", encoding="utf-8")

    def test_an_included_hooks_path_names_the_included_file_in_the_fix(self) -> None:
        self.git_init()
        included = self.root / "shared.gitconfig"
        included.write_text(f"[core]\n\thooksPath = {self.project / '.beads' / 'hooks'}\n", encoding="utf-8")
        subprocess.run(["git", "config", "include.path", str(included)], cwd=self.project, check=True, env=self.environ())
        hooks = self.probe()[0]["beads-hooks"]
        self.assertEqual(hooks.status, "fail")
        self.assertIn(f"git config --file {included} --unset core.hooksPath", hooks.fix)
        self.assertNotIn("git config --unset", hooks.fix)

    def test_an_xdg_global_hooks_path_names_the_xdg_file_not_dot_gitconfig(self) -> None:
        self.git_init()
        self.pin_global_config = False
        xdg = self.home / ".config" / "git" / "config"
        xdg.parent.mkdir(parents=True)
        xdg.write_text(f"[core]\n\thooksPath = {self.project / '.beads' / 'hooks'}\n", encoding="utf-8")
        (self.home / ".gitconfig").write_text("[user]\n\tname = Someone\n", encoding="utf-8")
        hooks = self.probe()[0]["beads-hooks"]
        self.assertEqual(hooks.status, "fail")
        self.assertIn("global scope", hooks.detail)
        self.assertIn(f"git config --file {xdg} --unset core.hooksPath", hooks.fix)
        self.assertNotIn("--global", hooks.fix)

    def test_a_hooks_path_from_the_command_line_has_no_file_to_edit(self) -> None:
        self.git_init()
        environ = self.environ()
        environ.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
                        "GIT_CONFIG_VALUE_0": str(self.project / ".beads" / "hooks")})
        with patch.object(self, "environ", return_value=environ):
            hooks = self.probe()[0]["beads-hooks"]
        self.assertEqual(hooks.status, "fail")
        self.assertIn("command scope", hooks.detail)
        self.assertNotIn("git config --file", hooks.fix)
        self.assertIn("environment or command line", hooks.fix)

    def test_a_git_probe_failure_is_a_required_failure_not_a_pass(self) -> None:
        cases = {"exit 128": lambda: self.script("git", 'echo "fatal: bad config line 3" >&2; exit 128'),
                 "cannot launch": lambda: self.script("git", "", shebang="#!/nonexistent/interpreter"),
                 "times out": lambda: self.script("git", "/bin/sleep 30"),
                 "no git on PATH": lambda: None}
        for label, install in cases.items():
            with self.subTest(label):
                install()
                with patch.object(preflight, "PROBE_TIMEOUT_S", 0.5):
                    checks, exit_code = self.probe(real_git=False)
                hooks = checks["beads-hooks"]
                self.assertEqual((hooks.status, hooks.required, exit_code), ("fail", True, 1))
                self.assertNotIn("does not point", hooks.detail)
                (self.bin / "git").unlink(missing_ok=True)

    def test_git_exit_code_1_means_the_key_is_absent(self) -> None:
        self.script("git", "exit 1")
        checks, exit_code = self.probe(real_git=False)
        self.assertEqual((checks["beads-hooks"].status, exit_code), ("pass", 0))


if __name__ == "__main__":
    unittest.main()
