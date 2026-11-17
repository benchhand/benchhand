"""Classify a CalculiX run that cannot give a valid result into an actionable no-result error: a failed ccx
run, a load that the ccx input applies to no mesh node, or a zero result under a nonzero load (issue #143)."""

from __future__ import annotations

import re
import signal
from collections.abc import Mapping, Sequence
from typing import Any

# SPOOLES 2.2's I2Ohash_insert, unpatched in the CalculiX ccx that FreeCAD bundles, multiplies two
# front indices in 32-bit arithmetic: a factorisation with more than 46,340 fronts (46,341**2 >
# 2**31 - 1) indexes outside its hash table and crashes in FrontMtx_postProcess.
SPOOLES_MAX_FRONTS = 46_340
# Every front holds at least one node's three displacement equations.
_MIN_EQUATIONS_PER_FRONT = 3
_CRASH_SIGNALS = {signal.SIGSEGV, signal.SIGBUS, signal.SIGILL, signal.SIGFPE, signal.SIGABRT}
_JACOBIAN_ELEMENT = re.compile(r"nonpositive jacobian determinant in element (\d+)", re.IGNORECASE)


def ccx_failure(returncode: int, stdout: str, *, spooles_started: bool, node_count: int) -> dict[str, Any] | None:
    """Return the no-result error for a finished ccx run, or None when ccx exited cleanly.

    `spooles_started` reports whether ccx created `spooles.out`, which it does on entering the
    SPOOLES factorisation; `node_count` is the mesh node count, three equations per node.
    """
    if returncode == 0:
        return None
    dof = 3 * node_count
    errors = _error_messages(stdout)
    details: dict[str, Any] = {
        "returncode": returncode, "node_count": node_count, "degrees_of_freedom": dof,
        "stage": "spooles_factorisation" if spooles_started else "before_equation_solver", "errors": errors[:20],
    }
    crash = _signal(returncode)
    if crash is not None:
        details["signal"] = crash.name
        return {"code": "solver_crashed", "message": _crash_message(crash, spooles_started, dof), "details": details}
    return {"code": "solver_failed", "message": _stop_message(returncode, errors), "details": details}


def _signal(returncode: int) -> signal.Signals | None:
    """The signal that ended ccx: negative for a direct child, 128+N through a shell wrapper."""
    number = -returncode if returncode < 0 else returncode - 128 if 128 < returncode < 160 else None
    try:
        return signal.Signals(number) if number else None
    except ValueError:
        return None


def _error_messages(stdout: str) -> list[str]:
    """Distinct ccx *ERROR messages, each joined with its continuation lines, in first-seen order."""
    lines = stdout.splitlines()
    messages: list[str] = []
    for index, line in enumerate(lines):
        if "*ERROR" not in line:
            continue
        parts = [line]
        for follow in lines[index + 1:]:
            if not follow.strip() or "*ERROR" in follow or "*WARNING" in follow:
                break
            parts.append(follow)
        message = " ".join(" ".join(parts).split())
        if message not in messages:
            messages.append(message)
    return messages


def _crash_message(crash: signal.Signals, spooles_started: bool, dof: int) -> str:
    if crash == signal.SIGKILL:
        return (f"CalculiX ccx was killed by SIGKILL while solving {dof:,} degrees of freedom, usually by the "
                "operating system reclaiming memory; increase mesh_size to reduce the node count.")
    if crash not in _CRASH_SIGNALS:
        return f"CalculiX ccx was terminated by {crash.name} while solving {dof:,} degrees of freedom."
    if not spooles_started:
        return (f"CalculiX ccx crashed with {crash.name} before reaching the SPOOLES equation solver, while reading "
                f"or assembling {dof:,} degrees of freedom; rerun ccx on GmshMesh.inp in the run directory to "
                "reproduce it.")
    if dof > SPOOLES_MAX_FRONTS * _MIN_EQUATIONS_PER_FRONT:
        return (f"CalculiX ccx crashed with {crash.name} inside the SPOOLES equation solver on {dof:,} degrees of "
                f"freedom: SPOOLES in this ccx build overflows a 32-bit hash index once the factorisation has more "
                f"than {SPOOLES_MAX_FRONTS:,} fronts, which solid tetrahedral meshes reach near 1,000,000 degrees of "
                "freedom. Increase mesh_size to reduce the node count, which falls roughly with its cube.")
    return (f"CalculiX ccx crashed with {crash.name} inside the SPOOLES equation solver on {dof:,} degrees of "
            f"freedom, too few for SPOOLES's {SPOOLES_MAX_FRONTS:,}-front overflow, so the ccx build is suspect; "
            "rerun ccx on GmshMesh.inp in the run directory to reproduce it.")


def _stop_message(returncode: int, errors: list[str]) -> str:
    elements = sorted({int(match) for message in errors for match in _JACOBIAN_ELEMENT.findall(message)})
    if elements:
        shown = ", ".join(str(element) for element in elements[:10]) + (", ..." if len(elements) > 10 else "")
        return (f"CalculiX ccx rejected the mesh: {len(elements)} element(s) have a nonpositive Jacobian "
                f"({shown}). Gmsh produced distorted second-order elements, typically where mesh_size is coarse "
                "for the local wall thickness or curvature; reduce mesh_size.")
    if errors:
        more = f" (+{len(errors) - 3} more)" if len(errors) > 3 else ""
        return f"CalculiX ccx stopped with exit status {returncode}: " + "; ".join(errors[:3]) + more
    return f"CalculiX ccx stopped with exit status {returncode} without an *ERROR message."


def applied_loads(inp: str) -> dict[str, int]:
    """Each force constraint of a FreeCAD-written ccx input, mapped to the number of distinct nodes its *CLOAD
    lines load with a nonzero component. FreeCAD names the constraint in the `** <name>` comment before its
    *CLOAD, and writes one `node, degree of freedom, load` line per loaded component. This reads that form only:
    FreeCAD 1.1.4's writer (femsolver/calculix/write_constraint_force.py) emits neither node sets nor
    continuation records. So the count takes a node-set name for one target, without expanding the set, and
    reads each line alone."""
    loads: dict[str, set[str]] = {}
    comment, current = None, None
    for line in inp.splitlines():
        text = line.strip()
        if text.startswith("**"):
            comment = text[2:].strip()
        elif text.upper().startswith("*CLOAD"):
            current = comment or f"*CLOAD {len(loads)}"
            loads.setdefault(current, set())
        elif text.startswith("*"):
            current = None
        elif current is not None and text:
            fields = [field.strip() for field in text.split(",")]
            if len(fields) == 3 and _nonzero(fields[2]):
                loads[current].add(fields[0])
    return {name: len(nodes) for name, nodes in loads.items()}


def _nonzero(value: str) -> bool:
    """Whether a *CLOAD magnitude is nonzero. A magnitude that does not parse counts as nonzero, so the check
    passes it."""
    try:
        return float(value) != 0.0
    except ValueError:
        return True


def unapplied_load(applied: Mapping[str, int], loads: Sequence[tuple[str, float]]) -> dict[str, Any] | None:
    """The `load_not_applied` error when a load with a nonzero force reaches no node of the ccx input, else None.
    `loads` lists each case load in order as (its force constraint's name, its force in N)."""
    missing = [(index, {"constraint": name, "force_n": force, "nodes": applied.get(name, 0)})
               for index, (name, force) in enumerate(loads) if force != 0.0 and applied.get(name, 0) == 0]
    if not missing:
        return None
    named = "; ".join(f"load {index} ({load['force_n']:g} N) reaches no mesh node" for index, load in missing)
    return {"code": "load_not_applied", "details": {"loads": [load for _, load in missing]},
            "message": (f"{named} in the CalculiX input, so ccx would solve without it. FreeCAD found no element "
                        "face of the mesh on the selected faces: check the load's face selector, or change "
                        "mesh_size. Nothing was solved.")}


def zero_result(max_stress: float, max_displacement: float, loads: Sequence[tuple[str, float]]) -> dict[str, Any] | None:
    """The `zero_result` error when the case applies a nonzero force but ccx returns a peak von Mises stress
    or a peak displacement of exactly 0, else None. Such a result says the load never reached the solved model."""
    force = sum(abs(magnitude) for _, magnitude in loads)
    if force == 0.0 or (max_stress != 0.0 and max_displacement != 0.0):
        return None
    return {"code": "zero_result",
            "details": {"max_von_mises_mpa": max_stress, "max_displacement_mm": max_displacement,
                        "applied_force_n": force},
            "message": (f"CalculiX ccx returned a peak von Mises stress of {max_stress:g} MPa and a peak "
                        f"displacement of {max_displacement:g} mm under {force:g} N of load: the load did not reach "
                        "the solved model. The result is refused.")}
