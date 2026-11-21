# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""A cad-record script's PEP 723 dependencies cover every third-party package its code imports.

The required set comes from the code, not from a copy of the list: the test parses each script with
`ast`, follows the sibling scripts it loads by path (a string constant naming a `.py` file beside it),
and maps each import name to its distribution. Imports under `if TYPE_CHECKING:` never run, so they
are not required.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
SCRIPTS = SKILLS / "cad-record" / "scripts"
BLOCK = re.compile(r"^# /// script\n((?:#.*\n)+?)# ///$", re.MULTILINE)
DISTRIBUTION = {"yaml": "pyyaml"}


def declared_dependencies(source: str) -> set[str] | None:
    """Normalized distribution names from the PEP 723 block, or None when the file has no block."""
    block = BLOCK.search(source)
    if block is None:
        return None
    toml = "\n".join(line[1:].lstrip(" ") if line != "#" else "" for line in block.group(1).splitlines())
    names = tomllib.loads(toml).get("dependencies", [])
    return {re.split(r"[<>=!~\[; ]", spec, maxsplit=1)[0].lower().replace("_", "-") for spec in names}


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _walk_runtime(node: ast.AST):
    """Every node that can run, skipping `if TYPE_CHECKING:` bodies."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(node, ast.If) and _is_type_checking(node.test) and child in node.body:
            continue
        yield from _walk_runtime(child)


def imported_distributions(source: str, siblings: set[str]) -> set[str]:
    """Distributions of the third-party modules imported at top level or inside a function."""
    found: set[str] = set()
    for node in _walk_runtime(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots = {node.module.split(".")[0]}
        else:
            continue
        for root in roots:
            if root not in sys.stdlib_module_names and root not in siblings and root != "__future__":
                found.add(DISTRIBUTION.get(root, root).lower().replace("_", "-"))
    return found


def loaded_siblings(source: str, available: set[str]) -> set[str]:
    """Sibling script filenames the source names as string constants, so it may load them by path."""
    return {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in available
    }


def required_distributions(script: str, files: dict[str, str], local: set[str]) -> set[str]:
    """Distributions the script and every sibling it loads by path (transitively) import.

    `local` names the repository's own script modules, which are not distributions."""
    seen: set[str] = set()
    pending = [script]
    required: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        required |= imported_distributions(files[name], local)
        pending.extend(loaded_siblings(files[name], set(files)))
    return required


def missing_dependencies(files: dict[str, str], local: set[str] | None = None) -> list[str]:
    local = {name.removesuffix(".py") for name in files} | (local or set())
    problems = []
    for name, source in sorted(files.items()):
        declared = declared_dependencies(source)
        if declared is None:
            continue
        missing = required_distributions(name, files, local) - declared
        if missing:
            problems.append(f"{name}: imports {', '.join(sorted(missing))} but does not declare it")
    return problems


def header(*dependencies: str) -> str:
    listed = ", ".join(f'"{d}"' for d in dependencies)
    return f"# /// script\n# dependencies = [{listed}]\n# ///\n"


class DerivedRequirements(unittest.TestCase):
    def test_reads_declared_names_without_version_specifiers(self) -> None:
        self.assertEqual(declared_dependencies(header("a>=1", "B_c==2", "d[x]>=1")), {"a", "b-c", "d"})

    def test_a_file_without_a_block_declares_nothing_to_check(self) -> None:
        self.assertIsNone(declared_dependencies("import yaml\n"))

    def test_top_level_and_function_level_imports_are_required_and_yaml_maps_to_pyyaml(self) -> None:
        source = "import yaml\nimport os\nimport sib\ndef f():\n    from referencing.jsonschema import X\n"
        self.assertEqual(imported_distributions(source, {"sib"}), {"pyyaml", "referencing"})

    def test_type_checking_imports_are_not_required(self) -> None:
        source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import jsonschema\n"
        self.assertEqual(imported_distributions(source, set()), set())

    def test_a_sibling_loaded_by_path_contributes_its_imports(self) -> None:
        files = {"a.py": header() + 'name = "b.py"\n', "b.py": "def f():\n    import yaml\n"}
        self.assertEqual(missing_dependencies(files), ["a.py: imports pyyaml but does not declare it"])

    def test_a_declared_superset_passes(self) -> None:
        files = {"a.py": header("pyyaml", "extra") + "import yaml\n"}
        self.assertEqual(missing_dependencies(files), [])


class CadRecordScriptDependencies(unittest.TestCase):
    def test_each_cad_record_script_declares_what_it_and_the_modules_it_loads_import(self) -> None:
        files = {path.name: path.read_text(encoding="utf-8") for path in sorted(SCRIPTS.glob("*.py"))}
        local = {path.stem for path in SKILLS.glob("*/scripts/*.py")}
        problems = missing_dependencies(files, local)
        self.assertEqual(problems, [], "Add the distribution to the script's PEP 723 dependencies:\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
