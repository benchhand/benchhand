# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""A skill script's PEP 723 dependencies cover every third-party package its code imports.

The required set comes from the code, not from a copy of the list: the test parses each script with
`ast`, follows the repository scripts it imports by name or loads by path (a string constant naming a `.py` file, in any skill),
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

from modload import PEP723_BLOCK

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
# Import name -> the distributions that provide it, when they differ from the name.
PROVIDERS = {
    "yaml": frozenset({"pyyaml"}),
    "ocp": frozenset({"cadquery-ocp", "cadquery-ocp-novtk"}),
    "fcl": frozenset({"python-fcl"}),
    "pil": frozenset({"pillow"}),
}
KERNEL_DISTRIBUTIONS = frozenset(d for key in ("ocp", "fcl") for d in PROVIDERS[key])
# Reaches the scan cannot tell from the loading call. The loader does not import the target into its own process:
# - emit.py loads sarif.py only when a caller asks for SARIF, and sarif.py imports jsonschema only to validate;
# - scratch.py runs form_check.py and cad_model.py as tools in their own process, as motion.py does render.py
#   (it loads render.py only to hash the file).
OPTIONAL_LOADS = frozenset(
    {
        ("cad-record/scripts/emit.py", "sarif"),
        ("cad-flow/scripts/scratch.py", "form_check"),
        ("cad-flow/scripts/scratch.py", "cad_model"),
        ("cad-motion/scripts/motion.py", "render"),
    }
)
# Geometry kernels. A loaded module imports them inside functions the loader never calls (pose.py's
# OCP helpers, loaded by the kernel-free validators), so only the script's own imports require them.
# Modules that only FreeCAD's own interpreter supplies; no PEP 723 header can declare them.
FREECAD_PROVIDED = frozenset({"freecad", "femmesh", "femsolver", "part"})


def declared_dependencies(source: str) -> set[str] | None:
    """Normalized distribution names from the PEP 723 block, or None when the file has no block."""
    block = PEP723_BLOCK.search(source)
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


def imported_distributions(source: str, siblings: set[str]) -> set[frozenset[str]]:
    """For each third-party module imported at top level or inside a function, the distributions that provide it."""
    found: set[frozenset[str]] = set()
    for node in _walk_runtime(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots = {node.module.split(".")[0]}
        else:
            continue
        for root in roots:
            name = root.lower()
            if root in sys.stdlib_module_names or root in siblings or root == "__future__" or name in FREECAD_PROVIDED:
                continue
            found.add(PROVIDERS.get(name, frozenset({name.replace("_", "-")})))
    return found


def local_modules(source: str, by_stem: dict[str, list[str]]) -> set[str]:
    """Paths of the repository scripts the source reaches: imported by name (the script's own directory is
    on `sys.path`), or, in a script that loads modules by path, named as a `.py` string constant, within or
    across skills. A script that never loads by path names `.py` files to run them in another process."""
    loads_by_path = "spec_from_file_location" in source
    reached: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        elif isinstance(node, ast.Constant) and loads_by_path and isinstance(node.value, str) and node.value.endswith(".py"):
            names = [node.value.removesuffix(".py")]
        else:
            continue
        for name in names:
            reached.update(by_stem.get(name, ()))
    return reached


def required_distributions(
    script: str, files: dict[str, str], by_stem: dict[str, list[str]], packages: frozenset[str]
) -> set[frozenset[str]]:
    """Provider sets for what the script and every repository script it imports or loads (transitively) import."""
    seen: set[str] = set()
    pending = [script]
    required: set[frozenset[str]] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        found = imported_distributions(files[name], set(by_stem) | packages)
        required |= found if name == script else {need for need in found if not need & KERNEL_DISTRIBUTIONS}
        pending.extend(
            path for path in local_modules(files[name], by_stem) if (name, Path(path).stem) not in OPTIONAL_LOADS
        )
    return required


def missing_dependencies(files: dict[str, str], packages: frozenset[str] = frozenset()) -> list[str]:
    """One line per script, keyed by path, whose PEP 723 block lacks a distribution its reach imports."""
    by_stem: dict[str, list[str]] = {}
    for path in files:
        by_stem.setdefault(Path(path).stem, []).append(path)
    problems = []
    for name, source in sorted(files.items()):
        declared = declared_dependencies(source)
        if declared is None:
            continue
        needs = required_distributions(name, files, by_stem, packages)
        missing = sorted("/".join(sorted(need)) for need in needs if not need & declared)
        if missing:
            problems.append(f"{name}: imports {', '.join(missing)} but does not declare it")
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
        self.assertEqual(imported_distributions(source, {"sib"}), {frozenset({"pyyaml"}), frozenset({"referencing"})})

    def test_import_name_maps_to_its_providing_distributions(self) -> None:
        files = {"a.py": header("cadquery-ocp-novtk") + "import OCP\nimport fcl\nimport femmesh\n"}
        self.assertEqual(missing_dependencies(files), ["a.py: imports python-fcl but does not declare it"])

    def test_a_loaded_scripts_kernel_import_is_not_required_of_its_loader(self) -> None:
        files = {
            "s/scripts/a.py": header() + 'spec_from_file_location("m", "b.py")\n',
            "s/scripts/b.py": "def f():\n    import OCP\n    import yaml\n",
        }
        self.assertEqual(missing_dependencies(files), ["s/scripts/a.py: imports pyyaml but does not declare it"])

    def test_type_checking_imports_are_not_required(self) -> None:
        source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import jsonschema\n"
        self.assertEqual(imported_distributions(source, set()), set())

    def test_a_script_loaded_by_path_contributes_its_imports(self) -> None:
        files = {"s/scripts/a.py": header() + 'spec_from_file_location("m", "b.py")\n', "s/scripts/b.py": "def f():\n    import yaml\n"}
        self.assertEqual(missing_dependencies(files), ["s/scripts/a.py: imports pyyaml but does not declare it"])

    def test_a_sibling_imported_by_name_contributes_its_imports(self) -> None:
        files = {"s/scripts/a.py": header() + "import b\n", "s/scripts/b.py": "import c\n", "s/scripts/c.py": "import yaml\n"}
        self.assertEqual(missing_dependencies(files), ["s/scripts/a.py: imports pyyaml but does not declare it"])

    def test_a_script_in_another_skill_loaded_by_path_contributes_its_imports(self) -> None:
        files = {"s/scripts/a.py": header() + 'spec_from_file_location("m", base / "t" / "scripts" / "c.py")\n', "t/scripts/c.py": "import yaml\n"}
        self.assertEqual(missing_dependencies(files), ["s/scripts/a.py: imports pyyaml but does not declare it"])

    def test_a_script_named_only_to_run_in_another_process_is_not_followed(self) -> None:
        files = {"s/scripts/a.py": header() + 'command = ["python", "b.py"]\n', "s/scripts/b.py": "import yaml\n"}
        self.assertEqual(missing_dependencies(files), [])

    def test_a_repository_package_is_not_a_distribution(self) -> None:
        files = {"s/scripts/a.py": header() + "import pkg\n"}
        self.assertEqual(missing_dependencies(files, frozenset({"pkg"})), [])

    def test_a_declared_superset_passes(self) -> None:
        files = {"a.py": header("pyyaml", "extra") + "import yaml\n"}
        self.assertEqual(missing_dependencies(files), [])


class ScriptDependencies(unittest.TestCase):
    def test_each_script_declares_what_it_and_the_scripts_it_reaches_import(self) -> None:
        files = {
            str(path.relative_to(SKILLS)): path.read_text(encoding="utf-8") for path in sorted(SKILLS.glob("*/scripts/*.py"))
        }
        packages = frozenset(path.name for path in SKILLS.glob("*/scripts/*/") if (path / "__init__.py").exists())
        problems = missing_dependencies(files, packages)
        self.assertEqual(problems, [], "Add the distribution to the script's PEP 723 dependencies:\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
