"""Import every module under skills, harness and scripts by path, the way the repo loads them.

Run it on the CI Python (3.12), the minimum for host scripts, so import-time errors such as unhashable
dataclass defaults fail here. With --manifest it checks only the FreeCAD job set in
freecad_job_modules.txt: those modules run inside freecadcmd on FreeCAD's bundled Python 3.11, so
CI runs that mode on 3.11. It compiles each module, then imports it.
Tests and fixtures are out of scope: tests run in the test steps, and fixtures are job scripts that run
inside FreeCAD, CadQuery or build123d. A module whose import needs a package this environment cannot install
is reported as SKIPPED with the package name; any other failure fails the run.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = ("skills", "harness", "scripts")
MANIFEST = Path(__file__).resolve().with_name("freecad_job_modules.txt")
# Top-level packages that exist only inside a FreeCAD runtime or a heavy CAD environment.
UNAVAILABLE = {
    "FreeCAD": "FreeCAD runtime only",
    "FreeCADGui": "FreeCAD runtime only",
    "Part": "FreeCAD runtime only",
    "Mesh": "FreeCAD runtime only",
    "TechDraw": "FreeCAD runtime only",
    "Import": "FreeCAD runtime only",
    "ObjectsFem": "FreeCAD runtime only",
    "femmesh": "FreeCAD runtime only",
    "cadquery": "not installable in the import check",
    "build123d": "not installable in the import check",
    "numpy": "not installed in the import check",
    "trimesh": "not installed in the import check",
    "fcl": "not installed in the import check",
    "PIL": "not installed in the import check",
}
# Job scripts that run only under freecadcmd and read their job file at import.
JOB_SCRIPTS = {
    "skills/cad-flow/scripts/fea_solvers_job.py": "FreeCAD job script, reads CAD_DRAWING_JOB at import",
}


def manifest_paths(manifest: Path = MANIFEST) -> list[Path]:
    """The FreeCAD job set: one repo-relative path per line, `#` starts a comment."""
    lines = (line.split("#", 1)[0].strip() for line in manifest.read_text(encoding="utf-8").splitlines())
    return [ROOT / line for line in lines if line]


def candidates() -> list[Path]:
    found = []
    for tree in TREES:
        for path in sorted((ROOT / tree).rglob("*.py")):
            parts = path.relative_to(ROOT).parts
            if "tests" in parts or "fixtures" in parts or path.name.startswith("test_") or "__pycache__" in parts:
                continue
            found.append(path)
    return found


def import_one(path: Path) -> str | None:
    """Import `path`; return the unavailable package's reason, or None on success. Raises on any other failure."""
    reason = JOB_SCRIPTS.get(path.relative_to(ROOT).as_posix())
    if reason:
        return reason
    # A module inside a package (cadmodel/, cadviewer/, ...) loads under its dotted name from the package's parent.
    base = path.parent
    package: list[str] = []
    while (base / "__init__.py").exists():
        package.insert(0, base.name)
        base = base.parent
    stem = [] if path.stem == "__init__" else [path.stem]
    name = ".".join(package + stem) if package else "_import_check_" + path.stem
    for directory in (str(base), str(ROOT / "scripts")):
        if directory not in sys.path:
            sys.path.insert(0, directory)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except ModuleNotFoundError as exc:
        top = (exc.name or "").split(".")[0]
        if top in UNAVAILABLE:
            return f"{top}: {UNAVAILABLE[top]}"
        raise
    finally:
        if not package:
            sys.modules.pop(name, None)
    return None


def check(path: Path, *, compile_first: bool) -> str | None:
    if compile_first:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    return import_one(path)


def main(argv: list[str]) -> int:
    sys.dont_write_bytecode = True
    manifest_only = "--manifest" in argv
    paths = manifest_paths() if manifest_only else candidates()
    failed = skipped = ok = 0
    for path in paths:
        rel = path.relative_to(ROOT)
        try:
            reason = check(path, compile_first=manifest_only)
        except Exception as exc:  # noqa: BLE001 - report every failure kind, then fail the run
            failed += 1
            print(f"FAILED  {rel}: {type(exc).__name__}: {exc}")
            continue
        if reason:
            skipped += 1
            print(f"SKIPPED {rel} ({reason})")
        else:
            ok += 1
    print(f"Python {sys.version.split()[0]}: imported {ok}, skipped {skipped}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
