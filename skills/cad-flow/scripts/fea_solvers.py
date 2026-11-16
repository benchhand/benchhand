"""Find the CalculiX ccx and the Gmsh that FreeCAD's FEM runs, with their versions.

The lookup runs inside FreeCAD. An AppImage mounts its bundle at a transient /tmp/.mount_* directory
that only the running FreeCAD sees, and a FREECADCMD wrapper script lies outside the bundle, so the
host cannot find the bundled solvers beside the command it starts. fea_solvers_job.py runs
`resolve_in_freecad` in a FreeCAD process for `probe`, and the FEA job runs it again in its own.

Order for each tool: $CCX or $GMSH, used as given without fallback; then FreeCAD's own lookup
(femsolver.settings.get_binary("Calculix"), the one ccxtools.setup_ccx applies to its
ccxBinaryPath preference, and GmshTools.get_gmsh_command); then beside realpath(freecadcmd);
then PATH.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# tool -> (override variable, executable name, version arguments)
TOOLS = {"calculix": ("CCX", "ccx", ("-v",)), "gmsh": ("GMSH", "gmsh", ("--version",))}
VERSION = re.compile(r"\b\d+(?:\.\d+){1,3}\b")
JOB = Path(__file__).with_name("fea_solvers_job.py")
PROBE_TIMEOUT_SECONDS = 120.0


class ProbeError(RuntimeError):
    """FreeCAD could not run the probe job."""


def _executable(path: str | os.PathLike[str]) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def resolve(tool: str, *, environ: Mapping[str, str], freecad_lookup: str | None, freecadcmd: str | None,
            search_path: str | None) -> tuple[str | None, str]:
    """The executable for `tool` and how it was found, or None and why not.

    `freecad_lookup` is what FreeCAD's own lookup returned inside the FreeCAD process, and `search_path`
    is that process's PATH.
    """
    variable, name, _ = TOOLS[tool]
    configured = environ.get(variable)
    if configured:
        if _executable(configured):
            return os.path.abspath(configured), f"${variable}"
        return None, f"${variable}={configured} is not an executable file"
    beside = os.path.join(os.path.dirname(os.path.realpath(freecadcmd)), name) if freecadcmd else None
    found = shutil.which(name, path=search_path)
    for source, candidate in (("FreeCAD's lookup", freecad_lookup), ("beside freecadcmd", beside), ("PATH", found)):
        if candidate and _executable(candidate):
            return os.path.abspath(candidate), source
    return None, (f"{name} not found: ${variable} is unset, FreeCAD's lookup found none, none is beside "
                  f"{os.path.realpath(freecadcmd) if freecadcmd else 'freecadcmd'} and none is on PATH")


def tool_version(command: str, args: tuple[str, ...]) -> str:
    """The first dotted version number the tool prints; ccx -v exits nonzero after printing it."""
    try:
        completed = subprocess.run([command, *args], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProbeError(f"cannot query the version of {command}: {exc}") from exc
    match = VERSION.search(completed.stdout + completed.stderr)
    if match is None:
        raise ProbeError(f"cannot determine the version of {command}")
    return match.group(0)


def _freecad_lookup(tool: str) -> str | None:
    """FreeCAD's own lookup, only inside FreeCAD; it reads the FEM preferences of the running profile."""
    if tool == "calculix":
        from femsolver import settings

        found: str | None = settings.get_binary("Calculix", silent=True)
        return found
    from femmesh.gmshtools import GmshTools

    holder = types.SimpleNamespace(gmsh_bin="")
    try:
        GmshTools.get_gmsh_command(holder)
    except Exception:  # noqa: BLE001 - GmshError when neither the preference nor PATH names a Gmsh
        return None
    gmsh: str = holder.gmsh_bin
    return gmsh or None


def resolve_in_freecad(freecadcmd: str | None, environ: Mapping[str, str] = os.environ) -> dict[str, dict[str, str]]:
    """Each tool's path, version and source, or its error; runs inside a FreeCAD process."""
    report: dict[str, dict[str, str]] = {}
    for tool, (_, _, version_args) in TOOLS.items():
        path, source = resolve(tool, environ=environ, freecad_lookup=_freecad_lookup(tool), freecadcmd=freecadcmd,
                               search_path=environ.get("PATH"))
        if path is None:
            report[tool] = {"error": source}
            continue
        try:
            report[tool] = {"path": path, "version": tool_version(path, version_args), "source": source}
        except ProbeError as exc:
            report[tool] = {"error": str(exc)}
    return report


def _freecad_runtime() -> Any:
    scripts = Path(__file__).resolve().parents[2] / "cad-drawings" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import freecad_runtime

    return freecad_runtime


def probe(freecadcmd: Path, *, environ: Mapping[str, str] | None = None,
          timeout: float = PROBE_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Run the probe job in an isolated FreeCAD process, as the FEA job runs.

    The report has `freecad` (path and version of the running FreeCAD), `resource_dir` (its
    App.getResourceDir()) and, for `calculix` and `gmsh`, either `path`, `version` and `source` or
    `error`. Raises ProbeError when FreeCAD does not run the job.
    """
    runtime = _freecad_runtime()
    with tempfile.TemporaryDirectory(prefix="cad-fea-solvers-") as workdir:
        try:
            result, reason = runtime.run_job(freecadcmd, JOB, {"freecadcmd": str(freecadcmd),
                                                               "module_dir": str(Path(__file__).resolve().parent)},
                                             Path(workdir), timeout, environ=environ)
        except OSError as exc:  # FreeCAD did not start, such as a wrapper whose interpreter is missing
            raise ProbeError(f"cannot start FreeCAD {freecadcmd}: {exc}") from exc
    if result is None:
        raise ProbeError(f"FreeCAD {freecadcmd} could not run the solver probe: {reason}")
    return {key: result[key] for key in ("freecad", "resource_dir", *TOOLS)}
