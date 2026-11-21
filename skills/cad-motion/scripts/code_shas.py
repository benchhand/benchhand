# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The sha256 of a producer script and of every skill module it loads, for the recipe of what it publishes.

A published result is reusable only while the code that decided it is unchanged, so a producer puts
`closure(<its own script>)` in its recipe: a change to the script, or to a module it loads, changes
the recipe and stops the reuse.

The modules come from the script's own sources, read statically and followed transitively:

- `from pkg import name` and `import pkg.name`, where `pkg` is a package directory next to the
  importing file or above it inside the skills tree (cad-model's `cadmodel`).
- `import name` of a script next to the importer, or of the one `name.py` in another skill's
  `scripts/` folder that the importer put on `sys.path`.
- A path expression that ends in a `.py` file: `Path(__file__)` with `.resolve()`, `.parent`,
  `.parents[n]`, `/ "part"` and `.with_name("file.py")`, directly or through a module-level name
  bound to such a path (`HERE`, `SKILLS`, `LAYOUT_PATH`, ...).
- A call that names a sibling file only by its name, `_sibling("module", "file.py")`, when that
  file exists next to the caller.

These skills load their siblings in those forms, with `spec_from_file_location` on such a path or
with an import. Standard-library imports and the packages in THIRD_PARTY resolve to no file here,
so the closure leaves them out. The recipes name the tool versions where those matter. Any other
import, a relative import, or a path expression that names a missing file is an error, so a module
cannot drop out of a recipe unnoticed.

Code is not all that decides a verdict: a producer also reads data files (schemas, layout.json, the
ISO 286 tables). It declares them, and `closure(script, data)` hashes each declared file and every
file under a declared folder into the same table. `referenced_data(script)` lists the data that
the closure's modules name by a path expression, so a test can check that the declaration covers it.

GATE_ONLY names the modules a closure leaves out because they only gate a run (cad-record validate.py).

Standard library only. cad-record's `layout.code_shas` (#114) can take over this closure once it lands.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import sys
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import cast

SKILLS = Path(__file__).resolve().parents[2]


# The third-party packages these skills import. Any other import that is no skill module and no standard-library
# module is an error, so a module that code_shas cannot place never drops out of a recipe unnoticed.
THIRD_PARTY = frozenset({"OCP", "build123d", "cadquery", "defusedxml", "fcl", "jsonschema", "numpy", "referencing",
                         "trimesh", "typing_extensions", "yaml"})
# Modules hashed into a closure whose own loads it does not follow: preflight finds the FreeCAD command and
# checks the whole install (viewer, browser, solvers), and none of that decides a producer's verdict.
UNFOLLOWED = frozenset({"cad-flow/scripts/preflight.py"})
# Modules that gate a producer and decide nothing it publishes: they refuse or allow a run, and change no geometry
# and no output byte. A closure neither hashes them nor follows their loads, so an edit to one leaves the recipe key
# alone; a file only they reach (the SARIF schema, evidence.py) drops out with them. A producer that does read a gate's
# verdict into what it publishes must list the gate in its own closure by another route, not through this table.
# - cad-record validate.py: `cad_model.py run` consults it for the project-start profile and builds or refuses.
GATE_ONLY = frozenset({"cad-record/scripts/validate.py"})


class CodeShaError(ValueError):
    """A module names a file or module that code_shas cannot find."""


def _read(path: Path) -> bytes:
    return path.read_bytes()


def _string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _call_path(node: ast.Call, names: Mapping[str, Path], file: Path) -> Path | None:
    """`Path(x)`, `<path>.resolve()`, `<path>.absolute()` or `<path>.with_name("name")`."""
    if isinstance(node.func, ast.Name):
        return _path(node.args[0], names, file) if node.func.id == "Path" and len(node.args) == 1 else None
    if not isinstance(node.func, ast.Attribute):
        return None
    base = _path(node.func.value, names, file)
    if base is None:
        return None
    if node.func.attr in ("resolve", "absolute") and not node.args:
        return base
    name = _string(node.args[0]) if node.func.attr == "with_name" and len(node.args) == 1 else None
    return None if name is None else base.with_name(name)


def _parents_path(node: ast.Subscript, names: Mapping[str, Path], file: Path) -> Path | None:
    """`<path>.parents[n]`."""
    if not (isinstance(node.value, ast.Attribute) and node.value.attr == "parents"
            and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int)):
        return None
    base = _path(node.value.value, names, file)
    return None if base is None else base.parents[node.slice.value]


def _path(node: ast.AST, names: Mapping[str, Path], file: Path) -> Path | None:
    """The path `node` evaluates to, for the path forms in the module docstring. Any other expression gives None."""
    if isinstance(node, ast.Name):
        return file if node.id == "__file__" else names.get(node.id)
    if isinstance(node, ast.Call):
        return _call_path(node, names, file)
    if isinstance(node, ast.Subscript):
        return _parents_path(node, names, file)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        base = _path(node.value, names, file)
        return None if base is None else base.parent
    part = _string(node.right) if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) else None
    if part is None:
        return None
    base = _path(cast(ast.BinOp, node).left, names, file)
    return None if base is None else base / part


def _module_paths(tree: ast.Module, file: Path) -> dict[str, Path]:
    """The module-level names bound to a path, in source order."""
    names: dict[str, Path] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 \
                and isinstance(statement.targets[0], ast.Name):
            target, value = statement.targets[0].id, statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name) and statement.value:
            target, value = statement.target.id, statement.value
        else:
            continue
        found = _path(value, names, file)
        if found is not None:
            names[target] = found
    return names


def _path_targets(tree: ast.Module, file: Path) -> Iterator[Path]:
    names = _module_paths(tree, file)
    for node in ast.walk(tree):
        if isinstance(node, (ast.BinOp, ast.Call)):
            found = _path(node, names, file)
            if found is not None and found.suffix == ".py":
                yield found
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.endswith(".py") \
                        and "/" not in arg.value and (file.parent / arg.value).is_file():
                    yield file.parent / arg.value


def _package_root(folder: Path, package: str) -> Path | None:
    """The folder that holds package directory `package`: searched from `folder` up to the skills tree, else the one
    skill `scripts/` folder that holds it (an importer that put that folder on sys.path)."""
    for candidate in (folder, *folder.parents):
        if (candidate / package / "__init__.py").is_file():
            return candidate
        if candidate == SKILLS:
            break
    found = sorted(path.parent.parent for path in SKILLS.glob(f"*/scripts/{package}/__init__.py"))
    if len(found) > 1:
        raise CodeShaError(f"package {package} imported in {folder} exists in {len(found)} skills: {found}")
    return found[0] if found else None


def _script_module(folder: Path, name: str) -> Path | None:
    """`name`.py imported as a top-level module from another skill's scripts folder (the importer put that folder
    on sys.path): the one file of that name there, None when there is none."""
    found = sorted(SKILLS.glob(f"*/scripts/{name}.py"))
    if len(found) > 1:
        raise CodeShaError(f"import {name} in {folder} matches {len(found)} skill scripts: {[str(f) for f in found]}")
    return found[0] if found else None


def _imports(tree: ast.Module, file: Path) -> Iterator[tuple[list[str], list[str]]]:
    """(dotted module, imported names) of every import in `tree`. A relative import is an error."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                raise CodeShaError(f"{file}:{node.lineno}: a relative import, which code_shas does not follow")
            yield node.module.split("."), [alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            yield from ((alias.name.split("."), []) for alias in node.names)


def _top_level_module(dotted: list[str], file: Path) -> Iterator[Path]:
    """The file of an import that names no package in the skills tree: a script next to the importer (its own
    folder comes first on its sys.path), nothing for the standard library or THIRD_PARTY, else the one script of
    that name in another skill's scripts folder. Anything else is an error."""
    top, folder = dotted[0], file.parent
    if len(dotted) == 1 and (folder / f"{top}.py").is_file():
        yield folder / f"{top}.py"
        return
    if top in sys.stdlib_module_names or top in THIRD_PARTY:
        return
    script = _script_module(folder, top) if len(dotted) == 1 else None
    if script is None:
        raise CodeShaError(f"{file}: `{'.'.join(dotted)}` is no skill module, standard-library module or "
                           f"listed third-party package (code_shas.THIRD_PARTY)")
    yield script


def _package_modules(root: Path, dotted: list[str], names: list[str], file: Path) -> Iterator[Path]:
    """The files of `from <dotted> import <names>` or `import <dotted>` from a package below `root`."""
    package = root.joinpath(*dotted)
    if package.with_suffix(".py").is_file():
        yield package.with_suffix(".py")
        return
    if not (package / "__init__.py").is_file():
        raise CodeShaError(f"{file}: `{'.'.join(dotted)}` names no module in {root}")
    yield package / "__init__.py"
    yield from (package / f"{name}.py" for name in names if (package / f"{name}.py").is_file())


def _import_targets(tree: ast.Module, file: Path) -> Iterator[Path]:
    for dotted, names in list(_imports(tree, file)):
        root = _package_root(file.parent, dotted[0])
        yield from (_top_level_module(dotted, file) if root is None else _package_modules(root, dotted, names, file))


def _is_gate_only(path: Path) -> bool:
    return path.is_relative_to(SKILLS) and path.relative_to(SKILLS).as_posix() in GATE_ONLY


def _data_files(entry: Path) -> list[Path]:
    """A declared data file, or every file under a declared data folder."""
    if entry.is_file():
        return [entry]
    if entry.is_dir():
        return sorted(path for path in entry.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    raise CodeShaError(f"{entry} is declared as data but does not exist")


def referenced_data(script: Path) -> set[Path]:
    """Every file or data folder under the skills tree that a module of `script`'s closure names by a path
    expression: what its declared data must cover. It skips folders that hold Python modules, which are code."""
    found: set[Path] = set()
    for name in closure(script):
        if name in UNFOLLOWED:
            continue
        path = SKILLS / name
        tree = ast.parse(_read(path), filename=str(path))
        names = _module_paths(tree, path)
        inner = {id(node.left) for node in ast.walk(tree) if isinstance(node, ast.BinOp)} | \
                {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Subscript)} | \
                {id(node.value) for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute) and node.attr in ("parent", "parents", "with_name", "resolve", "absolute")}
        for node in ast.walk(tree):
            if id(node) in inner or not isinstance(node, (ast.BinOp, ast.Call, ast.Name)):
                continue   # only a whole path expression, not the folders it starts from
            target = _path(node, names, path)
            if target is None or target.suffix == ".py":
                continue
            target = target.resolve()
            if not target.is_relative_to(SKILLS) or target == SKILLS or not target.exists():
                continue
            if target.is_dir() and (any(target.glob("*.py")) or target.parent == SKILLS):
                continue
            found.add(target)
    return found


def closure(script: Path, data: Iterable[Path] = ()) -> dict[str, str]:
    """{path under the skills tree: sha256} of `script`, every skill module it loads, transitively, and the declared
    `data` the producer reads to reach its verdict (a folder stands for every file under it).

    Each process reads a script once (`_closure` caches it). The process loaded its code when it started."""
    return dict(_closure(Path(script).resolve(), tuple(Path(entry).resolve() for entry in data)))


@functools.cache
def _closure(script: Path, data: tuple[Path, ...]) -> tuple[tuple[str, str], ...]:
    found: dict[Path, bytes] = {}
    pending = [script]
    while pending:
        path = pending.pop()
        if path in found:
            continue
        if not path.is_file():
            raise CodeShaError(f"{path} is named as a module but does not exist")
        found[path] = _read(path)
        if path.is_relative_to(SKILLS) and path.relative_to(SKILLS).as_posix() in UNFOLLOWED:
            continue
        tree = ast.parse(found[path], filename=str(path))
        for target in (*_path_targets(tree, path), *_import_targets(tree, path)):
            target = target.resolve()
            if target not in found and not _is_gate_only(target):
                pending.append(target)
    for entry in data:
        for path in _data_files(entry):
            found[path] = _read(path)
    return tuple((path.relative_to(SKILLS).as_posix(), hashlib.sha256(content).hexdigest())
                 for path, content in sorted(found.items()))


def digest(script: Path, data: Iterable[Path] = ()) -> str:
    """One sha256 over `closure(script, data)`."""
    table = closure(script, data)
    return hashlib.sha256("".join(f"{name}\0{sha}\n" for name, sha in table.items()).encode()).hexdigest()
