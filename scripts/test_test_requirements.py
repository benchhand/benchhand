"""Guard: CI installs its test pins from harness/test-requirements.txt, and every pin is used.

A `--with <pkg>==<ver>` in ci.yml is a second copy of a pin, and dev run scripts drift from it.
The only pins that may stay inline belong to a tool that runs in its own pinned environment.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
REQUIREMENTS = ROOT / "harness" / "test-requirements.txt"
REQUIREMENTS_ARG = "--with-requirements harness/test-requirements.txt"
# Tools that run in their own pinned environment keep their pin inline. None does today
# (ruff, actionlint and typecheck.py are pinned by `uvx`, a download or a --script lock).
TOOL_PINS: frozenset[str] = frozenset()
# The importable module of a pin whose distribution name differs from it.
MODULES = {"pyyaml": "yaml"}
INLINE_PIN = re.compile(r"--with[ =]+([A-Za-z0-9_.\-]+)==(\S+)")


def run_commands(workflow_text: str) -> list[str]:
    jobs = yaml.safe_load(workflow_text)["jobs"]
    return [
        step["run"]
        for job in jobs.values()
        for step in job.get("steps", [])
        if "run" in step
    ]


def inline_pins(workflow_text: str) -> list[str]:
    """Pins declared in a `run` command instead of the requirements file."""
    found = []
    for command in run_commands(workflow_text):
        for name, version in INLINE_PIN.findall(command):
            if name.lower() not in TOOL_PINS:
                found.append(f"{name}=={version}")
    return found


def uses_requirements(workflow_text: str) -> bool:
    return any(REQUIREMENTS_ARG in command for command in run_commands(workflow_text))


def pin_names(requirements_text: str) -> list[str]:
    names = []
    for line in requirements_text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(re.split(r"==", line)[0].strip().lower())
    return names


def unpinned_lines(requirements_text: str) -> list[str]:
    lines = (line.split("#", 1)[0].strip() for line in requirements_text.splitlines())
    return [line for line in lines if line and not re.fullmatch(r"[A-Za-z0-9_.\-]+==[^\s=]+", line)]


def source_texts() -> list[str]:
    paths = [
        path
        for folder in ("skills", "harness", "scripts")
        for path in (ROOT / folder).rglob("*.py")
    ]
    return [path.read_text(encoding="utf-8") for path in paths]


def unused_pins(requirements_text: str, sources: list[str]) -> list[str]:
    unused = []
    for name in pin_names(requirements_text):
        module = MODULES.get(name, name.replace("-", "_"))
        imported = re.compile(rf"^\s*(?:import|from)\s+{re.escape(module)}\b", re.M)
        if not any(imported.search(text) for text in sources):
            unused.append(name)
    return unused


class SyntheticWorkflowTests(unittest.TestCase):
    PINNED = "jobs:\n  a:\n    steps:\n      - run: uv run --no-project --with pyyaml==6.0.3 python -B t.py\n"
    SOURCED = f"jobs:\n  a:\n    steps:\n      - run: uv run --no-project {REQUIREMENTS_ARG} python -B t.py\n"

    def test_an_inline_pin_is_reported(self) -> None:
        self.assertEqual(inline_pins(self.PINNED), ["pyyaml==6.0.3"])

    def test_every_pin_of_a_multi_pin_command_is_reported(self) -> None:
        text = "jobs:\n  a:\n    steps:\n      - run: |\n          uv run --with a==1 \\\n            --with b==2 python x\n"
        self.assertEqual(inline_pins(text), ["a==1", "b==2"])

    def test_the_requirements_file_is_not_an_inline_pin(self) -> None:
        self.assertEqual(inline_pins(self.SOURCED), [])
        self.assertTrue(uses_requirements(self.SOURCED))

    def test_an_unpinned_with_is_not_a_test_pin(self) -> None:
        text = "jobs:\n  a:\n    steps:\n      - run: uv run --with rich python x\n"
        self.assertEqual(inline_pins(text), [])

    def test_a_pin_nothing_imports_is_unused(self) -> None:
        self.assertEqual(
            unused_pins("pyyaml==6.0.3\nghost==1\n", ["import yaml\n"]), ["ghost"]
        )

    def test_a_line_without_an_exact_pin_is_reported(self) -> None:
        self.assertEqual(unpinned_lines("a==1  # why\nb>=2\nc\n"), ["b>=2", "c"])


class RepositoryTests(unittest.TestCase):
    def test_ci_declares_no_inline_test_pin(self) -> None:
        self.assertEqual(inline_pins(WORKFLOW.read_text(encoding="utf-8")), [])

    def test_ci_installs_from_the_requirements_file(self) -> None:
        self.assertTrue(uses_requirements(WORKFLOW.read_text(encoding="utf-8")))

    def test_every_requirement_is_an_exact_pin(self) -> None:
        self.assertEqual(unpinned_lines(REQUIREMENTS.read_text(encoding="utf-8")), [])

    def test_every_requirement_is_imported_somewhere(self) -> None:
        self.assertEqual(
            unused_pins(REQUIREMENTS.read_text(encoding="utf-8"), source_texts()), []
        )


if __name__ == "__main__":
    unittest.main()
