"""Guard: CI installs its test pins from the requirements files, and every pin is used.

A `--with <pkg>==<ver>` in ci.yml is a second copy of a pin, and dev run scripts drift from it.
The only pins that may stay inline belong to a tool that runs in its own pinned environment.

Three files exist on purpose. `harness/test-requirements-min.txt` (PyYAML only) serves the steps that
run host scripts declaring PyYAML or nothing (`install.py`, `generate.py`, `docs_only.py`,
`ci_shards.py`): extra packages on the path there could hide a missing declaration.
`harness/test-requirements-freecad.txt` (PyYAML and jsonschema) serves the FreeCAD Python 3.11
import check. `harness/test-requirements.txt` serves the skill tests. A package pinned in more
than one file has the same version in each.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
FULL = "harness/test-requirements.txt"
MIN = "harness/test-requirements-min.txt"
FREECAD = "harness/test-requirements-freecad.txt"
REQUIREMENT_FILES = (FULL, MIN, FREECAD)
# Tools that run in their own pinned environment keep their pin inline. The set is empty: ruff,
# actionlint and typecheck.py take their pins from `uvx`, a download or a --script lock.
TOOL_PINS: frozenset[str] = frozenset()
# The importable module of a pin whose distribution name differs from it.
MODULES = {"pyyaml": "yaml"}
VERSION_OPERATOR = re.compile(r"[<>=!~]")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2$", re.M | re.S)
IMPORT = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", re.M)


def run_commands(workflow_text: str) -> list[str]:
    jobs = yaml.safe_load(workflow_text)["jobs"]
    return [
        step["run"]
        for job in jobs.values()
        for step in job.get("steps", [])
        if "run" in step
    ]


def tokens(command: str) -> list[str]:
    """The shell words of one `run` block: continuations joined, heredoc bodies dropped."""
    joined = re.sub(r"\\\n\s*", " ", command)
    return shlex.split(HEREDOC.sub("", joined), comments=True)


def option_values(command: str, *names: str) -> list[str]:
    """Every argument of the given long or short options: `--x v`, `--x=v` and `-x v` alike."""
    words = tokens(command)
    values = []
    for index, word in enumerate(words):
        for name in names:
            if word == name and index + 1 < len(words):
                values.append(words[index + 1])
            elif name.startswith("--") and word.startswith(name + "="):
                values.append(word.removeprefix(name + "="))
            elif not name.startswith("--") and word.startswith(name) and len(word) > len(name):
                # an attached short option: `-wpkg==1` or `-w=pkg==1`
                values.append(word[len(name) :].removeprefix("="))
    return values


def package_name(spec: str) -> str:
    return re.split(r"[<>=!~\[; ]", spec, maxsplit=1)[0].lower()


def inline_pins(workflow_text: str) -> list[str]:
    """Version-constrained `--with`/`-w` arguments declared in a `run` command."""
    found = []
    for command in run_commands(workflow_text):
        for spec in option_values(command, "--with", "-w"):
            if VERSION_OPERATOR.search(spec) and package_name(spec) not in TOOL_PINS:
                found.append(spec)
    return found


def requirement_files_used(workflow_text: str) -> list[str]:
    return [
        value
        for command in run_commands(workflow_text)
        for value in option_values(command, "--with-requirements")
    ]


def pins(requirements_text: str) -> dict[str, str]:
    result = {}
    for line in requirements_text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, _, version = line.partition("==")
            result[name.strip().lower()] = version.strip()
    return result


def unpinned_lines(requirements_text: str) -> list[str]:
    lines = (line.split("#", 1)[0].strip() for line in requirements_text.splitlines())
    return [
        line
        for line in lines
        if line and not re.fullmatch(r"[A-Za-z0-9_.\-]+==[^\s=]+", line)
    ]


def imported_modules(folders: tuple[str, ...]) -> set[str]:
    """Top-level modules imported by any tracked .py file under the folders."""
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--", *(f"{folder}/*.py" for folder in folders)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    modules: set[str] = set()
    for name in filter(None, listing.split("\0")):
        modules.update(IMPORT.findall((ROOT / name).read_text(encoding="utf-8")))
    return modules


def unused_pins(requirements_text: str, imported: set[str]) -> list[str]:
    return [
        name
        for name in pins(requirements_text)
        if MODULES.get(name, name.replace("-", "_")) not in imported
    ]


def workflow(run: str) -> str:
    return "jobs:\n  a:\n    steps:\n      - run: |\n" + "".join(
        f"          {line}\n" for line in run.splitlines()
    )


class InlinePinTests(unittest.TestCase):
    CASES = {
        "plain": "uv run --with pyyaml==6.0.3 python x",
        "double quoted": 'uv run --with "pyyaml==6.0.3" python x',
        "single quoted": "uv run --with 'pyyaml==6.0.3' python x",
        "equals form": "uv run --with=pyyaml==6.0.3 python x",
        "short flag": "uv run -w pyyaml==6.0.3 python x",
        "continuation": "uv run --with \\\n  pyyaml==6.0.3 python x",
        "newline between": "uv run --with\npyyaml==6.0.3 python x",
        "attached short": "uv run -wpyyaml==6.0.3 python x",
        "attached short equals": "uv run -w=pyyaml==6.0.3 python x",
        "attached short quoted": "uv run -w'pyyaml==6.0.3' python x",
        "specifier": "uv run --with 'pyyaml>=6' python x",
    }

    def test_every_form_of_an_inline_pin_is_reported(self) -> None:
        for label, command in self.CASES.items():
            with self.subTest(label):
                found = inline_pins(workflow(command))
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].startswith("pyyaml"), found)

    def test_every_pin_of_a_multi_pin_command_is_reported(self) -> None:
        text = workflow("uv run --with a==1 \\\n  --with b==2 python x")
        self.assertEqual(inline_pins(text), ["a==1", "b==2"])

    def test_a_heredoc_with_quotes_does_not_break_the_scan(self) -> None:
        text = workflow("python3 - <<'PY'\nprint(\"it's\")\nPY\nuv run --with a==1 python x")
        self.assertEqual(inline_pins(text), ["a==1"])

    def test_a_requirements_file_is_not_an_inline_pin(self) -> None:
        for command in (
            f"uv run --with-requirements {MIN} python x",
            f"uv run --with-requirements={MIN} python x",
        ):
            with self.subTest(command):
                text = workflow(command)
                self.assertEqual(inline_pins(text), [])
                self.assertEqual(requirement_files_used(text), [MIN])

    def test_an_unpinned_with_is_not_a_test_pin(self) -> None:
        self.assertEqual(inline_pins(workflow("uv run --with rich python x")), [])


class RequirementFileTests(unittest.TestCase):
    def test_a_pin_nothing_imports_is_unused(self) -> None:
        self.assertEqual(unused_pins("pyyaml==6.0.3\nghost==1\n", {"yaml"}), ["ghost"])

    def test_a_line_without_an_exact_pin_is_reported(self) -> None:
        self.assertEqual(unpinned_lines("a==1  # why\nb>=2\nc\n"), ["b>=2", "c"])

    def test_pins_ignore_comments_and_case(self) -> None:
        self.assertEqual(pins("# head\nPyYAML==6.0.3  # why\n"), {"pyyaml": "6.0.3"})


class RepositoryTests(unittest.TestCase):
    workflow_text: str
    full: str
    minimal: str
    freecad: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_text = WORKFLOW.read_text(encoding="utf-8")
        cls.full = (ROOT / FULL).read_text(encoding="utf-8")
        cls.minimal = (ROOT / MIN).read_text(encoding="utf-8")
        cls.freecad = (ROOT / FREECAD).read_text(encoding="utf-8")

    def test_ci_declares_no_inline_test_pin(self) -> None:
        self.assertEqual(inline_pins(self.workflow_text), [])

    def test_ci_uses_only_the_requirement_files_and_each_of_them(self) -> None:
        used = requirement_files_used(self.workflow_text)
        self.assertEqual(set(used), set(REQUIREMENT_FILES), used)

    def test_every_requirement_is_an_exact_pin(self) -> None:
        self.assertEqual(unpinned_lines(self.full), [])
        self.assertEqual(unpinned_lines(self.minimal), [])
        self.assertEqual(unpinned_lines(self.freecad), [])

    def test_the_minimal_file_is_pyyaml_alone(self) -> None:
        self.assertEqual(list(pins(self.minimal)), ["pyyaml"])

    def test_the_freecad_file_is_pyyaml_and_jsonschema_alone(self) -> None:
        self.assertEqual(sorted(pins(self.freecad)), ["jsonschema", "pyyaml"])

    def test_a_package_pinned_in_two_files_has_one_version(self) -> None:
        for subset in (self.minimal, self.freecad):
            for name, version in pins(subset).items():
                self.assertEqual(version, pins(self.full)[name], name)

    def test_every_pin_is_imported_in_the_code_its_file_serves(self) -> None:
        self.assertEqual(
            unused_pins(self.full, imported_modules(("skills", "harness", "scripts"))),
            [],
        )
        self.assertEqual(
            unused_pins(self.minimal, imported_modules(("harness", "scripts"))), []
        )
        self.assertEqual(unused_pins(self.freecad, imported_modules(("skills",))), [])


if __name__ == "__main__":
    unittest.main()
