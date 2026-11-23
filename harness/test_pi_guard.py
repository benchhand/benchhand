#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Tests for the pi bash guard: only one plain `uv run --script` command may run."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest

GUARD = Path(__file__).resolve().parent / "pi/guard/cad-bash-guard.js"
NODE = shutil.which("node")
DRIVER = (
    "import {isAllowed} from process.argv[1]; const cases = JSON.parse(await new Promise(r => {"
    "let s=''; process.stdin.on('data', d => s+=d).on('end', () => r(s));}));"
    "console.log(JSON.stringify(cases.map(c => isAllowed(c))));"
).replace("from process.argv[1]", "from " + json.dumps(GUARD.as_uri()))

ALLOWED = [
    "uv run --script scripts/run.py check",
    '  uv run --script "$CAD_SKILLS/cad-flow/scripts/run.py" cad-flow/scripts/scratch.py init --hw hw --unit u1 --part P1',
    "uv run --script x.py --note 'a; b | c && d $(e) `f`'",
    'uv run --script x.py --note "a; b | c && d > e"',
    "uv run --script x.py --out .cad-work/u1/out.json",
    "uv  run   --script x.py\t--flag",
]
BLOCKED = [
    ("empty", ""),
    ("blank", "   "),
    ("other command", "ls -la"),
    ("uv without run", "uv pip install requests"),
    ("uv run without --script", "uv run python x.py"),
    ("uv run --script no script", "uv run --script"),
    ("uv run --with before script", "uv run --with x --script y.py"),
    ("and chain", "uv run --script x.py && rm -rf /"),
    ("semicolon", "uv run --script x.py; rm -rf /"),
    ("pipe", "uv run --script x.py | sh"),
    ("or chain", "uv run --script x.py || id"),
    ("background", "uv run --script x.py & id"),
    ("redirect out", "uv run --script x.py > /etc/passwd"),
    ("redirect in", "uv run --script x.py < /etc/passwd"),
    ("subshell", "(uv run --script x.py)"),
    ("command substitution", "uv run --script x.py $(id)"),
    ("command substitution in double quotes", 'uv run --script x.py "$(id)"'),
    ("backticks", "uv run --script x.py `id`"),
    ("backticks in double quotes", 'uv run --script x.py "`id`"'),
    ("parameter expansion with command", 'uv run --script x.py "${x:-$(id)}"'),
    ("newline chain", "uv run --script x.py\nrm -rf /"),
    ("newline inside quotes", "uv run --script x.py 'a\nb'"),
    ("carriage return", "uv run --script x.py\rid"),
    ("env prefix", "FOO=1 uv run --script x.py"),
    ("env command prefix", "env FOO=1 uv run --script x.py"),
    ("leading command", "cd /tmp && uv run --script x.py"),
    ("backslash escape", "uv run --script x.py \\; id"),
    ("unterminated single quote", "uv run --script x.py 'abc"),
    ("unterminated double quote", 'uv run --script x.py "abc'),
    ("process substitution", "uv run --script x.py <(id)"),
    ("heredoc", "uv run --script x.py <<EOF\nid\nEOF"),
    ("not a string", None),
]


def run_guard(cases: list[object]) -> list[bool]:
    assert NODE is not None
    done = subprocess.run(
        [NODE, "--input-type=module", "-e", DRIVER],
        input=json.dumps(cases), capture_output=True, text=True, check=True, timeout=60,
    )
    return json.loads(done.stdout)


@unittest.skipUnless(NODE, "node is needed to run the pi guard")
class BashGuardTests(unittest.TestCase):
    def test_allows_plain_uv_run_script_commands(self) -> None:
        self.assertEqual(run_guard(ALLOWED), [True] * len(ALLOWED))

    def test_blocks_everything_else(self) -> None:
        verdicts = run_guard([command for _name, command in BLOCKED])
        allowed = [name for (name, _command), verdict in zip(BLOCKED, verdicts) if verdict]
        self.assertEqual(allowed, [])


@unittest.skipUnless(NODE, "node is needed to run the pi guard")
class ExtensionHookTests(unittest.TestCase):
    def run_hook(self, event: dict[str, object]) -> object:
        script = (
            f"import guard from {json.dumps(GUARD.as_uri())};"
            "let handler; guard({on: (name, fn) => { if (name === 'tool_call') handler = fn; }});"
            "const event = JSON.parse(process.argv[1]);"
            "console.log(JSON.stringify(await handler(event, {}) ?? null));"
        )
        done = subprocess.run([NODE, "--input-type=module", "-e", script, json.dumps(event)],
                              capture_output=True, text=True, check=True, timeout=60)
        return json.loads(done.stdout)

    def test_blocks_a_disallowed_bash_call_with_a_reason(self) -> None:
        result = self.run_hook({"toolName": "bash", "input": {"command": "rm -rf /"}})
        self.assertEqual(result["block"], True)  # type: ignore[index]
        self.assertIn("uv run --script", result["reason"])  # type: ignore[index]

    def test_lets_an_allowed_bash_call_through(self) -> None:
        self.assertIsNone(self.run_hook({"toolName": "bash", "input": {"command": "uv run --script x.py"}}))

    def test_blocks_a_bash_call_without_a_command(self) -> None:
        self.assertEqual(self.run_hook({"toolName": "bash", "input": {}})["block"], True)  # type: ignore[index]

    def test_ignores_other_tools(self) -> None:
        self.assertIsNone(self.run_hook({"toolName": "read", "input": {"path": "a"}}))


if __name__ == "__main__":
    unittest.main()
