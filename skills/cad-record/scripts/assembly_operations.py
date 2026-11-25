# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = []
# ///
"""Assembly operations: the one normalizer of a record's installation, closure, fastening, adjustment,
inspection and service operations, and their pure order, closure and joint checks. Stdlib only, no
file reads and no CAD kernel: callers pass the parsed assembly.yaml mapping or a normalized record.

Authority. Operations live in assembly.yaml top-level `operations` (a list, or an unknown fact).
`normalize` reads only that; a `drawing.operations` list is refused (`intent.dual_authority`), and no
drawing, guide or check reads the operations any other way.

Order model. Record order is the assembly sequence; `predecessors` are constraints that sequence must
satisfy. A part an installing operation (press_fit, heat_set, retaining_ring, insert, fasten, join,
close) puts in place is absent before it and present after it until a disassemble operation removes
it; the one occurrence no operation installs is present from the start (the base: with an inventory, the first
such occurrence the sequence names, as the record designates none). Any other uninstalled occurrence fails
`assembly.never_installed`.

Sub-builds. An operation with `sub_build` belongs to a bench sub-build (a subassembly id, or a new id), with
its own base, state and closure, judged by the same rules. An `install` operation puts the finished sub-build
into the main build as one unit; it fails `assembly.order` when the sub-build is unknown, unfinished or
installed twice, an occurrence two builds use fails it, and a sub-build no install puts in place fails it.
A `hold` keeps its part in a fixture for a duration in an orientation, judged against its own build's state;
its cure time and temperature count only as known facts with a datasheet source, and the hold must last the
cure time. A close operation encloses its
`closes` parts: no later operation reaches them. `check_order` judges exactly this declared sequence:
an operation whose receiving part is absent, whose part is enclosed or installed twice, or whose
predecessor comes later or is missing fails `assembly.order`. Whether the part and the tools can
physically travel to their positions is `assembly.access`, a geometric check (cad-motion access.py)
that this module only plans: `access_plan` lists, per operation, the moving part, its receiver-local
direction or declared insertion path, the tools and the parts present at that state.

Paths. An installing operation may declare `path`: the part's offsets from its installed pose (mm,
receiving part's frame, installed orientation kept), from `start` through `via` to the installed
pose, whose last leg must arrive along `direction`. Without one the part travels the default straight
line along `direction`, and access reports that default.

Tools. A plain `tools` entry is a display name: its reach and envelope stay unknown. A
`tool_access` entry may name a capability profile, an approach (receiver-local, like `direction`),
a reach and an envelope; an envelope fact whose source carries an artifact (a STEP solid of the tool
and holder in the operated part's model frame at the engaged pose) is what a geometric check uses.

Joints. `check_joints` judges the joint evidence of fastening, insert, press-fit, heat-set,
retaining-ring and joining operations, one `assembly.joint@op:<id>` outcome each: retention needs a
sourced fact, a fastener its engagement and torque, a fastener, insert or heat-set its edge distance
against its sourced criterion (any joint that declares one is judged), and the host of an insert,
heat-set or press fit its sourced service temperature limit against the use-case temperature (a host
part or material profile that does not resolve keeps the joint unknown, and a limit that is an estimate
or has no qualifying source can fail it but never pass it). A limit exceeded or an edge
distance short of its criterion is a failure; retention evidence is what supports a joint.
"""

from __future__ import annotations

import math
import re
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeGuard

Vec = tuple[float, float, float]

KINDS = ("press_fit", "heat_set", "retaining_ring", "insert", "fasten", "join", "close", "adjust", "inspect", "disassemble",
         "install", "hold")
INSTALLING = frozenset(("press_fit", "heat_set", "retaining_ring", "insert", "fasten", "join", "close", "install"))
JOINING = frozenset(("press_fit", "heat_set", "retaining_ring", "insert", "fasten", "join"))
INTERFACE_LIMIT_KINDS = frozenset(("press_fit", "heat_set", "retaining_ring"))  # the fit, insert or ring comes from the interface limit of this kind
ACCEPTANCE = {"seated": 2, "standoff": 2, "flush": 2, "engaged": 1, "torqued": 1, "adjusted": 1, "inspected": 1}
GEOMETRIC_ACCEPTANCE = frozenset(("seated", "standoff", "flush", "engaged"))  # measurable on the assembled model
REQUIRED = {  # schema: assembly.schema.json#/$defs/assembly_operation allOf
    "press_fit": ("into", "interface", "direction", "acceptance"), "heat_set": ("into", "interface", "direction", "acceptance"),
    "retaining_ring": ("into", "interface", "direction", "acceptance"), "insert": ("into", "direction", "acceptance"),
    "fasten": ("into", "direction", "acceptance"), "join": ("into", "direction", "acceptance"), "close": ("into", "closes"),
    "adjust": ("acceptance",), "inspect": ("acceptance",), "disassemble": ("into", "direction"),
    "install": ("into", "direction", "acceptance"), "hold": ("duration", "fixture", "orientation"),
}
SUB_BUILD_ID = re.compile(r"^[a-z][a-z0-9_-]*$")  # schema: assembly.schema.json#/$defs/sub_build_id
TIME_H = {"s": 1 / 3600, "min": 1 / 60, "h": 1.0, "d": 24.0}  # manufacturing.schema.json time units, in hours
CURE_SOURCES = frozenset(("datasheet",))  # a cure time or temperature counts only from the adhesive's datasheet
QUALIFYING_SOURCES = frozenset(("test", "supplier", "datasheet", "standard", "calibration"))
HOST_TEMPERATURE_KINDS = frozenset(("heat_set", "insert", "press_fit"))
TEMPERATURE_LIMITS = ("max_service_temperature", "heat_deflection_temperature")
EDGE_DISTANCE_KINDS = frozenset(("fasten", "insert", "heat_set"))  # hole-based joints: an edge distance is required
LENGTH_MM = {"mm": 1.0, "um": 1e-3, "m": 1e3}  # manufacturing_intent.UNITS' lengths, in mm
PATH_ALIGNMENT = 1e-6  # sine of the largest angle between a path's last leg and `direction`
JOINT_METHOD = ("declared joint evidence: sourced retention, fastener engagement and torque, edge distance against its "
                "criterion, host service temperature limit (cad-record assembly_operations)")
JOINT_COVERAGE = ("the declared facts and material profiles only: no joint strength, pull-out, creep, fatigue, preload "
                  "or cure is computed; retention, engagement and torque count only with a test, supplier, datasheet, "
                  "standard or calibration source")
RULE_SOURCE = types.MappingProxyType({
    "kind": "policy", "reference": "cad-record assembly_operations order and closure rules",
    "origin": "benchhand", "document": "skills/cad-record/scripts/assembly_operations.py"})
ORDER_METHOD = "declared assembly sequence: predecessors, parts present, closure state (cad-record assembly_operations)"
ORDER_COVERAGE = ("the declared sequence only: the receiving part present, the part not enclosed or installed twice, "
                  "predecessors earlier; part paths and tool reach are assembly.access, a geometric check, not covered")


class OperationsError(ValueError):
    """Operations that cannot be normalized; `code` is intent.dual_authority (drawing.operations) or intent.schema."""

    def __init__(self, message: str, code: str = "intent.schema") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Tool:
    """A tool that must reach the operation: a display name (`declared` False: plain `tools`) or a
    `tool_access` entry with its optional capability, approach, reach and envelope facts."""

    name: str
    declared: bool
    capability: str | None = None
    approach: Vec | None = None
    reach: Mapping[str, Any] | None = None
    envelope: Mapping[str, Any] | None = None

    @property
    def reach_known(self) -> bool:
        return isinstance(self.reach, Mapping) and self.reach.get("status") == "known"

    @property
    def envelope_artifact(self) -> tuple[str, str] | None:
        """(path below hw/, sha256) of the solid a known envelope cites, else None: a name has no geometry."""
        if not isinstance(self.envelope, Mapping) or self.envelope.get("status") != "known":
            return None
        for source in self.envelope.get("sources") or ():
            if isinstance(source, Mapping) and source.get("artifact") and source.get("sha256"):
                return source["artifact"], source["sha256"]
        return None


@dataclass(frozen=True)
class AssemblyOperation:
    id: str
    index: int  # position in record order
    kind: str
    part: str  # a part reference: PN or @occurrence
    into: str | None
    interface: str | None
    direction: Vec | None  # unit vector, in the receiving part's model frame
    acceptance: str | None
    features: tuple[str, ...]
    predecessors: tuple[str, ...]
    closes: tuple[str, ...]
    tools: tuple[Tool, ...]
    consumables: tuple[str, ...]
    verify_joint: str | None
    engagement: Mapping[str, Any] | None
    torque: Mapping[str, Any] | None
    retention: Mapping[str, Any] | None
    service_access: Mapping[str, Any] | None
    inspection: str | None
    raw: Mapping[str, Any]
    edge_distance: Mapping[str, Any] | None = None  # {criterion, distance} as recorded
    path: tuple[Vec, ...] | None = None  # declared insertion path: start, then via, mm offsets (receiver frame)
    sub_build: str | None = None  # the bench sub-build this operation belongs to; None for the main build
    duration: Mapping[str, Any] | None = None  # hold: how long the part stays in its fixture (a quantity fact)
    fixture: str | None = None  # hold: what holds the part
    orientation: Vec | None = None  # hold: the part's up direction during the hold, in its own model frame
    cure: Mapping[str, Any] | None = None  # hold: {time, temperature} quantity facts from the adhesive's datasheet

    @property
    def installs(self) -> bool:
        return self.kind in INSTALLING

    @property
    def removes(self) -> bool:
        return self.kind == "disassemble"

    @property
    def geometric_acceptance(self) -> bool:
        return self.acceptance in GEOMETRIC_ACCEPTANCE

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tools)


@dataclass(frozen=True)
class Operations:
    """The normalized operations of one record. `authority` names the list they came from; `unknown`
    holds the reason when the record states them unknown (or holds none)."""

    authority: str  # operations or none
    items: tuple[AssemblyOperation, ...]
    unknown: str | None = None

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.items)


# --------------------------------------------------------------------------- normalization


def normalize(assembly: Mapping[str, Any]) -> Operations:
    """The operations of a parsed assembly.yaml: top-level `operations`;
    OperationsError when `drawing.operations` is present or an entry is malformed."""
    if not isinstance(assembly, Mapping):
        raise OperationsError("assembly.yaml must be a mapping")
    drawing = assembly.get("drawing")
    legacy = drawing.get("operations") if isinstance(drawing, Mapping) else None
    if legacy is not None:
        raise OperationsError("assembly.yaml holds installation operations in drawing.operations: "
                              "keep them in top-level operations only", "intent.dual_authority")
    if "operations" in assembly:
        data = assembly["operations"]
        if isinstance(data, Mapping) and data.get("status") in ("unknown", "not_applicable"):
            return Operations("operations", (), str(data.get("reason") or f"operations are {data['status']}"))
        return Operations("operations", _items(data, "operations"))
    return Operations("none", (), "assembly.yaml records no operations")


def from_record(record: Any) -> Operations:
    """The operations of a normalized manufacturing_intent Record (its assembly_operations)."""
    data = record.assembly_operations
    if not isinstance(data, (list, tuple)):  # an unknown or not_applicable Fact
        return Operations("operations", (), data.reason or f"operations are {data.status}")
    return Operations("operations", _items(_thaw(data), "operations"))


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def _items(data: Any, where: str) -> tuple[AssemblyOperation, ...]:
    if not isinstance(data, list):
        raise OperationsError(f"{where} must be a list, or an unknown fact with its reason")
    items = tuple(_operation(raw, index, f"{where}/{index}") for index, raw in enumerate(data))
    ids = [item.id for item in items]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise OperationsError(f"{where}: operation ids repeat: {', '.join(duplicates)}")
    return items


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperationsError(f"{where} must be a non-empty string")
    return value


def _point(value: Any, where: str) -> Vec:
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value)):
        raise OperationsError(f"{where} must be three finite numbers")
    return (float(value[0]) + 0.0, float(value[1]) + 0.0, float(value[2]) + 0.0)


def _vector(value: Any, where: str) -> Vec:
    point = _point(value, where)
    length = math.hypot(*point)
    if length < 1e-9:
        raise OperationsError(f"{where} must not be zero")
    return (point[0] / length + 0.0, point[1] / length + 0.0, point[2] / length + 0.0)


def _path(raw: Mapping[str, Any], kind: str, direction: Vec | None, where: str) -> tuple[Vec, ...] | None:
    """The declared insertion path's points, start then via (the installed pose, the origin, is not
    listed): legs of non-zero length, the last one arriving along `direction`."""
    data = raw.get("path")
    if data is None:
        return None
    at = f"{where}/path"
    if kind not in INSTALLING or direction is None:
        raise OperationsError(f"{at}: only an installing operation with a direction has an insertion path")
    if not isinstance(data, Mapping) or "start" not in data or not isinstance(data.get("via") or [], list):
        raise OperationsError(f"{at} needs a start and, optionally, a list of via points")
    points = (_point(data["start"], f"{at}/start"),
              *(_point(p, f"{at}/via/{i}") for i, p in enumerate(data.get("via") or [])))
    for number, (a, b) in enumerate(zip(points, (*points[1:], (0.0, 0.0, 0.0)))):
        if math.dist(a, b) < 1e-9:
            raise OperationsError(f"{at}: leg {number + 1} has zero length")
    last = points[-1]
    length = math.hypot(*last)
    cross = (last[1] * direction[2] - last[2] * direction[1], last[2] * direction[0] - last[0] * direction[2],
             last[0] * direction[1] - last[1] * direction[0])
    if -sum(a * b for a, b in zip(last, direction)) <= 0.0 or math.hypot(*cross) > PATH_ALIGNMENT * length:
        raise OperationsError(f"{at}: the last leg must arrive along direction")
    return points


def _names(raw: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    values = raw.get(key) or []
    if not isinstance(values, list):
        raise OperationsError(f"{where}/{key} must be a list")
    return tuple(_text(value, f"{where}/{key}") for value in values)


def _fact(raw: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    value = raw.get(key)
    return value if isinstance(value, Mapping) else None


def _bench(raw: Mapping[str, Any], kind: str, where: str) -> tuple[str | None, Mapping[str, Any] | None]:
    """The operation's sub-build and cure block, checked: an install names a sub-build and belongs to the main
    build, and only a hold has a duration, fixture, orientation or cure."""
    sub_build = raw.get("sub_build")
    if sub_build is not None and (not isinstance(sub_build, str) or not SUB_BUILD_ID.match(sub_build)):
        raise OperationsError(f"{where}/sub_build must be a sub-build id (lower case, digits, - or _)")
    if kind == "install":
        if not isinstance(raw.get("part"), str) or not SUB_BUILD_ID.match(raw["part"]):
            raise OperationsError(f"{where}/part: an install operation installs a sub-build: name its id")
        if sub_build is not None:
            raise OperationsError(f"{where}: an install operation belongs to the main build, not to sub-build {sub_build}")
    for key in ("duration", "fixture", "orientation", "cure"):
        if key in raw and kind != "hold":
            raise OperationsError(f"{where}/{key}: only a hold operation has a {key}")
    cure = raw.get("cure")
    if cure is not None and (not isinstance(cure, Mapping) or "time" not in cure or set(cure) - {"time", "temperature"}):
        raise OperationsError(f"{where}/cure needs a time and, optionally, a temperature")
    return sub_build, cure


def _operation(raw: Any, index: int, where: str) -> AssemblyOperation:
    if not isinstance(raw, Mapping):
        raise OperationsError(f"{where} must be a mapping")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise OperationsError(f"{where}/kind must be one of {', '.join(KINDS)}")
    missing = [key for key in REQUIRED[kind] if key not in raw]
    if missing:
        raise OperationsError(f"{where}: a {kind} operation needs {', '.join(missing)}")
    acceptance = raw.get("acceptance")
    features: tuple[str, ...] = ()
    if acceptance is not None:
        if not isinstance(acceptance, Mapping) or acceptance.get("kind") not in ACCEPTANCE:
            raise OperationsError(f"{where}/acceptance needs kind {'|'.join(ACCEPTANCE)}")
        names = acceptance.get("features")
        if not isinstance(names, list) or not 1 <= len(names) <= 2 or not all(isinstance(f, str) and f for f in names):
            raise OperationsError(f"{where}/acceptance names one or two features")
        wanted = ACCEPTANCE[acceptance["kind"]]
        if acceptance["kind"] in GEOMETRIC_ACCEPTANCE and len(names) != wanted:
            raise OperationsError(f"{where}/acceptance {acceptance['kind']} names {wanted} feature(s)")
        features = tuple(names)
    tools = [Tool(name, declared=False) for name in _names(raw, "tools", where)]
    for number, entry in enumerate(raw.get("tool_access") or []):
        at = f"{where}/tool_access/{number}"
        if not isinstance(entry, Mapping):
            raise OperationsError(f"{at} must be a mapping")
        approach = entry.get("approach")
        tools.append(Tool(_text(entry.get("tool"), f"{at}/tool"), declared=True, capability=entry.get("capability"),
                          approach=None if approach is None else _vector(approach, f"{at}/approach"),
                          reach=_fact(entry, "reach"), envelope=_fact(entry, "envelope")))
    joint = raw.get("verify_joint")
    sub_build, cure = _bench(raw, kind, where)
    if "edge_distance" in raw and kind not in JOINING:
        raise OperationsError(f"{where}/edge_distance: a {kind} operation makes no joint")
    direction = None if raw.get("direction") is None else _vector(raw["direction"], f"{where}/direction")
    return AssemblyOperation(
        id=_text(raw.get("id"), f"{where}/id"), index=index, kind=kind, part=_text(raw.get("part"), f"{where}/part"),
        into=None if raw.get("into") is None else _text(raw["into"], f"{where}/into"),
        interface=None if raw.get("interface") is None else _text(raw["interface"], f"{where}/interface"),
        direction=direction,
        acceptance=None if acceptance is None else acceptance["kind"], features=features,
        predecessors=_names(raw, "predecessors", where), closes=_names(raw, "closes", where), tools=tuple(tools),
        consumables=_names(raw, "consumables", where),
        verify_joint=None if joint is None else _text(joint, f"{where}/verify_joint"),
        engagement=_fact(raw, "engagement"), torque=_fact(raw, "torque"), retention=_fact(raw, "retention"),
        service_access=_fact(raw, "service_access"),
        inspection=None if raw.get("inspection") is None else _text(raw["inspection"], f"{where}/inspection"),
        raw=types.MappingProxyType(dict(raw)), edge_distance=_fact(raw, "edge_distance"),
        path=_path(raw, kind, direction, where), sub_build=sub_build, duration=_fact(raw, "duration"),
        fixture=None if raw.get("fixture") is None else _text(raw["fixture"], f"{where}/fixture"),
        orientation=None if raw.get("orientation") is None else _vector(raw["orientation"], f"{where}/orientation"),
        cure=None if cure is None else types.MappingProxyType(dict(cure)))


# --------------------------------------------------------------------------- sequence and presence


@dataclass(frozen=True)
class Step:
    """One operation in sequence with the state of its own build just before it: the occurrences present and
    the enclosed ones (occurrence -> the close operation that enclosed it). `part` and `into` are
    resolved occurrence ids (the raw references without an inventory; an install's part is its sub-build id).
    `build` is the operation's sub-build, None for the main build."""

    operation: AssemblyOperation
    part: str
    into: str | None
    closes: tuple[str, ...]
    present: frozenset[str]
    closed: Mapping[str, str]
    build: str | None = None


@dataclass(frozen=True)
class SubBuild:
    """A bench sub-build: its own members, base and state, installed into the main build as one unit."""

    id: str
    members: frozenset[str]  # the occurrences its operations name, plus the subassembly of the same id
    ground: str | None  # its one base: the first uninstalled member its operations name
    floating: frozenset[str]  # with an inventory: its other members no operation installs
    last: int | None  # record index of its last operation, None when no operation belongs to it
    present: frozenset[str]  # its members in place once its own operations are done
    closed: Mapping[str, str]  # the members it encloses, and the close operation that does it


@dataclass(frozen=True)
class AssemblyState:
    steps: tuple[Step, ...]
    base: frozenset[str]  # present from the start of the main build: every main occurrence no operation installs
    unresolved: Mapping[str, str] = field(default_factory=dict)  # operation id -> why its references do not resolve
    ground: str | None = None  # with an inventory: the main build's one base, the first uninstalled occurrence it names
    floating: frozenset[str] = frozenset()  # with an inventory: the other main occurrences no operation installs
    sub_builds: Mapping[str, SubBuild] = field(default_factory=dict)
    shared: Mapping[str, tuple[str | None, ...]] = field(default_factory=dict)  # occurrence -> the builds claiming it


def sequence(operations: Operations, inventory: Any = None, existing: frozenset[str] = frozenset()) -> AssemblyState:
    """The state of each operation's build before it, in record order. With an assembly_data Inventory,
    references resolve to occurrence ids and every occurrence counts; without one, the raw references
    stand for themselves and a build's base is every part it references that none of its operations installs.

    Sub-builds. An operation with `sub_build` belongs to that bench sub-build; the occurrences its operations
    name (and the members of the subassembly of the same id) are the sub-build's own, with their own base, state
    and closure. An install operation puts a sub-build into the main build as one unit: from then on its
    members, and what it encloses, are part of the main build's state. Every other occurrence is the main
    build's. An occurrence two builds claim is in `shared` (a main build claims only what it installs)."""
    unresolved: dict[str, str] = {}

    def resolve(ref: str, op: AssemblyOperation) -> str:
        if inventory is None:
            return ref
        try:
            resolved: str = inventory.resolve(ref).id
            return resolved
        except ValueError as error:
            unresolved.setdefault(op.id, str(error))
            return ref

    resolved = [(op, op.part if op.kind == "install" else resolve(op.part, op),
                 None if op.into is None else resolve(op.into, op), tuple(resolve(ref, op) for ref in op.closes))
                for op in operations.items]
    builds = list(dict.fromkeys(op.sub_build for op in operations.items if op.sub_build is not None))
    groups = {group.id: group.members for group in inventory.groups} if inventory is not None else {}
    claims = _claims(resolved, builds, groups)
    if inventory is not None:  # a reference that does not resolve is no member: its operation stays unknown
        claims = {ref: owners for ref, owners in claims.items() if ref in inventory}
    members = {build: frozenset(ref for ref, owners in claims.items() if build in owners) for build in builds}
    owned = frozenset().union(*members.values()) if members else frozenset()
    named_by: dict[str | None, list[str]] = {}
    installed: dict[str | None, set[str]] = {}
    for op, part, into, closes in resolved:
        refs = (into, *closes) if op.kind == "install" else (part, into, *closes)
        named_by.setdefault(op.sub_build, []).extend(ref for ref in refs if ref is not None)
        if op.installs and op.kind != "install":
            installed.setdefault(op.sub_build, set()).add(part)
    named = list(dict.fromkeys(named_by.get(None, ())))
    everyone = set(inventory.ids) if inventory is not None else set(named) | {ref for refs in named_by.values() for ref in refs}
    base = frozenset(everyone - owned - installed.get(None, set()))
    # The record names no base. The first uninstalled occurrence the build names is its base, or the first
    # occurrence of the inventory (of the subassembly, for a sub-build) when it names none. The rest float.
    ground = None
    # existing equipment (these part numbers) is present from the start: never the product's base, never floating
    preexisting = frozenset(o.id for o in inventory.occurrences if o.pn in existing) if inventory is not None else frozenset()
    if inventory is not None:
        ground = next((ref for ref in (*named, *inventory.ids) if ref in base - preexisting), None)
    floating = frozenset(base - {ground} - preexisting) if inventory is not None else frozenset()
    sub_base = {build: frozenset(members[build] - installed.get(build, set())) for build in builds}
    sub_ground = {build: next((ref for ref in (*named_by.get(build, ()), *groups.get(build, ()), *sorted(members[build]))
                               if ref in sub_base[build]), None) for build in builds}
    steps, present, closed = _walk(resolved, base, sub_base)
    last = {op.sub_build: op.index for op in operations.items if op.sub_build is not None}
    sub_builds = {build: SubBuild(build, members[build], sub_ground[build],
                                  frozenset(sub_base[build] - {sub_ground[build]}) if inventory is not None else frozenset(),
                                  last.get(build), frozenset(present[build]), types.MappingProxyType(dict(closed[build])))
                  for build in builds}
    shared = {ref: tuple(owners) for ref, owners in claims.items() if len(owners) > 1}
    return AssemblyState(tuple(steps), base, types.MappingProxyType(unresolved), ground, floating,
                         types.MappingProxyType(sub_builds), types.MappingProxyType(shared))


Resolved = tuple[AssemblyOperation, str, str | None, tuple[str, ...]]


def _claims(resolved: list[Resolved], builds: list[str],
            groups: Mapping[str, tuple[str, ...]]) -> dict[str, list[str | None]]:
    """occurrence -> the builds that claim it, in claim order: a sub-build claims its subassembly's members and
    what its operations name; the main build claims only what it installs."""
    claims: dict[str, list[str | None]] = {}

    def claim(ref: str, build: str | None) -> None:
        owners = claims.setdefault(ref, [])
        if build not in owners:
            owners.append(build)

    for build in builds:
        for member in groups.get(build, ()):
            claim(member, build)
    for op, part, into, closes in resolved:
        if op.sub_build is not None:
            for ref in (part, into, *closes):
                if ref is not None:
                    claim(ref, op.sub_build)
        elif op.installs and op.kind != "install":
            claim(part, None)
    return claims


def _walk(resolved: list[Resolved], base: frozenset[str], sub_base: Mapping[str, frozenset[str]]
          ) -> tuple[list[Step], dict[str | None, set[str]], dict[str | None, dict[str, str]]]:
    """The steps in record order, each with its own build's state; an install adds the sub-build's parts and
    closures to the main build. Returns the steps and each build's final present and closed maps."""
    present: dict[str | None, set[str]] = {None: set(base), **{build: set(parts) for build, parts in sub_base.items()}}
    closed: dict[str | None, dict[str, str]] = {build: {} for build in present}
    steps = []
    for op, part, into, closes in resolved:
        owner = op.sub_build
        steps.append(Step(op, part, into, closes, frozenset(present[owner]),
                          types.MappingProxyType(dict(closed[owner])), owner))
        if op.kind == "install":
            if part in present:
                present[None] |= present[part]
                for ref, closer in closed[part].items():
                    closed[None].setdefault(ref, closer)
            continue
        if op.installs:
            present[owner].add(part)
        if op.removes:
            present[owner].discard(part)
        if op.kind == "close":
            for ref in closes:
                closed[owner].setdefault(ref, op.id)
    return steps, present, closed


# --------------------------------------------------------------------------- findings


def _finding(ident: str, code: str, op: AssemblyOperation | None, *, severity: str, outcome: str, measured: str,
             consequence: str, remedy: str, family: str, obligation: str | None, criterion: Mapping[str, Any] | None = None,
             uncertainty: str = "none: the declared record decides it", alternatives: list[dict[str, str]] | None = None,
             coverage: str | None = None, subject: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A finding about operation `op`, or about `subject` when no operation is at fault (`op` is then None)."""
    if subject is None:
        assert op is not None, "a finding needs an operation or a subject"
        subject = {"assembly_operation": op.id}
    subject = dict(subject)
    data: dict[str, Any] = {
        "id": ident, "code": code, "subject": subject, "severity": severity, "outcome": outcome,
        "measured": {"text": measured} if outcome != "unknown" else {"status": "unknown", "reason": measured},
        "criterion": dict(criterion) if criterion is not None else {
            "property": "assembly_order", "operator": "equals",
            "value": "receiving part present, part reachable (not enclosed), installed once, predecessors earlier",
            "source": dict(RULE_SOURCE)},
        "method": {"name": ORDER_METHOD if family == "assembly.order" else "declared joint evidence (cad-record assembly_operations)",
                   "tool": "cad-record assembly_operations.py"},
        "uncertainty": uncertainty, "consequence": consequence, "remedy": remedy,
        "recheck": {"families": [family], "scope": [subject], "earliest_stage": "detail"},
    }
    if obligation is not None:
        data["obligation"] = obligation
    if alternatives:
        data["alternatives"] = alternatives
    if coverage:
        data["coverage"] = coverage
    return data


def _slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")
    return out if out and out[0].isalpha() else f"op_{out}"


@dataclass(frozen=True)
class Report:
    """Per-obligation outcomes (manufacturing.schema.json#/$defs/obligation_outcome) and findings."""

    outcomes: tuple[Mapping[str, Any], ...]
    findings: tuple[Mapping[str, Any], ...]

    def outcome(self, obligation: str) -> Mapping[str, Any]:
        for row in self.outcomes:
            if row["obligation"] == obligation:
                return row
        raise KeyError(obligation)


def check_order(operations: Operations, inventory: Any = None, existing: frozenset[str] = frozenset()) -> Report:
    """`assembly.order@op:<id>` for every operation (or `intent.assembly_operations@product` unknown when the
    operations are unknown): pass when the declared sequence is possible, else fail with its findings. `existing`
    names the part numbers of existing equipment, present from the start and never the product's base."""
    if operations.unknown is not None:
        return Report(({"obligation": "intent.assembly_operations@product", "outcome": "unknown", "level": "process_planned",
                        "method": ORDER_METHOD, "coverage": "no operations are recorded",
                        "uncertainty": operations.unknown},), ())
    state = sequence(operations, inventory, existing)
    position = {op.id: op.index for op in operations.items}
    installer, transfers = _installers(state)
    outcomes, findings = [], []
    for step in state.steps:
        op = step.operation
        obligation = f"assembly.order@op:{op.id}"
        mine: list[dict[str, Any]] = []

        def fail(code: str, measured: str, consequence: str, remedy: str, *, severity: str = "critical",
                 alternatives: list[dict[str, str]] | None = None) -> None:
            ident = f"{_slug(op.id)}_{code.split('.')[-1]}"
            ident = ident if all(f["id"] != ident for f in mine) else f"{ident}_{len(mine) + 1}"
            mine.append(_finding(ident, code, op, severity=severity, outcome="fail", measured=measured,
                                 consequence=consequence, remedy=remedy, family="assembly.order", obligation=obligation,
                                 alternatives=alternatives))

        if op.id in state.unresolved:
            outcomes.append({"obligation": obligation, "outcome": "unknown", "level": "process_planned",
                             "method": ORDER_METHOD, "coverage": ORDER_COVERAGE,
                             "uncertainty": f"{op.id}: {state.unresolved[op.id]}"})
            continue
        for predecessor in op.predecessors:
            if predecessor not in position:
                fail("assembly.predecessor_unknown", f"predecessor {predecessor} is not an operation",
                     "the declared order cannot be followed", f"name an existing operation, or remove {predecessor}",
                     severity="major")
            elif position[predecessor] >= op.index:
                fail("assembly.order_impossible", f"{op.id} comes before its predecessor {predecessor} in the sequence",
                     f"{op.id} cannot be carried out in the declared sequence",
                     f"move {op.id} after {predecessor}, or correct its predecessors")
        reached = (step.into,) if op.kind == "install" else (step.part, step.into)  # an install's part is a unit
        enclosed = [ref for ref in reached if ref is not None and ref in step.closed
                    and not (op.kind == "close" and ref == step.into)]
        for ref in enclosed:
            closer = step.closed[ref]
            fail("assembly.closed_before_install",
                 f"{op.id} ({op.kind}) reaches {ref}, which {closer} encloses earlier in the sequence",
                 f"{ref} cannot be reached once {closer} has closed it: the {op.kind} is impossible",
                 f"install {ref} before {closer}, or give {ref} an access path that {closer} does not close",
                 alternatives=[{"text": f"reorder: {op.id} before {closer}",
                                "tradeoffs": "check that nothing else needs the open state after it"},
                               {"text": f"split the closing part so {ref} stays reachable",
                                "tradeoffs": "adds a joint, fasteners and a tolerance stack"}])
        if step.into is not None and step.into not in step.present and not enclosed:
            later = [other.id for other in installer.get(step.into, []) if other.index > op.index]
            fail("assembly.into_absent",
                 f"{op.id} puts {op.part} into {op.into}, which is not in place yet" +
                 (f" (installed later by {', '.join(later)})" if later else " (removed earlier)"),
                 "the receiving part is missing at this step: the declared order is impossible",
                 f"install {op.into} before {op.id}")
        if op.installs and op.kind != "install" and step.part in step.present:
            fail("assembly.installed_twice", f"{op.id} installs {op.part}, which is already in place",
                 "the sequence installs the same occurrence twice", "remove the duplicate step, or disassemble first",
                 severity="major")
        if not op.installs and step.part not in step.present and not enclosed:
            fail("assembly.part_absent", f"{op.id} ({op.kind}) needs {op.part}, which is not in place",
                 f"the {op.kind} cannot be done at this step", f"move {op.id} after {op.part} is installed")
        if op.kind == "close":
            for ref in step.closes:
                if ref not in step.present and ref != step.part:
                    later = [other.id for other in installer.get(ref, []) if other.index > op.index]
                    if not later:  # an enclosed part installed later fails at that later step instead
                        fail("assembly.part_absent", f"{op.id} encloses {ref}, which is not in place", f"{ref} is missing "
                             "inside the closed assembly", f"install {ref} before {op.id}")
        _sub_build_rules(operations, state, step, transfers, fail)
        findings.extend(mine)
        row: dict[str, Any] = {"obligation": obligation, "outcome": "fail" if mine else "pass", "level": "process_planned",
                               "method": ORDER_METHOD, "coverage": ORDER_COVERAGE}
        if mine:
            row["findings"] = [f["id"] for f in mine]
        outcomes.append(row)
    if inventory is not None:
        never = _never_installed(state, inventory)
        product: dict[str, Any] = {"obligation": "assembly.order@product", "outcome": "fail" if never else "pass",
                                   "level": "process_planned", "method": ORDER_METHOD, "coverage": ORDER_COVERAGE}
        if never:
            product["findings"] = [f["id"] for f in never]
        outcomes.append(product)
        findings.extend(never)
    return Report(tuple(outcomes), tuple(findings))


def _build_name(build: str | None) -> str:
    return "the main build" if build is None else f"sub-build {build}"


def _installers(state: AssemblyState) -> tuple[dict[str, list[AssemblyOperation]], dict[str, list[AssemblyOperation]]]:
    """(occurrence -> the operations that install it, sub-build id -> the install operations that transfer it), in
    record order. Kept apart, so an occurrence whose id is a sub-build id is never taken for the unit."""
    occurrences: dict[str, list[AssemblyOperation]] = {}
    transfers: dict[str, list[AssemblyOperation]] = {}
    for step in state.steps:
        op = step.operation
        if op.kind == "install":
            transfers.setdefault(step.part, []).append(op)
        elif op.installs:
            occurrences.setdefault(step.part, []).append(op)
    return occurrences, transfers


def _claimed(step: Step) -> list[tuple[str | None, str | None]]:
    """(resolved, written) references a step claims for its build: all of them in a sub-build, the installed
    part in the main build."""
    op = step.operation
    pairs = list(zip((step.part, step.into, *step.closes), (op.part, op.into, *op.closes)))
    return pairs if step.build is not None else pairs[:1] if op.installs and op.kind != "install" else []


def _sub_build_rules(operations: Operations, state: AssemblyState, step: Step,
                     transfers: Mapping[str, list[AssemblyOperation]], fail: Callable[..., None]) -> None:
    """An install names a sub-build that exists, is finished and is installed once; an occurrence belongs to one
    build (failed at the first step of each later build that claims it); a sub-build is installed; a hold's
    cure is sourced and long enough."""
    op = step.operation
    if op.kind == "hold":
        _hold_rules(op, fail)
    if op.kind == "install":
        unit = state.sub_builds.get(step.part)
        if unit is None:
            fail("assembly.sub_build_unknown", f"{op.id} installs sub-build {op.part}, but no operation belongs to it",
                 f"nothing builds {op.part}: the unit this step puts in place does not exist",
                 f"give the bench operations of {op.part} `sub_build: {op.part}`, or name an existing sub-build")
        elif unit.last is not None and unit.last > op.index:
            pending = [other.id for other in operations.items if other.sub_build == op.part and other.index > op.index]
            fail("assembly.sub_build_unfinished",
                 f"{op.id} installs sub-build {op.part} before its own step(s) {', '.join(pending)}",
                 f"{op.part} goes into the assembly unfinished: its later bench steps cannot be done in place",
                 f"move {op.id} after {pending[-1]}")
        if any(other.index < op.index for other in transfers.get(step.part, ())):
            fail("assembly.installed_twice", f"{op.id} installs sub-build {op.part}, which is already in place",
                 "the sequence installs the same unit twice", "remove the duplicate install step", severity="major")
        return
    earlier = {ref for other in state.steps[:op.index] if other.build == step.build for ref, _ in _claimed(other)}
    for ref, written in _claimed(step):
        owners = state.shared.get(ref) if ref is not None else None
        if owners is None or owners.index(step.build) == 0 or ref in earlier:
            continue
        earlier.add(ref)
        fail("assembly.sub_build_shared", f"{op.id} uses {written} in {_build_name(step.build)}, which "
             f"{_build_name(owners[0])} also uses", f"one physical part cannot be built into {len(owners)} units",
             f"keep {written} in one build, or add an occurrence for the other")
    unit = state.sub_builds.get(step.build) if step.build is not None else None
    if unit is not None and unit.last == op.index and step.build not in transfers:
        fail("assembly.sub_build_not_installed", f"no install operation puts sub-build {step.build} into the assembly",
             f"the parts of {step.build} are built on the bench but never reach the assembly",
             f"add an install operation with part {step.build} after {op.id}")


def _hold_rules(op: AssemblyOperation, fail: Callable[..., None]) -> None:
    """A cure hold's time and temperature are datasheet facts, and the part stays in its fixture for the cure time."""
    if op.cure is None:
        return
    for name in ("time", "temperature"):
        fact = _mapping(op.cure.get(name))
        if name in op.cure and not (fact.get("status") == "known" and any(
                isinstance(source, Mapping) and source.get("kind") in CURE_SOURCES for source in fact.get("sources") or ())):
            fail("assembly.cure_unsourced", f"{op.id}: the cure {name} is not a known fact with a datasheet source",
                 "the hold may end before the adhesive or potting has cured: the bond can shift or fail in service",
                 f"record the cure {name} from the adhesive's datasheet, with the datasheet as its source", severity="major")
    held = _hours(op.duration, lower=True)
    needed = _hours(op.cure.get("time"), lower=False)
    if needed is not None and (held is None or held < needed):
        measured = "unknown" if held is None else f"{held:g} h"
        fail("assembly.hold_short", f"{op.id}: the hold lasts {measured}, the cure time is {needed:g} h",
             "the part leaves its fixture before it has cured: the bond can shift or fail",
             f"hold for at least {needed:g} h, or choose an adhesive whose datasheet cure time fits the hold")


def _hours(fact: Any, *, lower: bool) -> float | None:
    """A known time quantity fact in hours: its lower end when `lower`, else its upper end; None otherwise."""
    if not isinstance(fact, Mapping) or fact.get("status") != "known":
        return None
    quantity = _mapping(fact.get("value"))
    units = quantity.get("units")
    scale = TIME_H.get(units) if isinstance(units, str) else None
    value = quantity.get("value") if _number(quantity.get("value")) else quantity.get("lower" if lower else "upper")
    return value * scale if scale is not None and _number(value) else None


def _never_installed(state: AssemblyState, inventory: Any) -> list[dict[str, Any]]:
    """A finding per occurrence of the inventory that no operation installs and that is not its build's one
    base: it would pass as a second base, but nothing says where it comes from."""
    found: list[dict[str, Any]] = []
    taken: set[str] = set()
    floats: list[tuple[str, str | None, str | None]] = [(occurrence_id, None, state.ground)
                                                         for occurrence_id in sorted(state.floating)]
    floats += [(occurrence_id, unit.id, unit.ground) for unit in state.sub_builds.values()
               for occurrence_id in sorted(unit.floating)]
    for occurrence_id, build, ground in floats:
        occurrence = inventory.get(occurrence_id)
        subject: dict[str, Any] = {"PN": occurrence.pn}
        if occurrence.explicit:
            subject["occurrence"] = occurrence.ref
        base = inventory.get(ground).ref if ground else "none"
        where = "the assembly" if build is None else f"sub-build {build}"
        ident = f"{_slug(occurrence_id)}_never_installed"
        while ident in taken:  # `clip-a` and `clip_a` slug alike
            ident = f"{ident}_2"
        taken.add(ident)
        found.append(_finding(
            ident, "assembly.never_installed", None, severity="critical", outcome="fail",
            measured=f"no operation installs {occurrence.ref}, and {where} has one base (the base is {base})",
            consequence=f"{occurrence.ref} is in {where} but the sequence never puts it in place, so it floats "
                        f"beside the base and {where} cannot be built as recorded",
            remedy=f"add the installing operation for {occurrence.ref}, or remove the occurrence from {where}",
            family="assembly.order", obligation="assembly.order@product", subject=subject))
    return found


# --------------------------------------------------------------------------- access plan


@dataclass(frozen=True)
class AccessRequest:
    """What a geometric access check of one operation needs: the operated occurrence (`part`), the one
    that travels (`moving`, None when the operation moves no part) and its direction in the `frame`
    occurrence's model frame, the tools, and the occurrences present at that state as obstacles (the
    moving part and its receiver excluded for the part path; the operated part excluded for the tools).
    `path` is the declared insertion path (receiver frame, see `_path`), None for the default straight
    line along `direction`. `unknown` lists what keeps reach unknown. The obstacles are the parts of the
    operation's own build: a sub-build step sees only its sub-build. An install moves the sub-build as one
    unit: `moving` is its base and `unit` all its members, none of which is an obstacle to the others."""

    operation: AssemblyOperation
    part: str
    moving: str | None
    frame: str  # the occurrence whose model frame `direction` and tool approaches are in
    direction: Vec | None
    removal: bool  # a disassemble path runs away from the receiver (along direction)
    path_obstacles: frozenset[str]
    tool_obstacles: frozenset[str]
    tools: tuple[Tool, ...]
    unknown: tuple[str, ...]
    path: tuple[Vec, ...] | None = None
    unit: frozenset[str] = frozenset()


def _unit(state: AssemblyState, step: Step, unknown: list[str]) -> tuple[frozenset[str], str]:
    """(the parts that move together, the operated occurrence): an install moves its sub-build's parts with the
    sub-build's base as the operated part, noting in `unknown` what the check cannot cover; any other step moves
    its own part."""
    op = step.operation
    if op.kind != "install":
        return frozenset(), step.part
    built = state.sub_builds.get(step.part)
    if built is None or built.ground is None:
        unknown.append(f"{op.id}: sub-build {op.part} has no base occurrence: its path is unknown")
        return (built.present if built is not None else frozenset()), step.part
    if len(built.present) > 1:
        unknown.append(f"{op.id}: sub-build {op.part} travels as one unit; only the path of its base "
                       f"{built.ground} is checked, the other members ({', '.join(sorted(built.present - {built.ground}))}) "
                       "are carried along unchecked")
    return built.present, built.ground


def access_plan(operations: Operations, inventory: Any = None) -> tuple[AccessRequest, ...]:
    """One AccessRequest per operation, from the declared sequence (see `sequence`)."""
    state = sequence(operations, inventory)
    requests = []
    for step in state.steps:
        op = step.operation
        unknown: list[str] = []
        travels = op.kind in JOINING or op.kind in ("close", "disassemble", "install")
        if travels and op.direction is None:
            unknown.append(f"{op.id} has no direction: its part path is unknown")
        if op.id in state.unresolved:
            unknown.append(state.unresolved[op.id])
        for tool in op.tools:
            if not tool.declared:
                unknown.append(f"tool {tool.name!r} is a name only: its reach and envelope are unknown")
                continue
            if tool.envelope_artifact is None:
                unknown.append(f"tool {tool.name!r} has no sourced envelope solid: its reach is unknown")
            if tool.approach is None:
                unknown.append(f"tool {tool.name!r} has no approach direction")
        unit, operated = _unit(state, step, unknown)
        moved = unit or {step.part}
        after = set(step.present) | (set(moved) if op.installs else set())
        path_obstacles = frozenset(after - moved - ({step.into} if step.into else set()))
        tool_obstacles = frozenset(after - moved)
        requests.append(AccessRequest(op, operated, operated if travels else None, step.into or operated,
                                      op.direction, op.removes, path_obstacles, tool_obstacles, op.tools, tuple(unknown),
                                      op.path, unit))
    return tuple(requests)


# --------------------------------------------------------------------------- joints


def check_joints(operations: Operations, record: Any, resolve_property: Callable[[Any, str], Any], *,
                 part_of: Callable[[str], str | None] | None = None) -> Report:
    """`assembly.joint@op:<id>` for every joining operation, with the findings on its joint evidence
    (see the module): fail when a finding fails, unknown when one is unknown, else pass. `record` is a
    manufacturing_intent Record, `resolve_property` its resolve_property, and `part_of` maps a part
    reference to its PN (default: a bare PN is itself, an @occurrence unresolved)."""
    part_of = part_of or (lambda ref: None if ref.startswith("@") else ref)
    outcomes: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    temperature = record.use_case.facts.get("environment.temperature")
    for op in operations.items:
        if op.kind not in JOINING:
            continue
        obligation = f"assembly.joint@op:{op.id}"
        mine: list[dict[str, Any]] = []

        def add(code: str, outcome: str, measured: str, consequence: str, remedy: str, *, severity: str = "major",
                criterion: Mapping[str, Any] | None = None) -> None:
            unknown = outcome == "unknown"
            mine.append(_finding(f"{_slug(op.id)}_{code.split('.')[-1]}", code, op, severity=severity, outcome=outcome,
                                 measured=measured, consequence=consequence, remedy=remedy, family="assembly.joint",
                                 obligation=obligation,
                                 criterion={"status": "unknown", "reason": measured} if unknown else criterion,
                                 uncertainty=measured if unknown else "the declared facts and profiles"))

        if not _sourced(op.retention):
            add("assembly.retention_unknown", "unknown", f"{op.id}: retention of the {op.kind} is not a sourced fact",
                "the joint may loosen, creep or pull out over the service temperature and life",
                "record retention evidence: a joint test, supplier data or a sourced calculation for this host "
                "material, temperature and life")
        if op.kind == "fasten":
            for name, value in (("engagement", op.engagement), ("torque", op.torque)):
                if not _sourced(value):
                    add(f"assembly.{name}_unknown", "unknown", f"{op.id}: thread {name} is not a sourced fact",
                        "the fastener may strip or loosen", f"record the {name} with its source")
        _edge_distance(op, add)
        _host_temperature(op, record, resolve_property, part_of, temperature, add)
        findings.extend(mine)
        states = {f["outcome"] for f in mine}
        row: dict[str, Any] = {"obligation": obligation,
                               "outcome": "fail" if "fail" in states else "unknown" if "unknown" in states else "pass",
                               "level": "process_planned", "method": JOINT_METHOD, "coverage": JOINT_COVERAGE}
        if mine:
            row["findings"] = [f["id"] for f in mine]
        if row["outcome"] == "unknown":
            row["uncertainty"] = "; ".join(f["measured"]["reason"] for f in mine if f["outcome"] == "unknown")
        outcomes.append(row)
    return Report(tuple(outcomes), tuple(findings))


def _edge_distance(op: AssemblyOperation, add: Callable[..., None]) -> None:
    """A hole-based joint's edge distance against its criterion: required for EDGE_DISTANCE_KINDS,
    judged wherever declared. The distance's lower end must meet the criterion's minimum, its upper
    end a `between` maximum."""
    edge = op.edge_distance
    consequence = "the hole may break out or the host split at its edge under load or installation"
    if edge is None:
        if op.kind in EDGE_DISTANCE_KINDS:
            add("assembly.edge_distance_unknown", "unknown", f"{op.id}: the {op.kind} records no edge distance", consequence,
                "record edge_distance: the sourced criterion (supplier or standard minimum) and the distance the design gives")
        return
    criterion = _mapping(edge.get("criterion"))
    bounds = _criterion_mm(criterion)
    distance = _mapping(edge.get("distance"))
    measured = _quantity_mm(distance.get("value")) if distance.get("status") == "known" else None
    if bounds is None:
        add("assembly.edge_distance_unknown", "unknown", f"{op.id}: the edge distance criterion is not a length bound",
            consequence, "state the criterion as >= or between, in mm, um or m, with its source")
        return
    if measured is None:
        reason = distance.get("reason") or "not a known length"
        add("assembly.edge_distance_unknown", "unknown",
            f"{op.id}: the edge distance is {distance.get('status', 'unknown')}: {reason}", consequence,
            "record the distance from the hole to the nearest edge, as designed or measured")
        return
    (minimum, maximum), (lower, upper) = bounds, measured
    if lower < minimum or (maximum is not None and upper > maximum):
        limit = f">= {minimum:g} mm" if maximum is None else f"between {minimum:g} and {maximum:g} mm"
        add("assembly.edge_distance", "fail", f"{op.id}: the edge distance is {lower:g}..{upper:g} mm, the criterion {limit}",
            consequence, "move the hole, enlarge the boss or flange, or choose a joint whose sourced criterion this "
            "distance meets", severity="critical", criterion=dict(criterion))


def _criterion_mm(criterion: Mapping[str, Any]) -> tuple[float, float | None] | None:
    """(minimum, maximum or None) in mm of an edge distance criterion, None when it is not a length bound."""
    units = criterion.get("units")
    scale = LENGTH_MM.get(units) if isinstance(units, str) else None
    value = criterion.get("value")
    if scale is None:
        return None
    if criterion.get("operator") == ">=" and _number(value):
        return value * scale, None
    if (criterion.get("operator") == "between" and isinstance(value, (list, tuple)) and len(value) == 2
            and all(_number(v) for v in value) and value[0] <= value[1]):
        return value[0] * scale, value[1] * scale
    return None


def _quantity_mm(value: Any) -> tuple[float, float] | None:
    """(lower, upper) in mm of a length quantity, None when it is not one."""
    if not isinstance(value, Mapping) or value.get("units") not in LENGTH_MM:
        return None
    scale = LENGTH_MM[value["units"]]
    if _number(value.get("value")):
        return value["value"] * scale, value["value"] * scale
    if _number(value.get("lower")) and _number(value.get("upper")):
        return value["lower"] * scale, value["upper"] * scale
    return None


def _number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _mapping(value: object) -> Mapping[str, Any]:
    """`value` when it is a mapping, else an empty one (an absent or malformed block reads as empty)."""
    return value if isinstance(value, Mapping) else {}


def _host_temperature(op: AssemblyOperation, record: Any, resolve_property: Callable[[Any, str], Any],
                      part_of: Callable[[str], str | None], temperature: Any, add: Callable[..., None]) -> None:
    """The host of an insert, heat-set or press fit against the use-case temperature. A qualified limit (see
    `_qualified`) judges it; an estimated or unsourced one can only fail it: within such a limit the joint stays
    unknown, never a pass."""
    if op.kind not in HOST_TEMPERATURE_KINDS or op.into is None:
        return
    pn = part_of(op.into)
    part = record.parts.get(pn) if pn else None
    profile_id = part.intent.material_profile if part is not None else None
    profile = record.materials.get(profile_id) if isinstance(profile_id, str) else None
    if profile is None:
        what = f"{op.into} resolves to no part" if part is None else f"{pn} has no resolved material profile"
        add("assembly.joint_temperature_unknown", "unknown", f"{op.id}: the host is unknown: {what}",
            f"a {op.kind} in an unidentified host may relax or release when the host softens",
            "name the receiving part and record its material profile with a sourced service temperature limit")
        return
    resolved = [(name, r) for name in TEMPERATURE_LIMITS for r in (resolve_property(profile, name),) if r.status == "resolved"]
    upper = _upper_deg_c(temperature)
    if not resolved or upper is None:
        add("assembly.joint_temperature_unknown", "unknown",
            f"{op.id}: {'the use-case temperature' if upper is None else profile.id + ' temperature limit'} is unknown",
            f"a {op.kind} in {profile.id} may relax or release when the host softens",
            "record the use-case temperature range and the host's sourced service temperature limit")
        return
    qualified = next(((name, r) for name, r in resolved if _qualified(r)), None)
    name, resolution = qualified or resolved[0]
    value = resolution.value
    limit_c = _to_deg_c(value.lower if value.value is None else value.value, value.units)
    if upper > limit_c:
        source = {key: item for key, item in vars(resolution.sources[0]).items() if item is not None}
        add("assembly.joint_temperature", "fail",
            f"{op.id}: the use-case temperature reaches {upper:g} deg_C, above {profile.id} {name} {limit_c:g} deg_C "
            f"({resolution.basis})", f"the {op.kind} host softens in service: the joint loses retention",
            "choose a host material qualified above the service temperature, or a joint that does not rely on the "
            "host's stiffness (a through bolt with a metal sleeve)", severity="critical",
            criterion={"property": "service_temperature", "operator": "<=", "value": limit_c, "units": "deg_C",
                       "source": source})
    elif qualified is None:
        add("assembly.joint_temperature_unknown", "unknown",
            f"{op.id}: {profile.id} {name} {limit_c:g} deg_C ({resolution.basis}) is not a qualified limit (an estimate, or "
            f"no {', '.join(sorted(QUALIFYING_SOURCES))} source): it cannot show the host holds up to {upper:g} deg_C",
            f"a {op.kind} in {profile.id} may relax or release when the host softens",
            "record the host's service temperature limit with a qualifying source (datasheet, supplier, standard, test "
            "or calibration)")


def _qualified(resolution: Any) -> bool:
    """Whether a resolved temperature limit may judge a joint: a sourced value (a QUALIFYING_SOURCES source),
    never an estimate."""
    return bool(resolution.basis != "estimate" and any(source.kind in QUALIFYING_SOURCES for source in resolution.sources))


def _sourced(fact: Mapping[str, Any] | None) -> bool:
    if not isinstance(fact, Mapping) or fact.get("status") != "known":
        return False
    return any(isinstance(s, Mapping) and s.get("kind") in QUALIFYING_SOURCES for s in fact.get("sources") or ())


def _to_deg_c(value: float, units: str) -> float:
    return value - 273.15 if units == "K" else value


def _upper_deg_c(fact: Any) -> float | None:
    if fact is None or getattr(fact, "status", None) != "known":
        return None
    quantity = fact.value
    upper = quantity.value if getattr(quantity, "upper", None) is None else quantity.upper
    return None if upper is None else _to_deg_c(float(upper), quantity.units)


def _hold_summary(op: AssemblyOperation) -> dict[str, Any]:
    """A hold's orientation, duration and cure as JSON (empty for any other operation)."""
    row: dict[str, Any] = {} if op.orientation is None else {"orientation": list(op.orientation)}
    row.update({key: _thaw(getattr(op, key)) for key in ("duration", "cure") if getattr(op, key) is not None})
    return row


def summary(operations: Operations) -> list[dict[str, Any]]:
    """The normalized semantics as JSON, for drawing recipes and guide provenance."""
    rows = []
    for op in operations.items:
        row: dict[str, Any] = {"id": op.id, "kind": op.kind, "part": op.part}
        for key in ("into", "interface", "acceptance", "verify_joint", "inspection", "sub_build", "fixture"):
            if getattr(op, key) is not None:
                row[key] = getattr(op, key)
        if op.direction is not None:
            row["direction"] = list(op.direction)
        if op.path is not None:
            row["path"] = [list(point) for point in op.path]
        row.update(_hold_summary(op))
        for key in ("features", "predecessors", "closes", "consumables"):
            if getattr(op, key):
                row[key] = list(getattr(op, key))
        if op.tools:
            row["tools"] = [{"name": t.name, "declared": t.declared, "reach_known": t.reach_known,
                             "envelope": None if t.envelope_artifact is None else t.envelope_artifact[0]} for t in op.tools]
        rows.append(row)
    return rows
