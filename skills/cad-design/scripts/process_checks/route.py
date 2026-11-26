# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Route handlers: the consistency of a component's whole route, each operation's plan, and the optical
route rule (decision 2: a transparency or optical requirement is met only by a purchased transparent part
or identified transparent stock on a suitable process; opaque processes are eliminated)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .core import FAIL, NOT_APPLICABLE, NOT_ASSESSED, PASS, UNKNOWN, Context, Draft, Result, combine, policy, text, unknown

ADDITIVE = frozenset({"fdm", "resin"})
SECONDARY = frozenset({"drilling", "reaming", "tapping", "finishing", "coating", "joining", "inspection"})
SHEET = frozenset({"laser", "waterjet", "plasma"})
OPTICAL_ROUTES = frozenset({"laser", "cnc", "waterjet"})  # transparent stock processes; finishing ops ride along


def consistency(ctx: Context) -> Result:
    """route.consistency: every operation names a capability of its own kind that is no template and does
    not forbid the material; the material profile exists; the component is shaped by some operation;
    input states follow output states; the scalar process label agrees with the route."""
    method = "route consistency: capability, material, component coverage, state chain and process label"
    mi, part = ctx.mi, ctx.part
    findings: list[Draft] = []
    parts: list[tuple[str, str]] = []
    if mi.is_fact(part.intent.route):
        findings.append(Draft("route.unknown", "major", UNKNOWN, text(f"route {part.intent.route.status}"),
                              policy("route", "equals", "recorded"), "part.yaml intent route", part.intent.route.reason or "",
                              f"{ctx.label} has no recorded route: no process check can run",
                              remedy="record the route operations, each with its capability", recheck=["process", "route"],
                              earliest_stage="parts"))
        return Result(UNKNOWN, method, "route unknown", findings)
    ops = ctx.shaping()
    if not ops:
        findings.append(Draft("route.component_unplanned", "major", UNKNOWN, text(f"no operation shapes {ctx.component}"),
                              policy("component_route", "equals", "at least one operation"), "part.yaml route components",
                              "the operations name other components", f"how {ctx.label} is made is unplanned: it is exported but "
                              "no process check covers it",
                              remedy=f"add {ctx.component} to an operation's components, or a route operation for it",
                              recheck=["route", "process"], earliest_stage="parts"))
        parts.append((UNKNOWN, "component unplanned"))
    material = ctx.material_id
    if material is None:
        parts.append((UNKNOWN, "material profile unknown"))
    elif material not in ctx.record.materials:
        findings.append(Draft("route.material_reference", "major", FAIL, text(f"material {material}"),
                              policy("material_profile", "equals", "a profile of hw/manufacturing/materials.yaml"),
                              "material profile lookup", "record content", f"{ctx.label} names a material profile the record does not hold",
                              remedy="add the profile or correct the reference", recheck=["route.consistency"], earliest_stage="parts"))
        parts.append((FAIL, "material profile missing"))
    else:
        parts.append((PASS, f"material {material}"))
    outputs = {op.id: op.output_state for op in ops}
    for op in ops:
        if op.kind == "purchased":
            parts.append((PASS, f"{op.id} purchased"))
            continue
        parts.append(_capability(ctx, op, material, findings))
        if part.provider_of(op.provider) == "outsourced" and not op.raw.get("supplier"):
            findings.append(Draft("route.supplier_unknown", "minor", UNKNOWN, unknown("no supplier"),
                                  policy("supplier", "equals", "named for an outsourced operation"), "part.yaml route",
                                  "not recorded", f"{op.id} is outsourced to an unnamed supplier: its capability and inspection "
                                  "evidence cannot be obtained", remedy="name the supplier", recheck=["route.consistency"]))
            parts.append((UNKNOWN, f"{op.id} supplier unknown"))
    parts += _states(ctx, ops, outputs, findings)
    kinds = {op.kind for op in ops if op.kind not in SECONDARY and op.kind != "inspection"}
    if part.process not in kinds and kinds and part.process != "other":
        findings.append(Draft("route.process_label", "major", FAIL, text(f"process {part.process}"),
                              policy("process_label", "one_of", sorted(kinds)), "part.yaml process against the route",
                              "record content", f"the scalar process {part.process} that names the outputs and the BOM disagrees "
                              f"with the route's {', '.join(sorted(kinds))}",
                              remedy="set part.yaml process to the route's primary operation kind", recheck=["route.consistency"],
                              earliest_stage="parts"))
        parts.append((FAIL, "process label disagrees with the route"))
    return combine(method, parts, findings, reason="no_capability_profile")


def _capability(ctx: Context, op: Any, material: str | None, findings: list[Draft]) -> tuple[str, str]:
    provider = ctx.part.provider_of(op.provider)  # stated, else the part's recorded sourcing
    if ctx.guidelines_without_machine(op):
        return NOT_ASSESSED, f"{op.id} records no machine; the guideline values govern"
    if ctx.mi.is_fact(op.capability):
        findings.append(Draft("route.capability_unknown", "major", UNKNOWN, text(f"{op.id} capability unknown"),
                              unknown(op.capability.reason or "no capability profile"), "part.yaml route capability",
                              "not recorded", f"{op.id} names no capability of the actual equipment or supplier"
                              + ("; outsourcing is no exemption" if provider == "outsourced" else ""),
                              remedy="record the capability profile of whoever performs it", recheck=[f"process.{op.kind}"],
                              earliest_stage="parts"))
        return UNKNOWN, f"{op.id} capability unknown"
    profile = ctx.record.capabilities.get(op.capability)
    if profile is None:
        findings.append(Draft("route.capability_reference", "major", FAIL, text(f"{op.id} capability {op.capability}"),
                              policy("capability_profile", "equals", "a profile of hw/manufacturing/capabilities.yaml"),
                              "capability profile lookup", "record content", f"{op.id} names a capability the record does not hold",
                              remedy="add the profile or correct the reference", recheck=[f"process.{op.kind}"], earliest_stage="parts"))
        return FAIL, f"{op.id} capability missing"
    if profile.template or profile.process != op.kind:
        why = "is a plugin template" if profile.template else f"is a {profile.process} profile"
        findings.append(Draft("route.capability_mismatch", "major", FAIL, text(f"{op.id}: {profile.id} {why}"),
                              policy("capability_process", "equals", op.kind), "capability profile lookup", "record content",
                              f"{op.id} is a {op.kind} operation but {profile.id} {why}: no other process's capability stands in",
                              remedy=f"name a {op.kind} capability of the actual equipment or supplier", recheck=[f"process.{op.kind}"],
                              earliest_stage="parts"))
        return FAIL, f"{op.id} capability mismatch"
    if profile.provider and provider != profile.provider:
        findings.append(Draft("route.provider_mismatch", "minor", FAIL, text(f"{op.id} {provider}, {profile.id} {profile.provider}"),
                              policy("provider", "equals", profile.provider), "capability profile provider", "record content",
                              f"{op.id} is {provider} but its capability is {profile.provider}",
                              remedy="make the operation's provider match whoever holds the capability", recheck=["route.consistency"]))
        return FAIL, f"{op.id} provider mismatch"
    approvals = profile.materials
    if material is not None and not ctx.mi.is_fact(approvals):
        entry = next((a for a in approvals if a.get("material") == material), None)
        if entry is not None and entry.get("status") == "forbidden":
            findings.append(Draft("route.material_forbidden", "major", FAIL, text(f"{material} on {profile.id}"),
                                  policy("material_approval", "equals", "not forbidden"), "capability profile materials",
                                  "recorded approval", f"{profile.id} forbids {material}: {entry.get('reason')}",
                                  remedy="choose another material or capability", recheck=["route.consistency"], earliest_stage="parts"))
            return FAIL, f"{op.id} forbids {material}"
    return PASS, f"{op.id} on {profile.id}"


def _states(ctx: Context, ops: list[Any], outputs: dict[str, Any], findings: list[Draft]) -> list[tuple[str, str]]:
    parts = []
    for op in ops:
        wanted = op.input_state
        if not wanted or wanted == "stock":
            continue
        earlier = {o.id for o in ctx.before(op)}
        produced = {outputs.get(i) for i in earlier}
        if wanted not in produced:
            findings.append(Draft("route.state_chain", "major", FAIL, text(f"{op.id} input {wanted}"),
                                  policy("input_state", "one_of", sorted(s for s in produced if s) or ["stock"]),
                                  "part.yaml route states", "record content",
                                  f"{op.id} starts from {wanted}, which no earlier operation of {ctx.label} produces",
                                  remedy="correct the input state or the order", recheck=["route.consistency", "route.operation_plan"]))
            parts.append((FAIL, f"{op.id} input state"))
    return parts


class _Plan:
    """The plan fields of one operation, judged one by one into `parts` and `findings`."""

    def __init__(self, ctx: Context) -> None:
        self.ctx, self.op, self.mi = ctx, ctx.operation, ctx.mi
        self.setup = self.op.setup or {}
        self.findings: list[Draft] = []
        self.parts: list[tuple[str, str]] = []

    def recorded(self, value: Any, ok: bool) -> str:
        """Missing when absent, open when a Fact or not yet in its settled form, present otherwise."""
        if value is None or (not self.mi.is_fact(value) and not value):
            return "missing"
        return "present" if ok else "open"

    def need(self, state: str, field: str, what: str, consequence: str, severity: str = "minor") -> None:
        """`state` is "present", "open" (recorded but undecided: UNKNOWN) or "missing" (not recorded at all: FAIL)."""
        op = self.op
        if state == "present":
            self.parts.append((PASS, what))
        elif state == "open":
            self.findings.append(Draft("route.plan_incomplete", severity, UNKNOWN, unknown(f"{what} not recorded"),
                                       policy("operation_plan", "equals", what), "part.yaml route", "not recorded", consequence,
                                       remedy=f"record {what} for {op.id}", recheck=["route.operation_plan"], feature=None))
            self.parts.append((UNKNOWN, f"{what} missing"))
        else:
            self.findings.append(Draft("route.operation_plan_field_missing", severity, FAIL,
                                       text(f"{field} missing from the plan of {op.id}"),
                                       policy("operation_plan", "equals", what), "part.yaml route", "record content", consequence,
                                       remedy=f"record {what} for {op.id}", recheck=["route.operation_plan"], feature=None))
            self.parts.append((FAIL, f"{field} missing"))

    def additive(self) -> None:
        orientation = self.op.orientation
        self.need(self.recorded(orientation, isinstance(orientation, Mapping) and orientation.get("status") == "approved"),
                  "orientation", "an approved orientation", "the pose the part prints in is not fixed", "major")
        self.need(self.recorded(self.op.supports, isinstance(self.op.supports, Mapping)), "supports", "a support strategy",
                  "where supports go is unplanned", "major")

    def secondary(self) -> None:
        op = self.op
        if not self.ctx.before() and not op.input_state:
            self.findings.append(Draft("route.plan_order", "major", FAIL, text(f"{op.id} first"),
                                       policy("input_state", "equals", "an earlier operation"), "part.yaml route order",
                                       "record content", f"{op.id} has nothing to act on", remedy="order the route",
                                       recheck=["route.operation_plan"]))
            self.parts.append((FAIL, "no predecessor"))
        if op.kind != "coating":  # a coat locates on nothing; every other secondary operation locates on datums
            self.need(self.recorded(self.setup.get("datums"), True), "datums", "the datums it locates on",
                      "the datum transfer from the earlier operation is unknown", "major")
        if op.kind not in ("inspection", "coating"):  # a coat without targets covers the whole part
            self.need(self.recorded(op.targets, True), "targets", "its target features", "what it acts on is unknown", "major")

    def shaping(self) -> None:
        op, setup = self.op, self.setup
        if not self.ctx.before():
            self.need(self.recorded(setup.get("stock"), True), "stock", "its stock", "what it is made from is unknown", "major")
        if op.kind in SHEET:
            thickness = (setup.get("stock") or {}).get("thickness")
            self.need(self.recorded(thickness, isinstance(thickness, Mapping) and thickness.get("status") == "known"),
                      "stock thickness", "the stock thickness", "the sheet thickness that sets kerf and fits is unknown", "major")
        elif op.kind in ("casting", "moulding", "extrusion"):
            self.parts.append((PASS, "its workholding"))
        elif op.kind != "sheet_forming":
            self.need(self.recorded(setup.get("workholding"), True), "workholding", "its workholding",
                      "how the part is held is unknown")


def operation_plan(ctx: Context) -> Result:
    """route.operation_plan: the operation records the plan its kind needs (orientation and supports,
    setup and stock, datums and targets) and an inspection for each critical surface it makes."""
    op = ctx.operation
    method = f"{op.kind} operation plan: setup, orientation, supports, allowances and inspection recorded"
    plan = _Plan(ctx)
    findings, parts = plan.findings, plan.parts
    if op.kind in ADDITIVE:
        plan.additive()
    elif op.kind in SECONDARY:
        plan.secondary()
    else:
        plan.shaping()
    inspections = ctx.inspections()
    made = ctx.targets() or ({s.feature for s in ctx.surfaces()} if op.kind not in SECONDARY else set())
    for surface in ctx.surfaces():
        if surface.feature not in made or op.kind == "inspection":
            continue
        if surface.inspection and surface.inspection in inspections:
            parts.append((PASS, f"{surface.feature} inspected by {surface.inspection}"))
            continue
        findings.append(Draft("route.inspection_missing", "minor", UNKNOWN, unknown("no inspection"),
                              policy("inspection", "equals", "a route inspection entry for each critical surface"),
                              "critical surfaces and route inspections", "record content",
                              f"no route inspection verifies the {surface.role} {surface.feature}",
                              remedy=f"add an inspection entry for {surface.feature} and name it on the critical surface",
                              recheck=["route.operation_plan", "feature.inspection"], feature=surface.feature))
        parts.append((UNKNOWN, f"{surface.feature} uninspected"))
    if not parts:
        return Result(NOT_APPLICABLE, method, "nothing to plan for this operation kind")
    return combine(method, parts, findings)


def optical(ctx: Context) -> Result:
    """optical.route: purchased transparent part with optical purchase criteria, or identified transparent
    stock (a sourced light_transmission) cut or machined; FDM and resin are eliminated."""
    method = "optical route: purchased transparent part or identified transparent stock; opaque processes eliminated"
    mi = ctx.mi
    route = ctx.part.intent.route
    if mi.is_fact(route):
        return Result(UNKNOWN, method, "route unknown", [Draft(
            "optical.route_unknown", "major", UNKNOWN, unknown("route unknown"),
            policy("optical_route", "one_of", ["purchased transparent part", "identified transparent stock"]),
            "part.yaml intent route", "not recorded", "the optical requirement has no route",
            remedy="choose a purchased transparent part or identified transparent stock", recheck=["optical.route"],
            earliest_stage="parts")])
    kinds = {op.kind for op in route}
    findings: list[Draft] = []
    opaque = sorted(kinds & ADDITIVE)
    if opaque:
        findings.append(Draft("optical.opaque_process", "major", FAIL, text(f"route {', '.join(opaque)}"),
                              policy("optical_route", "one_of", ["purchased transparent part", "identified transparent stock"]),
                              "route kinds against the optical requirement", "record content",
                              f"{', '.join(opaque)} does not make an optical part: the transparency requirement fails",
                              remedy="buy a transparent part with traceable criteria (material grade, optical grade, thickness, "
                                     "supplier evidence), or cut or machine identified transparent stock such as a cast acrylic "
                                     "or polycarbonate sheet grade", recheck=["optical.route", "selection"], earliest_stage="parts"))
        return Result(FAIL, method, "opaque process", findings)
    purchased = [op for op in route if op.kind == "purchased"]
    if purchased:
        purchase = purchased[0].purchase or {}
        missing = [name for name in ("identity", "specification", "optical")
                   if not (isinstance(purchase.get(name), Mapping) and purchase[name].get("status") == "known")]
        if missing:
            findings.append(Draft("optical.purchase_criteria", "major", UNKNOWN, text(f"missing {', '.join(missing)}"),
                                  policy("optical_purchase", "equals", "identity, specification and optical grade recorded"),
                                  "part.yaml route purchase", "not recorded", "an unidentified clear plastic is not an optical part",
                                  remedy=f"record the {', '.join(missing)} with supplier evidence", recheck=["optical.route"],
                                  earliest_stage="parts"))
            return Result(UNKNOWN, method, "purchase criteria incomplete", findings)
        return Result(PASS, method, "purchased transparent part with optical criteria; transmission is physical evidence")
    shaping = kinds - SECONDARY
    if not shaping <= OPTICAL_ROUTES:
        return Result(UNKNOWN, method, f"no optical route contract for {', '.join(sorted(shaping - OPTICAL_ROUTES))}", [Draft(
            "optical.route_unsupported", "major", UNKNOWN, text(f"route {', '.join(sorted(shaping))}"),
            policy("optical_route", "one_of", sorted(OPTICAL_ROUTES | {"purchased"})), "route kinds", "no contract",
            "whether this process keeps the part transparent is unassessed",
            remedy="choose a purchased transparent part or transparent stock cut or machined", recheck=["optical.route"],
            earliest_stage="parts")])
    profile = ctx.record.materials.get(ctx.stock_material_id() or "") if ctx.stock_material_id() else None
    transmission = None
    if profile is not None:
        transmission = next((p for p in profile.properties if p.name == "light_transmission" and p.value.known), None)
    if profile is None or profile.level != "grade" or transmission is None:
        findings.append(Draft("optical.stock_unidentified", "major", UNKNOWN,
                              text(f"stock {profile.id if profile else 'unknown'}"),
                              policy("transparent_stock", "equals", "a grade profile with a sourced light_transmission"),
                              "material profile", "record content", "the stock is not shown to be an identified transparent grade",
                              remedy="record the stock grade's sourced light transmission and optical grade", recheck=["optical.route"],
                              earliest_stage="parts"))
        return Result(UNKNOWN, method, "transparent stock not identified", findings)
    return Result(PASS, method, f"identified transparent stock {profile.id} on {', '.join(sorted(shaping))}; "
                  "transmission of the made part is physical evidence")
