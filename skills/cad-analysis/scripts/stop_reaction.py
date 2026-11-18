#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["cadquery-ocp-novtk==8.0.1.1.0", "jsonschema>=4.23", "pyyaml>=6.0"]
# ///
"""Stop reaction: the `engineering.stop_reaction` check of every critical structural interface that carries
a load, derived from the modelled contact as one required-check packet.

Usage: uv run --script stop_reaction.py --hw hw (--version vN | --parts-dir DIR | --step STEP) [--attach REF=PARENT]...
       [--replace] [--out .cad-work/stop-reaction.json]

Obligations are cad-record required_checks' engineering.stop_reaction@IF-###.

Pivot. The movable joints on the kinematic path between the interface's two parts: exactly one, a revolute
joint, is the pivot. None means the interface is no stop (not applicable); several, a prismatic or a coupled
pivot leave it unknown. The pivot is posed at its `min` and at its `max`, the other joints at nominal; an end
at which the two parts are at zero distance is a stop. A pivot whose limits touch nothing is no stop either
(not applicable, with the gaps in the reason).

Patch. The overlap of the touching, opposite-facing planar faces of the two parts. Its outline gives the arms,
arm = ((p - o) x n) . a, with o a point on the pivot axis, a the axis direction and n the outward normal of the
fixed part's face, signed so that a reaction that resists the load is positive. A face that does not resist it
(or lies on the axis) has an arm of zero or less.

Applied moment. The peak force of the interface's use-case loads times the moving part's farthest point from
the axis. The record does not say where the force acts, so this arm is a conservative upper bound.

Design reaction. stop_statics.py: M over the smallest arm. A smallest arm of zero or less is `unbounded`
(unknown, critical), and only an FEA contact case approved in a design record can close it. A recorded
functional.contact.load below the design reaction is `understated` (fail, major). The uniform and compliant
figures, the lower bound and the pivot load bound (reaction plus the applied force) are evidence only.

With --version the packet is published to hw/checks/<V>/stop-reaction/ (bundle.checks): result.json
(schemas/manufacturing-check-result.schema.json) and evidence/stop-reaction.json.
An end within the effective contact tolerance counts as the stop, and the statics run on the closest opposite-facing
parallel planar faces there. The tolerance is the larger of STOP_CONTACT_TOLERANCE_MM and the largest clearance of a
fit the interface declares as `<size> <HOLE/SHAFT>`; the evidence records it, its source and the gap at each limit.
Exit 0 = every obligation passes or is not applicable, 1 = a failure or an unknown, 2 = usage or input error.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import stop_extrema as extrema
import stop_statics as statics


def _load_module(name: str, path: Path) -> Any:
    """A sibling skill's script module, loaded once per process under `name`."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path} not found")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SKILLS = Path(__file__).resolve().parents[2]
pose = _load_module("cad_motion_pose", SKILLS / "cad-motion" / "scripts" / "pose.py")
fits = _load_module("cad_fits_fits", SKILLS / "cad-fits" / "scripts" / "fits.py")
check_packet = _load_module("cad_record_check_packet", SKILLS / "cad-record" / "scripts" / "check_packet.py")
intent = _load_module("cad_record_manufacturing_intent", SKILLS / "cad-record" / "scripts" / "manufacturing_intent.py")
LAYOUT = pose.LAYOUT

PRODUCER = "cad-analysis stop_reaction.py"
SUBJECT = "stop-reaction"
FAMILY = "engineering.stop_reaction"
CONTACT_MM = 1e-4  # parts at or under this distance touch; planes this close are coincident
# A joint limit whose parts are this close counts as the stop. A stop with a small gap in the model then gets a
# reaction. The value is a tunable. Issue #165 moves it into the tool-settings schema. The effective tolerance
# is the larger of it and the interface's declared fit clearance.
STOP_CONTACT_TOLERANCE_MM = 0.15
STOP_CONTACT_EPSILON_MM = 1e-6  # float noise: a gap on the tolerance gets no verdict from rounding
NORMAL_TOL = 1e-6  # |n1 . n2 + 1| under this: opposite-facing faces
MIN_AREA_MM2 = 1e-6
METHOD = ("rigid statics of the modelled stop contact: the peak use-case force times the moving part's farthest point "
          "from the pivot axis, over the smallest arm of the overlap of the touching planar faces (cad-analysis "
          "stop_reaction.py)")
COVERAGE = ("the reaction of one pivot at its joint limits under the peak recorded force; the force is taken at the "
            "moving part's farthest point from the axis, perpendicular to the lever, because its location is not "
            "recorded; strength and stiffness of the load path, friction, dynamics and non-planar contact are not covered")
RULE_SOURCE = {"kind": "policy", "reference": "cad-analysis stop reaction rule: a stop's design reaction is the applied moment over "
                                              "its smallest contact arm", "origin": "cad-agent",
               "document": "skills/cad-analysis/scripts/stop_reaction.py"}
REMEDY_UNBOUNDED = ("add an FEA contact case for this stop (cad-analysis fea_static.py) and approve it in a design record, or "
                    "move the stop face so its nearest edge is clear of the pivot axis")
Vec = tuple[float, float, float]


# --------------------------------------------------------------------------- vectors


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


# --------------------------------------------------------------------------- geometry (OCP)


def xyz(point: Any) -> Vec:
    return (point.X(), point.Y(), point.Z())


def plane_of(face: Any) -> tuple[Vec, Vec] | None:
    """(a point, the outward unit normal) of a planar face as placed, else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_REVERSED

    surface = BRepAdaptor_Surface(face)
    if surface.GetType() != GeomAbs_Plane:
        return None
    plane = surface.Plane()
    normal = xyz(plane.Axis().Direction())
    if face.Orientation() == TopAbs_REVERSED:
        normal = (-normal[0], -normal[1], -normal[2])
    return xyz(plane.Location()), normal


def separation(first: Any, second: Any) -> float:
    """The minimum distance in mm between two shapes."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    extrema = BRepExtrema_DistShapeShape(first, second)
    if not extrema.IsDone():
        raise pose.MotionError("distance computation failed")
    return float(extrema.Value())


def overlap(fixed_face: Any, moving_face: Any) -> list[Any]:
    """The faces of the Boolean common of two coplanar faces."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

    common = BRepAlgoAPI_Common()
    common.SetArguments(pose.shape_list([fixed_face]))
    common.SetTools(pose.shape_list([moving_face]))
    common.SetNonDestructive(True)
    common.SetFuzzyValue(CONTACT_MM)
    common.Build()
    if not common.IsDone():
        raise pose.MotionError("contact patch computation failed")
    return [face for face in extrema.faces(common.Shape()) if face_area(face) > MIN_AREA_MM2]


def overlap_of(moving: Any, fixed: Any) -> tuple[float, float]:
    """(volume in mm3, depth in mm) of the volume the two parts share, or (0, 0) when they only touch. Overlap is a
    shared volume above pose.INTERFERENCE_MM3, the rule cad-motion's sweep and cad-model check use. The depth is the
    thinnest width of the shared region along the normal of one of its planar faces, exact for lines and arcs: a
    sliver of overlap is as thick as its flat faces say. A region with no planar face gets its smallest oriented box side."""
    from OCP.Bnd import Bnd_OBB
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepBndLib import BRepBndLib

    common = BRepAlgoAPI_Common()
    common.SetArguments(pose.shape_list([moving]))
    common.SetTools(pose.shape_list([fixed]))
    common.SetNonDestructive(True)
    common.SetFuzzyValue(pose.FUZZY_MM)
    common.Build()
    if not common.IsDone():
        raise pose.MotionError("overlap computation failed")
    shared = common.Shape()
    volume = float(pose.volume_properties(shared).Mass())
    if volume <= pose.INTERFERENCE_MM3:
        return 0.0, 0.0
    widths = []
    for face in extrema.faces(shared):
        plane = plane_of(face)
        if plane is not None:
            low, high = extrema.linear_range(shared, plane[1], plane[0])
            widths.append(high - low)
    if widths:
        return volume, min(widths)
    box = Bnd_OBB()
    BRepBndLib.AddOBB_s(shared, box, False, True, False)
    return volume, 2.0 * min(box.XHSize(), box.YHSize(), box.ZHSize())


def face_area(face: Any) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return float(props.Mass())


def scale(a: Vec, factor: float) -> Vec:
    return (a[0] * factor, a[1] * factor, a[2] * factor)


@dataclass(frozen=True)
class Pivot:
    joint: Any
    moving: str  # occurrence id of the part that turns relative to the other
    fixed: str


def pivot_between(chain: Any, first: str, second: str) -> tuple[Pivot | None, str]:
    """(the pivot of the two occurrences, "") or (None, why not): the one movable joint on their tree path."""
    path_first, path_second = chain.path(first), chain.path(second)
    shared = 0
    while shared < min(len(path_first), len(path_second)) and path_first[shared] is path_second[shared]:
        shared += 1
    movable = [(who, joint) for who, path in ((first, path_first[shared:]), (second, path_second[shared:]))
               for joint in path if joint.movable]
    if not movable:
        return None, "no movable joint connects the two parts: the interface is not a stop"
    if len(movable) > 1:
        return None, f"several movable joints connect the two parts ({', '.join(j.id for _, j in movable)}): the pivot is not single"
    who, joint = movable[0]
    if joint.id in chain.couplings.laws:
        return None, f"{joint.id} is driven through a coupling: its limits are not independent stops"
    if joint.type != "revolute":
        return None, f"{joint.id} is {joint.type}: only a revolute stop is derived"
    return Pivot(joint, who, second if who == first else first), ""


def contact_faces(fixed: Any, moving: Any, tolerance: float) -> list[tuple[Any, Vec]]:
    """(overlap face, outward normal of the fixed part's face) for the closest opposite-facing parallel planar faces
    of the two parts, at most `tolerance` apart. The moving face moves onto the fixed face's plane first, so a
    stop with a small gap in the model gives the same overlap as one that touches."""
    moving_planes = [(face, plane_of(face)) for face in extrema.faces(moving)]
    found: list[tuple[float, Any, Vec]] = []
    for fixed_face in extrema.faces(fixed):
        fixed_plane = plane_of(fixed_face)
        if fixed_plane is None:
            continue
        for moving_face, moving_plane in moving_planes:
            if moving_plane is None or abs(dot(fixed_plane[1], moving_plane[1]) + 1.0) > NORMAL_TOL:
                continue
            offset = dot(sub(moving_plane[0], fixed_plane[0]), fixed_plane[1])
            if abs(offset) > tolerance + STOP_CONTACT_EPSILON_MM:
                continue
            carried = pose.placed(moving_face, pose.Rigid.translation(fixed_plane[1], -offset))
            found += [(abs(offset), face, fixed_plane[1]) for face in overlap(fixed_face, carried)]
    closest = min((offset for offset, _, _ in found), default=0.0)
    return [(face, normal) for offset, face, normal in found if offset <= closest + CONTACT_MM]


def patch_of(contact: Sequence[tuple[Any, Vec]], origin: Vec, axis: Vec, sign: float) -> statics.Patch | None:
    """The patch's arms about the axis through `origin`, or None without contact. A point p of a face with
    outward normal n has the arm sign * ((p - o) x n) . a = gradient . (p - o), gradient = sign * (n x a), so its
    integrals follow from the face's area, centroid and moment of inertia about the line where the arm is zero."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt

    arms: list[float] = []
    area = first = second = 0.0
    for face, normal in contact:
        gradient = scale(cross(normal, axis), sign)
        arms += list(extrema.linear_range(face, gradient, origin))
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        size2 = dot(gradient, gradient)
        arm_c = dot(gradient, sub(xyz(props.CentreOfMass()), origin))
        area += props.Mass()
        first += props.Mass() * arm_c
        if size2 > 1e-18:
            zero = sub(xyz(props.CentreOfMass()), scale(gradient, arm_c / size2))
            line = scale(cross(normal, gradient), 1.0 / math.sqrt(size2))
            second += size2 * props.MomentOfInertia(gp_Ax1(gp_Pnt(*zero), gp_Dir(*line)))
    return statics.Patch(min(arms), max(arms), area, first, second) if arms else None


# --------------------------------------------------------------------------- the record's loads


def applied_force(record: Any, interface: Any) -> tuple[float | None, list[dict[str, Any]], str]:
    """(the peak force in N of the interface's use-case loads, the loads read, why there is none)."""
    ids = list(interface.functional.raw.get("loads") or ())
    loads = record.use_case.loads
    if not ids or intent.is_fact(loads):
        return None, [], "the interface names no use-case load, or the use case's loads are unknown"
    by_id = {load.id: load for load in loads}
    read: list[dict[str, Any]] = []
    for ident in ids:
        magnitude = by_id[ident].magnitude if ident in by_id else None
        if magnitude is None or not magnitude.known or magnitude.value.dimension != "force":
            return None, read, f"load {ident} has no known force magnitude"
        read.append({"load": ident, "newtons": magnitude.value.to("N").bounds[1]})
    return max(row["newtons"] for row in read), read, ""


def recorded_load_n(interface: Any) -> float | None:
    """The upper bound in N of the interface's known functional.contact.load, else None."""
    contact = interface.functional.raw.get("contact")
    fact = contact.get("load") if isinstance(contact, Mapping) else None
    if not isinstance(fact, Mapping) or fact.get("status") != "known":
        return None
    quantity = intent.quantity(fact["value"])
    return float(quantity.to("N").bounds[1]) if quantity.dimension == "force" else None


def contact_tolerance(interface: Any) -> tuple[float, str]:
    """(the effective contact tolerance in mm, its source): the larger of STOP_CONTACT_TOLERANCE_MM and the largest
    clearance of a fit the interface declares as `<size> <HOLE/SHAFT>` (ISO 286 through cad-fits). A declared fit
    without a size, or outside the tables, declares no clearance."""
    best, source = STOP_CONTACT_TOLERANCE_MM, "constant"
    for entry in (interface.raw.get("limits") or {}).values():
        text = str(entry.get("fit") or "").strip() if isinstance(entry, Mapping) else ""
        size, _, designation = text.partition(" ")
        try:
            result = fits.evaluate(Decimal(size), designation.strip())
        except (ArithmeticError, ValueError):
            continue  # no size, a malformed designation or a size outside the tables (cad-fits FitError is a ValueError)
        clearance = result.get("fit", {}).get("max_clearance_um", 0) / 1000.0
        if clearance > best:
            best, source = clearance, f"fit {text} clearance"
    return best, source


# --------------------------------------------------------------------------- one interface


@dataclass
class Assessment:
    interface: str
    outcome: str  # pass, fail, unknown or not_applicable
    reasons: list[str]
    reactions: dict[str, statics.Reaction]  # per joint limit that is a stop: "min" or "max"
    detail: dict[str, Any]


def end_geometry(chain: Any, definitions: Mapping[str, Any], pivot: Pivot, value: float) -> dict[str, Any]:
    """The two parts posed with the pivot at `value`, the pivot axis there, and the moving part's farthest reach."""
    values = chain.complete({pivot.joint.id: value})
    parent = chain.motions(values)[pivot.joint.parent]
    origin = parent.apply(pivot.joint.origin)
    tip = parent.apply(tuple(o + a for o, a in zip(pivot.joint.origin, pivot.joint.axis)))
    direction = sub(tip, origin)
    axis = scale(direction, 1.0 / math.sqrt(dot(direction, direction)))
    transforms = chain.transforms(values)
    shapes = {name: pose.placed(definitions[chain.pn(occurrence)].shape, transforms[occurrence])
              for name, occurrence in (("moving", pivot.moving), ("fixed", pivot.fixed))}
    return {"origin": origin, "axis": axis, "reach": extrema.reach(shapes["moving"], origin, axis), **shapes}


def assess_end(chain: Any, definitions: Mapping[str, Any], pivot: Pivot, label: str, tolerance: float) -> dict[str, Any]:
    """The geometry at one joint limit and, when the parts are within `tolerance` of each other there, the patch.
    At `min` the load pushes the joint down and the stop resists with a positive moment about the axis; at `max`
    the reverse."""
    value = pivot.joint.min if label == "min" else pivot.joint.max
    geometry = end_geometry(chain, definitions, pivot, value)
    gap = separation(geometry["moving"], geometry["fixed"])
    contact = gap <= tolerance + STOP_CONTACT_EPSILON_MM
    patch, volume, depth = None, 0.0, 0.0
    if gap <= CONTACT_MM:
        volume, depth = overlap_of(geometry["moving"], geometry["fixed"])
    if contact:
        faces_in_contact = contact_faces(geometry["fixed"], geometry["moving"], tolerance)
        patch = patch_of(faces_in_contact, geometry["origin"], geometry["axis"], 1.0 if label == "min" else -1.0)
    return {"label": label, "value": value, "touching": contact, "gap": gap, "patch": patch, "reach": geometry["reach"],
            "overlap_volume": volume, "overlap_depth": depth}


def patch_report(patch: statics.Patch | None) -> dict[str, Any] | None:
    if patch is None:
        return None
    return {"min_arm_mm": patch.min_arm, "max_arm_mm": patch.max_arm, "area_mm2": patch.area,
            "integral_arm_mm3": patch.first, "integral_arm_squared_mm4": patch.second}


def reaction_report(result: statics.Reaction) -> dict[str, Any]:
    return {"status": result.status, "moment_n_mm": result.moment_n_mm, "design_reaction_n": result.design_n,
            "rigid_lower_bound_n": result.rigid_lower_n, "uniform_pressure_n": result.uniform_n,
            "compliant_stop_n": result.compliant_n, "pin_load_bound_n": result.pin_load_n, "message": result.message}


def unresolved(if_id: str, outcome: str, reason: str, **detail: Any) -> Assessment:
    return Assessment(if_id, outcome, [reason], {}, {"outcome": outcome, "reason": reason, **detail})


def resolve_ends(chain: Any, interface: Any, if_id: str) -> tuple[list[str], Assessment | None]:
    """The occurrence ids of the interface's two parts, or the assessment that ends the check. A reference that names
    no occurrence is not a stop between two parts (not applicable). One that names two or more is ambiguous, and the
    stop it may be stays unassessed (unknown)."""
    ends = []
    for key in ("from", "to"):
        ref = interface.raw.get(key) or ""
        try:
            ends.append(chain.resolve(ref, f"{if_id} {key}: "))
        except pose.MotionError as error:
            if isinstance(ref, str) and not ref.startswith("@") and len(chain.inventory.of(ref)) > 1:
                return [], unresolved(if_id, "unknown", f"{if_id}: {error}; the stop it may be is not assessed")
            return [], unresolved(if_id, "not_applicable", f"{if_id} joins a part outside the posed assembly ({error}): not a stop")
    return ends, None


def assess_interface(if_id: str, record: Any, interface: Any, chain: Any, definitions: Mapping[str, Any]) -> Assessment:
    """Whether the interface is a stop comes first, and only a stop needs a force."""
    ends, stopped = resolve_ends(chain, interface, if_id)
    if stopped is not None:
        return stopped
    pivot, why = pivot_between(chain, ends[0], ends[1])
    if pivot is None:
        return unresolved(if_id, "not_applicable" if why.startswith("no movable") else "unknown", f"{if_id}: {why}")
    tolerance, source = contact_tolerance(interface)
    states = [assess_end(chain, definitions, pivot, label, tolerance) for label in ("min", "max")]
    detail: dict[str, Any] = {"pivot": pivot.joint.id, "moving": chain.ref(pivot.moving), "fixed": chain.ref(pivot.fixed),
                              "contact_tolerance": {"mm": tolerance, "source": source}, "ends": {}}
    for state in states:
        detail["ends"][state["label"]] = {"joint_value": state["value"], "touching": state["touching"],
                                          "gap_mm": state["gap"], "overlap_volume_mm3": state["overlap_volume"],
                                          "overlap_depth_mm": state["overlap_depth"],
                                          "farthest_point_from_axis_mm": state["reach"], "patch": patch_report(state["patch"])}
    stops = [state for state in states if state["touching"]]
    if not stops:
        gaps = ", ".join(f"{state['label']} {state['gap']:.4g} mm" for state in states)
        reason = (f"{if_id}: the parts are not in contact at either limit of {pivot.joint.id} "
                  f"(gaps: {gaps}; tolerance {tolerance:.4g} mm): not a stop")
        return Assessment(if_id, "not_applicable", [reason], {}, {"outcome": "not_applicable", "reason": reason, **detail})
    overlapping = [state for state in stops if state["overlap_volume"] > 0.0]
    if overlapping:
        where = " and ".join(f"at the {state['label']} limit ({state['value']:g} {pivot.joint.unit})" for state in overlapping)
        depth = max(state["overlap_depth"] for state in overlapping)
        reason = f"{if_id}: the parts overlap by {depth:.3g} mm {where}: a design defect, not a stop"
        return Assessment(if_id, "unknown", [reason], {}, {"outcome": "unknown", "reason": reason, **detail})
    flat = [state["label"] for state in stops if state["patch"] is None]
    if flat:
        reason = (f"{if_id}: at the {' and '.join(flat)} limit of {pivot.joint.id} the parts are within {tolerance:.4g} mm, "
                  "but no parallel opposite-facing planar faces are that close, so no contact patch can be taken")
        return Assessment(if_id, "unknown", [reason], {}, {"outcome": "unknown", "reason": reason, **detail})
    force, loads, why = applied_force(record, interface)
    if force is None:
        reason = f"{if_id} is a stop at the {' and '.join(state['label'] for state in stops)} limit of {pivot.joint.id}, but {why}"
        return Assessment(if_id, "unknown", [reason], {}, {"outcome": "unknown", "reason": reason, **detail})
    detail.update(loads=loads, peak_force_n=force)
    return judge_stops(if_id, stops, force, recorded_load_n(interface), detail)


def judge_stops(if_id: str, stops: Sequence[Mapping[str, Any]], force: float, recorded: float | None,
                detail: dict[str, Any]) -> Assessment:
    """The reaction at each stop against the recorded contact load, and the worst outcome."""
    detail["recorded_contact_load_n"] = recorded
    reactions: dict[str, statics.Reaction] = {}
    for state in stops:
        reactions[state["label"]] = statics.reaction(force, state["reach"], state["patch"], recorded)
        detail["ends"][state["label"]]["reaction"] = reaction_report(reactions[state["label"]])
    seen = {r.outcome for r in reactions.values()}
    outcome = "fail" if "fail" in seen else "unknown" if "unknown" in seen else "pass"
    detail["outcome"] = outcome
    return Assessment(if_id, outcome, [r.message for r in reactions.values() if r.outcome != "pass"], reactions, detail)


# --------------------------------------------------------------------------- findings and packet


def slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")


def unknown_finding(entry: Assessment, base: Mapping[str, Any]) -> dict[str, Any]:
    reason = "; ".join(entry.reasons)
    return {"id": f"{slug(entry.interface)}_stop_reaction_unknown", "code": "engineering.stop_reaction_unknown", **base,
            "severity": "major", "outcome": "unknown", "measured": {"status": "unknown", "reason": reason},
            "criterion": {"status": "unknown", "reason": reason}, "uncertainty": reason,
            "consequence": f"the reaction {entry.interface} carries at its stop is not established",
            "remedy": "record the joint between its two parts, their loads and a modelled contact at a joint limit, then rerun"}


def reaction_finding(entry: Assessment, label: str, result: statics.Reaction, base: Mapping[str, Any]) -> dict[str, Any]:
    where = f"the {label} limit of the pivot"
    ident = f"{slug(entry.interface)}_{label}_stop_reaction_{result.status}"
    if result.status == statics.UNBOUNDED:
        estimates = f"uniform pressure {result.uniform_n or 0:.4g} N and a compliant stop {result.compliant_n or 0:.4g} N"
        return {"id": ident, "code": "engineering.stop_reaction_unbounded", **base, "severity": result.severity,
                "outcome": "unknown", "measured": {"text": result.message},
                "criterion": {"status": "unknown", "reason": "a rigid stop with its nearest edge on the pivot axis has no bounded reaction"},
                "uncertainty": f"{where}: {result.message}",
                "consequence": (f"the load {entry.interface} carries at {where} has no design value, so the stop, its pin and "
                                f"the cheeks cannot be sized ({estimates} are estimates, not bounds)"),
                "remedy": REMEDY_UNBOUNDED}
    recorded = entry.detail["recorded_contact_load_n"]
    return {"id": ident, "code": "engineering.stop_reaction_understated", **base, "severity": result.severity,
            "outcome": "fail", "measured": {"value": recorded, "units": "N"},
            "criterion": {"property": "contact_load", "operator": ">=", "value": result.design_n, "units": "N",
                          "source": dict(RULE_SOURCE)},
            "uncertainty": f"{where}: {result.message}",
            "consequence": f"{entry.interface} is sized for {recorded:.4g} N but its stop can carry {result.design_n:.4g} N at {where}",
            "remedy": ("record the design reaction as functional.contact.load and size the stop, pin and cheeks for the pivot "
                       "load bound, or model the contact in an FEA contact case approved in a design record")}


def findings_of(entry: Assessment, obligation: str) -> list[dict[str, Any]]:
    if entry.outcome in ("pass", "not_applicable"):
        return []
    subject = {"interface": entry.interface}
    base = {"obligation": obligation, "subject": subject, "method": {"name": METHOD, "tool": PRODUCER},
            "recheck": {"families": [FAMILY], "scope": [subject], "earliest_stage": "layout"}}
    if not entry.reactions:
        return [unknown_finding(entry, base)]
    return [reaction_finding(entry, label, result, base) for label, result in entry.reactions.items() if result.outcome != "pass"]


def outcome_row(entry: Assessment, obligation: str, mine: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    row: dict[str, Any] = {"obligation": obligation, "outcome": entry.outcome, "level": "simulation_supported",
                           "method": METHOD, "coverage": entry.reasons[0] if entry.outcome == "not_applicable" else COVERAGE}
    if mine:
        row["findings"] = [f["id"] for f in mine]
    if entry.outcome == "unknown":
        row["uncertainty"] = "; ".join(entry.reasons)
    return row


def producer_code_hashes() -> dict[str, str]:
    """sha256 of every file whose content decides this producer's packet: its own code and the modules and data it
    loads, so an edit to any of them blocks reuse of a published bundle. A hand-kept list, like
    fea_static's _producer_code_hashes. #140's code_shas helper takes over once it lands."""
    here = Path(__file__).resolve()
    paths = [here, here.with_name("stop_statics.py"), here.with_name("stop_extrema.py"), Path(pose.__file__), pose.COUPLING_PATH,
             pose.ASSEMBLY_DATA_PATH, pose.YAML_LOADER_PATH, Path(pose.LAYOUT.__file__), Path(check_packet.__file__),
             Path(intent.__file__), Path(fits.__file__), fits.DATA]
    return {path.resolve().relative_to(SKILLS).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def file_hashes(record_hw: Path, names: Iterable[str]) -> dict[str, str]:
    return {name.replace(".", "_"): hashlib.sha256((record_hw / name).read_bytes()).hexdigest()
            for name in names if (record_hw / name).is_file()}


def assess(args: argparse.Namespace, version: str | None, record_hw: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the packet, the evidence detail) of one run."""
    record = intent.load_record(record_hw)
    expected = [o for o in intent.required_checks(record) if o.family == FAMILY]
    dependencies = file_hashes(record_hw, ("assembly.yaml", "interfaces.yaml", "product.yaml"))
    if version is not None:
        dependencies["version_manifest"] = pose.input_sha256(args.version_dir)
    detail: dict[str, Any] = {"interfaces": {}, "limits": [COVERAGE]}
    outcomes: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    if expected:
        _, chain, definitions = pose.load_inputs(args)
        with pose.stdout_to_stderr():
            for obligation in expected:
                if_id = obligation.subject["interface"]
                entry = assess_interface(if_id, record, record.interfaces[if_id], chain, definitions)
                mine = findings_of(entry, obligation.id)
                findings += mine
                outcomes.append(outcome_row(entry, obligation.id, mine))
                detail["interfaces"][if_id] = entry.detail
    return check_packet.build(record, expected, PRODUCER, outcomes, findings, dependencies), detail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stop reaction derived from the modelled contact")
    pose.add_geometry_arguments(parser)
    parser.add_argument("--replace", action="store_true", help="retain and replace a published packet with another recipe")
    parser.add_argument("--out", type=Path, help="scratch JSON path, only under .cad-work")
    args = parser.parse_args(argv)
    try:
        version, record_hw = None, args.hw
        if args.version:
            version, version_dir, record_hw = LAYOUT.built_version(args.hw, args.version)
            args.version, args.version_dir, args.record_hw = version, version_dir, record_hw
        result, detail = assess(args, version, record_hw)
        recipe = {"options": {"attach": list(args.attach)}, "dependencies": result["dependencies"],
                  "producer_code_sha256": producer_code_hashes()}
        output, status = check_packet.emit(LAYOUT, args.hw, version, SUBJECT, PRODUCER, result, detail, "stop-reaction.json",
                                           recipe, out=args.out, replace=args.replace, record_version=intent.RECORD_VERSION)
        json.dump(output, sys.stdout, indent=2)
        sys.stdout.write("\n")
        if status != "pass":
            print(f"stop_reaction: {status}", file=sys.stderr)
        return 0 if status == "pass" else 1
    except (check_packet.PacketError, pose.MotionError, LAYOUT.LayoutError, intent.IntentError) as error:
        print(f"stop_reaction: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
