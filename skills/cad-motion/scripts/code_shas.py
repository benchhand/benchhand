"""The sha256 of a producer script and of every skill module it loads, for the recipe of what it publishes.

A published result is reusable only while the code that decided it is unchanged, so a producer puts
`closure(<its own script>)` in its recipe: a change to the script, or to a module it loads, changes
the recipe and stops the reuse.

The modules come from the script's own sources, read statically and followed transitively:

- `from pkg import name` and `import pkg.name`, where `pkg` is a package directory next to the
  importing file or above it inside the skills tree (cad-model's `cadmodel`).
- A path expression that ends in a `.py` file: `Path(__file__)` with `.resolve()`, `.parent`,
  `.parents[n]`, `/ "part"` and `.with_name("file.py")`, directly or through a module-level name
  bound to such a path (`HERE`, `SKILLS`, `LAYOUT_PATH`, ...).
- A call that names a sibling file only by its name, `_sibling("module", "file.py")`, when that
  file exists next to the caller.

These skills load their siblings in those forms, with `spec_from_file_location` on such a path or
with a package import. Standard-library and third-party imports resolve to no file here, so the
closure leaves them out. The recipes name the tool versions where those matter. A path expression
that names a missing file is an error, so a renamed module cannot drop out of a recipe unnoticed.

Standard library only. cad-record's `layout.code_shas` (#114) can take over this closure once it lands.
"""

from __future__ import annotations

import ast
import functools
import hashlib
from collections.abc import Iterator, Mapping
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]


class CodeShaError(ValueError):
    """A module names a sibling file that does not exist."""


def _read(path: Path) -> bytes:
    return path.read_bytes()


def _path(node: ast.AST, names: Mapping[str, Path], file: Path) -> Path | None:
    """The path `node` evaluates to, for the path forms in the module docstring. Any other expression gives None."""
    if isinstance(node, ast.Name):
        return file if node.id == "__file__" else names.get(node.id)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "Path" and len(node.args) == 1:
            return _path(node.args[0], names, file)
        if isinstance(node.func, ast.Attribute):
            base = _path(node.func.value, names, file)
            if base is None:
                return None
            if node.func.attr in ("resolve", "absolute") and not node.args:
                return base
            if node.func.attr == "with_name" and len(node.args) == 1 and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                return base.with_name(node.args[0].value)
        return None
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        base = _path(node.value, names, file)
        return None if base is None else base.parent
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "parents" \
            and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
        base = _path(node.value.value, names, file)
        return None if base is None else base.parents[node.slice.value]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and isinstance(node.right, ast.Constant) \
            and isinstance(node.right.value, str):
        base = _path(node.left, names, file)
        return None if base is None else base / node.right.value
    return None


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
    """The folder that holds package directory `package`, searched from `folder` up to the skills tree."""
    for candidate in (folder, *folder.parents):
        if (candidate / package / "__init__.py").is_file():
            return candidate
        if candidate == SKILLS:
            break
    return None


def _import_targets(tree: ast.Module, folder: Path) -> Iterator[Path]:
    modules: list[tuple[list[str], list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append((node.module.split("."), [alias.name for alias in node.names]))
        elif isinstance(node, ast.Import):
            modules += [(alias.name.split("."), []) for alias in node.names]
    for dotted, names in modules:
        root = _package_root(folder, dotted[0])
        if root is None:
            continue   # standard library or third party
        package = root.joinpath(*dotted)
        if package.with_suffix(".py").is_file():
            yield package.with_suffix(".py")
            continue
        yield package / "__init__.py"
        for name in names:
            if (package / f"{name}.py").is_file():
                yield package / f"{name}.py"


def closure(script: Path) -> dict[str, str]:
    """{path under the skills tree: sha256} of `script` and every skill module it loads, transitively.

    Each process reads a script once (`_closure` caches it). The process loaded its code when it started."""
    return dict(_closure(Path(script).resolve()))


@functools.cache
def _closure(script: Path) -> tuple[tuple[str, str], ...]:
    found: dict[Path, bytes] = {}
    pending = [script]
    while pending:
        path = pending.pop()
        if path in found:
            continue
        if not path.is_file():
            raise CodeShaError(f"{path} is named as a module but does not exist")
        found[path] = _read(path)
        tree = ast.parse(found[path], filename=str(path))
        for target in (*_path_targets(tree, path), *_import_targets(tree, path.parent)):
            target = target.resolve()
            if target not in found:
                pending.append(target)
    return tuple((path.relative_to(SKILLS).as_posix(), hashlib.sha256(data).hexdigest())
                 for path, data in sorted(found.items()))


def digest(script: Path) -> str:
    """One sha256 over `closure(script)`."""
    table = closure(script)
    return hashlib.sha256("".join(f"{name}\0{sha}\n" for name, sha in table.items()).encode()).hexdigest()
