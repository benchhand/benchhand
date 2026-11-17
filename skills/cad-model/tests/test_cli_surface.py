#!/usr/bin/env python3
"""The cad_model.py command line: how it refuses bad arguments and where its options reach the output.

Run: python3 -m unittest discover -s skills/cad-model/tests -p test_cli_surface.py -v
Needs `uv`. None of these commands builds a part, so none waits on a kernel environment.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().parent.parent / "scripts" / "cad_model.py"
COMMANDS = ("run", "check", "diff", "latest", "store")


@unittest.skipUnless(shutil.which("uv"), "uv is required to start the script")
class CliSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-cli-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "hw").mkdir()

    def cli(self, *args: str, output: str = "json") -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "CAD_OUTPUT": output}
        return subprocess.run(["uv", "run", "--quiet", "--script", str(CLI), *args], capture_output=True,
                              text=True, check=False, cwd=self.root, env=env)

    def test_help_exits_zero_for_the_tool_and_every_command(self) -> None:
        for args in ((), *((command,) for command in COMMANDS)):
            with self.subTest(command=args):
                proc = self.cli(*args, "--help")
                self.assertEqual((proc.returncode, "Usage" in proc.stdout or "usage" in proc.stdout),
                                 (0, True), proc.stderr[-1000:])

    def test_no_command_and_an_unknown_command_are_usage_errors(self) -> None:
        for args in ((), ("frobnicate",)):
            with self.subTest(args=args):
                proc = self.cli(*args)
                self.assertEqual((proc.returncode, proc.stdout), (2, ""), proc.stderr[-1000:])

    def test_a_missing_required_argument_or_option_is_a_usage_error(self) -> None:
        for args in (("run", "hw"), ("latest", "--label", "v1"), ("check",), ("diff", "v1"), ("store", "hw")):
            with self.subTest(args=args):
                self.assertEqual(self.cli(*args).returncode, 2)

    def test_a_value_outside_its_choices_is_a_usage_error(self) -> None:
        for args in (("run", "hw", "--label", "v1", "--kernel", "freecad"), ("store", "purge", "hw")):
            with self.subTest(args=args):
                self.assertEqual(self.cli(*args).returncode, 2)

    def test_a_malformed_option_value_is_a_usage_error(self) -> None:
        for flag, value in (("--pair", "TST-0001"), ("--pair", "TST-0001:TST-0001"), ("--expect-solids", "TST-0001"),
                            ("--allow-interference", "nonsense"), ("--jobs", "0"), ("--jobs", "many")):
            with self.subTest(flag=flag, value=value):
                proc = self.cli("check", "v1", "--hw", "hw", flag, value)
                self.assertEqual((proc.returncode, proc.stdout), (2, ""), proc.stderr[-1000:])

    def test_options_that_exclude_each_other_or_need_another_are_usage_errors(self) -> None:
        for extra in (("--pair", "TST-0001:TST-0002", "--changed-only"), ("--cache", "c", "--no-cache"),
                      ("--since-version", "v1")):
            with self.subTest(extra=extra):
                proc = self.cli("check", "v2", "--hw", "hw", *extra)
                self.assertEqual((proc.returncode, proc.stdout), (2, ""), proc.stderr[-1000:])

    def test_json_prints_the_full_result_whatever_cad_output_says(self) -> None:
        args = ("store", "gc", "hw", "--dry-run")
        full = self.cli(*args, output="json")
        compact = self.cli(*args, output="compact")
        forced = self.cli(*args, "--json", output="compact")
        self.assertEqual((full.returncode, compact.returncode, forced.returncode), (0, 0, 0), forced.stderr[-1000:])
        self.assertEqual(json.loads(forced.stdout), json.loads(full.stdout))
        self.assertEqual(json.loads(full.stdout)["payload"]["dry_run"], True)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(compact.stdout)


if __name__ == "__main__":
    unittest.main()
