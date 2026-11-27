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
# Reaches the scan cannot tell from the import or load. It follows a whole module, so it cannot see that a
# caller uses one function. In each pair below the first file never runs what the second reaches:
# - emit.py loads sarif.py only when a caller asks for SARIF, and sarif.py imports jsonschema only to validate;
# - scratch.py runs form_check.py and cad_model.py as tools in their own process, as motion.py does render.py
#   (it loads render.py only to hash the file);
# - runner.py (in the kernel's environment) calls record.load_layout only (runner.py:111), and the cadmodel
#   package loads pose.py for its geometry rules (occ.py:18), while pose.py loads yaml_loader.py only to read an
#   assembly. The first element of a pair is a path prefix;
# - preflight.py imports fea_profile.py (which loads flow_profile.py and PyYAML) inside project_fea only, to
#   read a project's FEA setting. The cad-model runners, cad-analysis and cad-drawings reach preflight.py for
#   the FreeCAD lookup alone. preflight.py declares pyyaml itself, and a test pins that it does;
# - review_render.py calls record.load_yaml_loader only (review_render.py:126) and runs build_reference.py as a
#   tool (review_render.py:118).
OPTIONAL_LOADS = frozenset(
    {
        ("cad-record/scripts/emit.py", "sarif"),
        ("cad-flow/scripts/preflight.py", "fea_profile"),
        ("cad-flow/scripts/scratch.py", "form_check"),
        ("cad-flow/scripts/scratch.py", "cad_model"),
        ("cad-motion/scripts/motion.py", "render"),
        ("cad-model/scripts/cadmodel/runner.py", "record"),
        ("cad-model/scripts/cadmodel/", "pose"),
        ("cad-design/scripts/review_render.py", "record"),
        ("cad-design/scripts/review_render.py", "build_reference"),
    }
)
# The geometry, mesh and imaging stack. A script that is loaded imports it inside functions or optional
# extras that the loader may never call (pose.py's OCP helpers under the kernel-free validators, mesh.py's
# trimesh under form_check). So only a script's own imports require it; a loader declares what it imports itself.
TRANSIT_EXEMPT = ("ocp", "fcl", "numpy", "trimesh", "ocp_tessellate", "pil")
KERNEL_DISTRIBUTIONS = frozenset(d for key in TRANSIT_EXEMPT for d in PROVIDERS.get(key, {key.replace("_", "-")}))
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


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    caught = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(n, ast.Name) and n.id in {"ImportError", "ModuleNotFoundError"} for n in caught)


def _walk_required(node: ast.AST):
    """Every node that must run: not an `if TYPE_CHECKING:` body, and not the body of a `try` with an
    `except ImportError`, where an import or a load is an optional extra."""
    yield node
    skipped: list[ast.stmt] = []
    if isinstance(node, ast.If) and _is_type_checking(node.test):
        skipped = node.body
    elif isinstance(node, ast.Try) and any(_catches_import_error(h) for h in node.handlers):
        skipped = node.body
    for child in ast.iter_child_nodes(node):
        if child not in skipped:
            yield from _walk_required(child)


def imported_distributions(source: str, siblings: set[str]) -> set[frozenset[str]]:
    """For each third-party module imported at top level or inside a function, the distributions that provide it."""
    found: set[frozenset[str]] = set()
    for node in _walk_required(ast.parse(source)):
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


class Index:
    """The repository scripts: top-level ones by stem, and package modules by dotted name."""

    def __init__(self, files: dict[str, str]) -> None:
        self.by_stem: dict[str, list[str]] = {}
        self.modules: dict[str, str] = {}  # dotted name -> path, for the files of every package
        self.package_of: dict[str, str] = {}  # path -> dotted package that holds it
        roots = {str(Path(path).parent) for path in files if Path(path).name == "__init__.py"}
        for path in files:
            parent = Path(path).parent
            root = next((r for r in roots if parent == Path(r) or Path(r) in parent.parents), None)
            if root is None:
                self.by_stem.setdefault(Path(path).stem, []).append(path)
                continue
            parts = [Path(root).name, *parent.relative_to(root).parts]
            dotted = ".".join(parts if Path(path).stem == "__init__" else [*parts, Path(path).stem])
            self.modules[dotted] = path
            self.package_of[path] = ".".join(parts)
        self.roots = {name.split(".")[0] for name in self.modules}

    def module_paths(self, dotted: str, aliases: list[str]) -> set[str]:
        """Files of `dotted`, its parent packages, and `dotted.alias` for each alias that is a module."""
        names = {".".join(dotted.split(".")[:i]) for i in range(1, dotted.count(".") + 2)}
        names |= {f"{dotted}.{alias}" for alias in aliases}
        return {self.modules[name] for name in names if name in self.modules}


def local_modules(path: str, source: str, index: Index) -> set[str]:
    """Paths of the repository files the source reaches: a script imported by name (its directory is on
    `sys.path`), a package module imported absolutely or relatively, and, in a file that loads modules by
    path, a script named as a `.py` string constant. A file that never loads by path names `.py` files to
    run them in another process. A load inside `try: ... except ImportError` is an optional extra."""
    loads_by_path = "spec_from_file_location" in source
    reached: set[str] = set()
    for node in _walk_required(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                reached.update(index.by_stem.get(alias.name.split(".")[0], ()))
                reached.update(index.module_paths(alias.name, []))
        elif isinstance(node, ast.ImportFrom):
            aliases = [alias.name for alias in node.names]
            if node.level == 0 and node.module:
                reached.update(index.by_stem.get(node.module.split(".")[0], ()))
                loaders = [a for a in aliases if a.startswith("load_") and a[5:] in index.by_stem]
                if loaders and index.module_paths(node.module, []) and len(loaders) == len(aliases):
                    # `from cadmodel.motion import load_pose` reaches pose.py, not every load in motion.py
                    for alias in loaders:
                        reached.update(index.by_stem[alias[5:]])
                else:
                    reached.update(index.module_paths(node.module, aliases))
            elif node.level > 0 and path in index.package_of:
                base = index.package_of[path].split(".")
                base = base[: len(base) - (node.level - 1)]
                reached.update(index.module_paths(".".join([*base, *([node.module] if node.module else [])]), aliases))
        elif loads_by_path and isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith(".py"):
            reached.update(index.by_stem.get(node.value.removesuffix(".py"), ()))
    return reached


def _optional(loader: str, target: str) -> bool:
    return any(loader.startswith(prefix) and Path(target).stem == stem for prefix, stem in OPTIONAL_LOADS)


def required_distributions(script: str, files: dict[str, str], index: Index) -> set[frozenset[str]]:
    """Provider sets for what the script and every repository file it imports or loads (transitively) import."""
    seen: set[str] = set()
    pending = [script]
    required: set[frozenset[str]] = set()
    local = set(index.by_stem) | index.roots
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        found = imported_distributions(files[name], local)
        required |= found if name == script else {need for need in found if not need & KERNEL_DISTRIBUTIONS}
        pending.extend(
            path for path in local_modules(name, files[name], index) if not _optional(name, path)
        )
    return required


def missing_dependencies(files: dict[str, str]) -> list[str]:
    """One line per script, keyed by path, whose PEP 723 block lacks a distribution its reach imports."""
    index = Index(files)
    problems = []
    for name, source in sorted(files.items()):
        declared = declared_dependencies(source)
        if declared is None:
            continue
        needs = required_distributions(name, files, index)
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

    def test_a_loaded_scripts_geometry_stack_imports_are_not_required_of_its_loader(self) -> None:
        files = {
            "s/scripts/a.py": header() + 'spec_from_file_location("m", "b.py")\n',
            "s/scripts/b.py": "def f():\n    import OCP\n    import numpy\n    import yaml\n",
        }
        self.assertEqual(missing_dependencies(files), ["s/scripts/a.py: imports pyyaml but does not declare it"])

    def test_a_package_module_imported_by_name_contributes_its_imports(self) -> None:
        files = {
            "s/scripts/a.py": header() + "from pkg import mod\n",
            "s/scripts/pkg/__init__.py": "",
            "s/scripts/pkg/mod.py": "import yaml\n",
        }
        self.assertEqual(missing_dependencies(files), ["s/scripts/a.py: imports pyyaml but does not declare it"])

    def test_a_dotted_package_import_and_a_relative_import_are_followed(self) -> None:
        files = {
            "s/scripts/a.py": header() + "import pkg.mod\n",
            "s/scripts/pkg/__init__.py": "",
            "s/scripts/pkg/mod.py": "from . import deep\n",
            "s/scripts/pkg/deep.py": "from .leaf import X\n",
            "s/scripts/pkg/leaf.py": "import yaml\n",
        }
        self.assertEqual(missing_dependencies(files), ["s/scripts/a.py: imports pyyaml but does not declare it"])

    def test_a_package_module_that_loads_a_script_by_path_reaches_that_scripts_imports(self) -> None:
        files = {
            "s/scripts/a.py": header() + "from pkg import mod\n",
            "s/scripts/pkg/__init__.py": "",
            "s/scripts/pkg/mod.py": 'spec_from_file_location("m", base / "b.py")\n',
            "s/scripts/b.py": "import yaml\n",
        }
        self.assertEqual(missing_dependencies(files), ["s/scripts/a.py: imports pyyaml but does not declare it"])

    def test_importing_a_load_function_reaches_only_the_script_it_loads(self) -> None:
        files = {
            "s/scripts/a.py": header() + "from pkg.hub import load_b\n",
            "s/scripts/pkg/__init__.py": "",
            "s/scripts/pkg/hub.py": 'spec_from_file_location("m", "c.py")\n',
            "s/scripts/b.py": "x = 1\n",
            "s/scripts/c.py": "import yaml\n",
        }
        self.assertEqual(missing_dependencies(files), [])

    def test_a_load_guarded_by_try_except_import_error_does_not_count(self) -> None:
        guarded = 'try:\n    spec_from_file_location("m", "b.py")\nexcept ImportError:\n    pass\n'
        files = {"s/scripts/a.py": header() + guarded, "s/scripts/b.py": "import yaml\n"}
        self.assertEqual(missing_dependencies(files), [])

    def test_an_import_guarded_by_try_except_import_error_does_not_count(self) -> None:
        source = "try:\n    import numpy\nexcept ImportError:\n    numpy = None\n"
        self.assertEqual(imported_distributions(source, set()), set())

    def test_a_load_whose_try_catches_another_error_still_counts(self) -> None:
        other = 'try:\n    spec_from_file_location("m", "b.py")\nexcept ValueError:\n    pass\n'
        files = {"s/scripts/a.py": header() + other, "s/scripts/b.py": "import yaml\n"}
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

    def test_a_declared_superset_passes(self) -> None:
        files = {"a.py": header("pyyaml", "extra") + "import yaml\n"}
        self.assertEqual(missing_dependencies(files), [])


class ScriptDependencies(unittest.TestCase):
    def test_each_script_declares_what_it_and_the_scripts_it_reaches_import(self) -> None:
        files = {
            str(path.relative_to(SKILLS)): path.read_text(encoding="utf-8") for path in sorted(SKILLS.glob("*/scripts/**/*.py"))
        }
        problems = missing_dependencies(files)
        self.assertEqual(problems, [], "Add the distribution to the script's PEP 723 dependencies:\n" + "\n".join(problems))

    def test_preflight_shows_the_pyyaml_reach_of_its_project_read_to_the_scan(self) -> None:
        # preflight.py reads a project through fea_profile.py (PyYAML). The reach must be visible to the scan, so
        # preflight declares what it needs and the one exemption (OPTIONAL_LOADS) names the consumers' side.
        files = {
            str(path.relative_to(SKILLS)): path.read_text(encoding="utf-8") for path in sorted(SKILLS.glob("*/scripts/**/*.py"))
        }
        preflight = "cad-flow/scripts/preflight.py"
        reached = local_modules(preflight, files[preflight], Index(files))
        self.assertIn("cad-flow/scripts/fea_profile.py", reached)
        self.assertIn("pyyaml", declared_dependencies(files[preflight]) or set())


if __name__ == "__main__":
    unittest.main()
