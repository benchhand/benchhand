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
at which the two parts are at zero distance is a stop.

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
from pathlib import Path
from typing import Any

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
check_packet = _load_module("cad_record_check_packet", SKILLS / "cad-record" / "scripts" / "check_packet.py")
intent = _load_module("cad_record_manufacturing_intent", SKILLS / "cad-record" / "scripts" / "manufacturing_intent.py")
LAYOUT = pose.LAYOUT

PRODUCER = "cad-analysis stop_reaction.py"
SUBJECT = "stop-reaction"
FAMILY = "engineering.stop_reaction"
CONTACT_MM = 1e-4  # parts at or under this distance touch; planes this close are coincident
NORMAL_TOL = 1e-6  # |n1 . n2 + 1| under this: opposite-facing faces
MIN_AREA_MM2 = 1e-6
CURVE_SAMPLES = 64  # points per curved edge when its outline is read
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


def distance_from_axis(point: Vec, origin: Vec, axis: Vec) -> float:
    return math.sqrt(dot(c := cross(sub(point, origin), axis), c))


# --------------------------------------------------------------------------- geometry (OCP)


def xyz(point: Any) -> Vec:
    return (point.X(), point.Y(), point.Z())


def faces(shape: Any) -> list[Any]:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    found, explorer = [], TopExp_Explorer(shape, TopAbs_FACE)
    cast = getattr(TopoDS, "Face_s", None) or TopoDS.Face
    while explorer.More():
        found.append(cast(explorer.Current()))
        explorer.Next()
    return found


def outline_points(shape: Any) -> list[Vec]:
    """The vertices of `shape` and CURVE_SAMPLES points along each of its curved edges."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Line
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    points = [xyz(p) for p in pose._vertex_points(shape)]
    cast = getattr(TopoDS, "Edge_s", None) or TopoDS.Edge
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        curve = BRepAdaptor_Curve(cast(explorer.Current()))
        if curve.GetType() != GeomAbs_Line:
            first, last = curve.FirstParameter(), curve.LastParameter()
            points += [xyz(curve.Value(first + (last - first) * i / CURVE_SAMPLES)) for i in range(CURVE_SAMPLES + 1)]
        explorer.Next()
    return points


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


def touching(first: Any, second: Any) -> bool:
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    extrema = BRepExtrema_DistShapeShape(first, second)
    if not extrema.IsDone():
        raise pose.MotionError("distance computation failed")
    return bool(extrema.Value() <= CONTACT_MM)


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
    return [face for face in faces(common.Shape()) if face_area(face) > MIN_AREA_MM2]


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


def contact_faces(fixed: Any, moving: Any) -> list[tuple[Any, Vec]]:
    """(overlap face, outward normal of the fixed part's face) for each pair of touching, opposite-facing planar faces."""
    moving_planes = [(face, plane_of(face)) for face in faces(moving)]
    found = []
    for fixed_face in faces(fixed):
        fixed_plane = plane_of(fixed_face)
        if fixed_plane is None:
            continue
        for moving_face, moving_plane in moving_planes:
            if (moving_plane is not None and abs(dot(fixed_plane[1], moving_plane[1]) + 1.0) <= NORMAL_TOL
                    and abs(dot(sub(moving_plane[0], fixed_plane[0]), fixed_plane[1])) <= CONTACT_MM):
                found += [(face, fixed_plane[1]) for face in overlap(fixed_face, moving_face)]
    return found


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
        arms += [dot(gradient, sub(point, origin)) for point in outline_points(face)]
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
    reach = max(distance_from_axis(point, origin, axis) for point in outline_points(shapes["moving"]))
    return {"origin": origin, "axis": axis, "reach": reach, **shapes}


def assess_end(chain: Any, definitions: Mapping[str, Any], pivot: Pivot, label: str) -> dict[str, Any]:
    """The geometry at one joint limit and, when the parts touch there, the patch. At `min` the load pushes the
    joint down and the stop resists with a positive moment about the axis; at `max` the reverse."""
    value = pivot.joint.min if label == "min" else pivot.joint.max
    geometry = end_geometry(chain, definitions, pivot, value)
    contact = touching(geometry["moving"], geometry["fixed"])
    patch = None
    if contact:
        faces_in_contact = contact_faces(geometry["fixed"], geometry["moving"])
        patch = patch_of(faces_in_contact, geometry["origin"], geometry["axis"], 1.0 if label == "min" else -1.0)
    return {"label": label, "value": value, "touching": contact, "patch": patch, "reach": geometry["reach"]}


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


def assess_interface(if_id: str, record: Any, interface: Any, chain: Any, definitions: Mapping[str, Any]) -> Assessment:
    ends = []
    for key in ("from", "to"):
        try:
            ends.append(chain.resolve(interface.raw.get(key) or "", f"{if_id} {key}: "))
        except pose.MotionError as error:
            return unresolved(if_id, "not_applicable", f"{if_id} joins a part outside the posed assembly ({error}): not a stop")
    pivot, why = pivot_between(chain, ends[0], ends[1])
    if pivot is None:
        return unresolved(if_id, "not_applicable" if why.startswith("no movable") else "unknown", f"{if_id}: {why}")
    force, loads, why = applied_force(record, interface)
    if force is None:
        return unresolved(if_id, "unknown", f"{if_id}: {why}", pivot=pivot.joint.id)
    states = [assess_end(chain, definitions, pivot, label) for label in ("min", "max")]
    detail: dict[str, Any] = {"pivot": pivot.joint.id, "moving": chain.ref(pivot.moving), "fixed": chain.ref(pivot.fixed),
                              "loads": loads, "peak_force_n": force, "ends": {}}
    for state in states:
        detail["ends"][state["label"]] = {"joint_value": state["value"], "touching": state["touching"],
                                          "farthest_point_from_axis_mm": state["reach"], "patch": patch_report(state["patch"])}
    stops = [state for state in states if state["touching"]]
    if not stops:
        reason = f"{if_id}: the parts are not in contact at either limit of {pivot.joint.id}, so no stop is modelled"
        return Assessment(if_id, "unknown", [reason], {}, {"outcome": "unknown", "reason": reason, **detail})
    recorded = recorded_load_n(interface)
    detail["recorded_contact_load_n"] = recorded
    reactions: dict[str, statics.Reaction] = {}
    for state in stops:
        patch = state["patch"] or statics.Patch(0.0, 0.0, 0.0, 0.0, 0.0)  # touching faces that are not planar: no arm
        reactions[state["label"]] = statics.reaction(force, state["reach"], patch, recorded)
        detail["ends"][state["label"]]["reaction"] = reaction_report(reactions[state["label"]])
    states_seen = {r.outcome for r in reactions.values()}
    outcome = "fail" if "fail" in states_seen else "unknown" if "unknown" in states_seen else "pass"
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
        recipe = {"options": {"attach": list(args.attach)}, "dependencies": result["dependencies"]}
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
