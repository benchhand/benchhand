# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Additive handlers: FDM and resin. Criteria come from the operation's capability-profile calibrations for the
component's material; where none holds, overhang, bridge, wall, small-feature and small-hole limits come from the
guideline sets of the profile and material (core.Context.limit), graded by service class (core.Context.grade).
Where neither a calibration nor a guideline value holds, a printer check is not assessed (reason no_guideline): a
calibration is optional and blocks no gate (_no_basis).
Geometry is the exported component in its approved pose (bed frame, build direction +Z).

Calibration (or guideline) characteristics read (scope: material, and orientation where given):
  overhang_angle            deg from vertical, the unsupported range demonstrated: criterion angle <= lower
  bridge_span               mm, the unsupported bridge demonstrated: criterion span <= lower
  min_wall                  mm, the thinnest reliable wall: criterion wall >= upper; scope.feature names a
                            feature class (tooth_tip, chamfer, decorative, ...) assessed apart from structure,
                            against its own calibration only
  min_feature               mm, the smallest reliable pin or boss diameter: criterion diameter >= upper
  min_hole                  mm, the smallest reliable hole diameter: criterion diameter >= upper
  hole_diameter_deviation   mm, as-made minus nominal; scope.orientation Z (axis along the build) or XY
  dimensional_deviation     mm, as-made minus nominal of a planar or profile critical feature
  profile_deviation         mm, gear flank or cam profile deviation (scope.feature flank or cam)
  elephant_foot             mm, first-layer flare of bed-contact features
  warp                      mm, flatness deviation of a bed footprint up to scope.size
  min_bed_contact_area      mm2, the smallest reliable first-layer contact: criterion contact area >= upper
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .core import (FAIL, MAXIMUM, MINIMUM, NOT_APPLICABLE, NOT_ASSESSED, PASS, UNKNOWN, Calibrated, Context, Draft, Geometry, Limit,
                   Result, angle, combine, gated, length, policy, source_dict, text, unknown)

FINISHING_KINDS = frozenset({"reaming", "finishing", "cnc", "drilling"})  # operations that can restore a surface
PRINTERS = frozenset({"fdm", "resin"})  # printer calibration is optional on these (benchhand-c7q.30)
NO_BASIS = "no_guideline"  # the not_assessed reason of a printer check with neither a calibration nor a guideline value
EXEMPT_CLASSES = frozenset({"tooth_tip", "flank", "chamfer", "fillet", "decorative", "text", "emboss", "tip", "knurl"})
SOLUBLE_WORDS = ("soluble", "pva", "bvoh", "hips", "interface", "breakaway")
HOLE_CLASSES = {"Z": "axis along the build direction", "XY": "axis in the layer plane", "inclined": "inclined axis"}
EPS = 1e-9
COORD_STEP_MM = 1e-6  # form_check rounds probed coordinates to this step
BOUNDARY_MM = 3 * COORD_STEP_MM


def _k(ctx: Context) -> str:
    kind: str = ctx.operation.kind
    return kind


def _no_basis(ctx: Context, findings: list[Draft], draft: Draft, what: str, otherwise: str,
              why: str = "no calibration or guideline", process: str | None = None, op: Any | None = None) -> tuple[str, str]:
    """`draft`, the finding of a check that has no value to judge against, as a sub-check. On a printer (the `process`
    whose as-made result is judged, this operation's by default; `op` the operation whose stock is judged, this one by
    default) a calibration is optional (benchhand-c7q.30): the check
    is not assessed, a minor finding that blocks no gate and is never a pass. On any other process, or with the stock
    material unidentified (no calibration or guideline could be looked up), it stays UNKNOWN with `otherwise` as its
    coverage."""
    findings.append(draft)
    if (process or _k(ctx)) not in PRINTERS or ctx.stock_material_id(op) is None:
        return UNKNOWN, otherwise
    draft.outcome, draft.severity, draft.reason = NOT_ASSESSED, "minor", NO_BASIS
    return NOT_ASSESSED, f"{what} not assessed: {why}; print and inspect, or calibrate to assess"


def hole_class(axis: Any) -> str:
    """Z, XY or inclined: the hole axis against the build direction (+Z of the bed frame)."""
    cos = abs(float(axis[2]))
    if cos >= math.cos(math.radians(1.0)):
        return "Z"
    if cos <= math.sin(math.radians(1.0)):
        return "XY"
    return "inclined"


def _approved(ctx: Context) -> tuple[bool, str]:
    data = ctx.operation.orientation
    mi = ctx.mi
    if data is None:
        return False, "no orientation recorded"
    if mi.is_fact(data):
        return False, f"orientation {data.status}: {data.reason or 'no reason recorded'}"
    status = data.get("status")
    return status == "approved", f"orientation {status}"


# --- orientation ------------------------------------------------------------------------------------------------

def orientation(ctx: Context) -> Result:
    kind = _k(ctx)
    method = f"{kind} orientation: approved pose, first-layer contact, stability and envelope"
    approved, why = _approved(ctx)
    if not approved:
        draft = Draft(f"process.{kind}.orientation_unapproved", "major", UNKNOWN, text(why),
                      policy("orientation_status", "equals", "approved"), "part.yaml route orientation", why,
                      "first-layer contact, stability, supports and layer direction are not judged in the pose it will print in",
                      remedy="approve the print orientation (route orientation status approved with its decision); "
                             "an envelope auto-fit is never an approved orientation",
                      recheck=[f"process.{kind}", "route.operation_plan"], earliest_stage="detail")
        return Result(UNKNOWN, method, f"orientation not approved ({why})", [draft])
    g = ctx.measured("first-layer contact and stability")
    if isinstance(g, Result):
        return g
    if g.frame != "bed":
        draft = Draft(f"process.{kind}.pose_unmeasured", "major", UNKNOWN, text(f"exported frame {g.frame}"),
                      policy("export_frame", "equals", "bed"), g.method("pose"),
                      "the component was not exported in its approved pose",
                      "first-layer contact and stability are not measured", remedy="rebuild the version with the approved orientation",
                      recheck=[f"process.{kind}.orientation"], earliest_stage="detail")
        return Result(UNKNOWN, method, "pose not measured", [draft])
    findings: list[Draft] = []
    parts: list[tuple[str, str]] = []
    bed = g.facts.get("bed") or {}
    contact = float(bed.get("contact_area_mm2") or 0.0)
    if contact <= EPS:
        findings.append(Draft(f"process.{kind}.first_layer", "major", FAIL, text(f"bed contact {contact:g} mm2"),
                              policy("bed_contact_area", ">=", 0.0, "mm", "a face must lie on the bed"),
                              g.method("planar faces at the lowest z"), "faces within 1e-6 mm of the bed plane",
                              "the part rests on an edge or a point: the first layer has nothing to bond to",
                              remedy="reorient so a planar face lies on the bed, or add a sacrificial flat",
                              alternatives=[{"text": "print on a raft or supports", "tradeoffs": "supported underside finish and removal"}],
                              recheck=[f"process.{kind}.orientation", f"process.{kind}.support"], earliest_stage="detail"))
        parts.append((FAIL, "no first-layer contact"))
    else:
        parts.append(_contact_area(ctx, g, contact, findings))
        centre = g.facts.get("centre_mm")
        if centre and _outside_hull(centre, bed.get("contact_hull") or ()):
            supports = ctx.operation.supports
            supported = isinstance(supports, Mapping) and supports.get("strategy") != "none"
            findings.append(Draft(f"process.{kind}.stability", "minor" if supported else "major",
                                  PASS if supported else FAIL,
                                  text(f"centre of volume at x {centre[0]:.3f}, y {centre[1]:.3f} outside the contact hull"),
                                  policy("centre_of_volume", "equals", "inside the convex hull of the bed contact faces"),
                                  g.method("centre of volume against the convex hull of the bed contact faces"),
                                  "the hull is of the faces' outlines with curved edges sampled; print forces are not modelled",
                                  "the part can tip or peel during printing" if not supported
                                  else "stability relies on the planned supports",
                                  remedy=None if supported else "reorient, widen the base or plan stabilising supports or a brim",
                                  recheck=[f"process.{kind}.orientation"], earliest_stage="detail"))
            parts.append((PASS if supported else FAIL, "stability relies on supports" if supported else "unstable pose"))
        elif centre:
            parts.append((PASS, "centre of volume above the bed contact hull"))
    envelope = _envelope(ctx, g, findings)
    parts.append(envelope)
    _layer_note(ctx, g, findings)
    unassessed = any(f.outcome == NOT_ASSESSED for f in findings)
    return combine(method, parts, findings,
                   reason="no_capability_profile" if envelope[0] == NOT_ASSESSED and not unassessed else _reason(findings))


def _outside_hull(point: Any, hull: Any) -> bool:
    """Whether the x, y of `point` lies outside the counter-clockwise convex polygon `hull`. A hull of fewer
    than three points has no interior, so everything is outside. A point on an edge is inside (stable).
    The producer rounds every coordinate to COORD_STEP_MM, so a point up to BOUNDARY_MM outside an edge is
    on it: 3 steps cover the rounding of the point and of both edge ends."""
    if len(hull) < 3:
        return True
    for i, (x1, y1) in enumerate(hull):
        x2, y2 = hull[(i + 1) % len(hull)]
        cross = (x2 - x1) * (point[1] - y1) - (y2 - y1) * (point[0] - x1)
        if cross < -BOUNDARY_MM * math.hypot(x2 - x1, y2 - y1):  # more than BOUNDARY_MM outside this edge
            return True
    return False


def _contact_area(ctx: Context, g: Geometry, contact: float, findings: list[Draft]) -> tuple[str, str]:
    """The bed contact area against the calibrated minimum (`min_bed_contact_area`, mm2: area >= upper)."""
    kind = _k(ctx)
    cal = ctx.calibrated("min_bed_contact_area")
    measured = {"value": round(contact, 6), "units": "mm2"}
    if not cal.known:
        return _no_basis(ctx, findings, Draft(f"process.{kind}.contact_area_uncalibrated", "minor", UNKNOWN, measured,
                                              cal.criterion("bed_contact_area", ">="), g.method("planar faces at the lowest z"),
                                              "; ".join(cal.reasons), "whether the first layer holds the part to the bed is unassessed",
                                              remedy=ctx.remedy("calibrate the smallest reliable bed contact area"),
                                              recheck=[f"process.{kind}.orientation"]),
                         "bed contact area", "bed contact area not calibrated")
    need = cal.bounds("mm2")[1]
    if contact >= need - 1e-9:
        return PASS, f"first-layer contact {contact:.3g} mm2 >= {need:g} mm2"
    findings.append(Draft(f"process.{kind}.contact_area", "major", FAIL, measured,
                          cal.criterion("bed_contact_area", ">=", need), g.method("planar faces at the lowest z"),
                          "face areas are exact", "the first layer is smaller than the calibrated minimum: the part can peel or shift",
                          remedy="enlarge the bed contact, or add a brim or raft", recheck=[f"process.{kind}.orientation"],
                          earliest_stage="detail"))
    return FAIL, "bed contact below the calibrated minimum"


def _envelope(ctx: Context, g: Geometry, findings: list[Draft]) -> tuple[str, str]:
    kind, profile = _k(ctx), ctx.profile
    if profile is None and ctx.guidelines_without_machine():
        return NOT_ASSESSED, "no machine recorded; the envelope is not assessed"
    raw = (profile.raw.get("envelope") if profile is not None else None) or {}
    known: list[float] = []
    for axis in "xyz":
        item = raw.get(axis)
        if not isinstance(item, Mapping) or item.get("status") != "known":
            break
        known.append(ctx.mi.quantity(item["value"]).to("mm").bounds[0])
    sizes = known if len(known) == 3 else None
    if sizes is None:
        findings.append(Draft(f"process.{kind}.envelope_unknown", "minor", UNKNOWN, unknown("no measured envelope"),
                              unknown("the capability profile records no x, y and z envelope"),
                              "capability profile envelope", "the usable envelope is not recorded",
                              "the approved pose is not checked against the machine",
                              remedy="record the measured envelope in the capability profile",
                              recheck=[f"process.{kind}.orientation"], earliest_stage="parts"))
        return UNKNOWN, "envelope unknown"
    box = g.facts.get("bbox_mm") or {}
    size = [box["max"][i] - box["min"][i] for i in range(3)] if box else None
    if size is None:
        return UNKNOWN, "component size not measured"
    over = [("xyz"[i], size[i], sizes[i]) for i in range(3) if size[i] > sizes[i] + 1e-9]
    if over:
        axis, value, limit = over[0]
        findings.append(Draft(f"process.{kind}.envelope", "major", FAIL, length(value),
                              {"property": f"envelope_{axis}", "operator": "<=", "value": limit, "units": "mm",
                               "source": _envelope_source(profile, axis)},
                              g.method("bounding box in the approved pose"), "exact B-rep bounding box",
                              f"the component does not fit the machine along {axis} in its approved pose",
                              remedy="reorient (with a new approval), split with controlled joints or choose a larger machine",
                              recheck=[f"process.{kind}.orientation"], earliest_stage="detail"))
        return FAIL, "outside the envelope"
    return PASS, "inside the measured envelope in the approved pose"


def _envelope_source(profile: Any, axis: str) -> dict[str, Any]:
    sources = profile.raw["envelope"][axis].get("sources") or []
    return source_dict(sources[0]) if sources else {"kind": "user", "reference": profile.id, "origin": "capability profile"}


def _layer_note(ctx: Context, g: Geometry, findings: list[Draft]) -> None:
    if "structural" not in ctx.functions():
        return
    axis = g.facts.get("build_axis_native")
    where = f"native direction [{', '.join(f'{c:.3f}' for c in axis)}]" if axis else "the approved pose's +Z"
    findings.append(Draft(f"process.{_k(ctx)}.layer_direction", "info", UNKNOWN,
                          text(f"layers stack along {where}"), unknown("interlayer strength against the load directions"),
                          "approved orientation", "the load directions and the interlayer allowables are not part of this check",
                          "a load across the layer planes is carried by interlayer bonding, the weakest direction",
                          remedy="assess layer direction against the load cases with orientation-specific properties",
                          recheck=["engineering.anisotropy"], earliest_stage="analysis", link=False))


# --- supports ---------------------------------------------------------------------------------------------------

def support(ctx: Context) -> Result:
    kind = _k(ctx)
    method = f"{kind} support plan: sampled down-facing normals, downward support rays and the recorded strategy"
    supports = ctx.operation.supports
    mi = ctx.mi
    if supports is None or mi.is_fact(supports):
        why = "no support strategy recorded (absent is unknown, never none)" if supports is None \
            else f"support strategy {supports.status}: {supports.reason or 'no reason recorded'}"
        draft = Draft(f"process.{kind}.support_unplanned", "major", UNKNOWN, text(why),
                      policy("support_strategy", "one_of", ["none", "reachable_noncritical", "finished_critical"]),
                      "part.yaml route supports", why, "where supports start, touch and come off is unknown",
                      remedy="record the support strategy, its contact and start regions and the removal tools and access",
                      recheck=[f"process.{kind}.support"], earliest_stage="detail")
        return Result(UNKNOWN, method, "support strategy unknown", [draft])
    g = ctx.measured("overhangs, bridges and islands")
    if isinstance(g, Result):
        return g
    strategy = supports.get("strategy")
    contacts = {t.get("feature") for t in supports.get("contact_regions") or ()}
    starts = {t.get("feature") for t in supports.get("start_regions") or ()}
    critical = ctx.critical_features()
    overhang = ctx.limit("overhang_angle", MAXIMUM)
    bridge = ctx.limit("bridge_span", MAXIMUM)
    findings: list[Draft] = []
    parts: list[tuple[str, str]] = []
    material = str(supports.get("material") or "")
    soluble = any(word in material.lower() for word in SOLUBLE_WORDS)

    reported: set[str] = set()
    for feature in sorted(contacts & set(critical)):
        if strategy != "finished_critical":
            findings.append(_critical_contact(ctx, feature, critical[feature], soluble, "declared as a non-critical contact"))
            parts.append((FAIL, f"critical {feature} declared as a non-critical support contact"))
            reported.add(feature)

    needing: list[tuple[Mapping[str, Any], str]] = []
    undecided: list[tuple[Mapping[str, Any], str]] = []
    planned: list[tuple[Mapping[str, Any], str]] = []
    judged: dict[str, tuple[Limit, dict[str, Any]]] = {}  # the limit each down-facing face was judged by, at its worst value
    for face in g.faces():
        down = face.get("down") or {}
        area = float(down.get("area_mm2") or 0.0)
        if area <= EPS:
            continue
        _judge(down, overhang, bridge, judged)
        reason = _needs_support(down, overhang, bridge)
        if reason is None:
            continue
        (undecided if reason == "undecided" else needing).append((face, reason))
    for face, reason in needing + undecided:
        feats = set(face.get("features") or ())
        down = face["down"]
        hit = sorted(feats & set(critical))
        start = down.get("start") or {}
        start_hit = sorted(set(start.get("features") or ()) & set(critical))
        limit = _limit_criterion("bridge" if _is_spanned(down) else "overhang", overhang, bridge)
        covered = strategy == "finished_critical" and set(hit + start_hit) <= contacts
        if reason == "undecided" and not (hit or start_hit):
            if feats & contacts:
                planned.append((face, reason))
                continue
            parts.append(_no_basis(ctx, findings, Draft(
                f"process.{kind}.overhang_uncalibrated", "minor", UNKNOWN, angle(down.get("max_angle_deg") or 0.0), limit,
                g.method("sampled down-facing normals"), "; ".join(overhang.reasons + bridge.reasons) or "not calibrated",
                "which down-facing surfaces need support is unknown",
                remedy=ctx.remedy("calibrate the unsupported overhang angle and bridge span for this material and machine, "
                                  "or plan supports on this surface", "plan supports on this surface"),
                recheck=[f"process.{kind}.support"], earliest_stage="detail", feature=_one(feats)),
                f"face {face.get('id')} overhang", f"face {face.get('id')} overhang not calibrated"))
            continue
        if reason == "undecided" and not covered:
            for feature in sorted(set(hit + start_hit) - reported):
                reported.add(feature)
                parts.append(_no_basis(ctx, findings, Draft(
                    f"process.{kind}.support_on_critical_unknown", "major", UNKNOWN, angle(down.get("max_angle_deg") or 0.0),
                    limit, g.method("sampled down-facing normals"), "; ".join(overhang.reasons + bridge.reasons),
                    f"whether supports touch the {critical[feature].role} surface {feature} is unknown",
                    remedy=ctx.remedy("calibrate the unsupported overhang angle and bridge span, or reorient so "
                                      f"{feature} faces up or sideways", f"reorient so {feature} faces up or sideways"),
                    recheck=[f"process.{kind}.support"], earliest_stage="detail", feature=feature),
                    f"support on critical {feature}", f"support on critical {feature} undecided"))
            continue
        if hit or start_hit:
            for feature in sorted(set(hit + start_hit) - reported):
                reported.add(feature)
                verdict = _critical_exception(ctx, feature, critical[feature], supports, contacts, soluble, findings,
                                              starting=feature in start_hit and feature not in hit)
                parts.append((verdict, f"support on critical {feature}"))
            continue
        if strategy == "none":
            findings.append(_unsupported(ctx, g, face, reason, overhang, bridge))
            parts.append((FAIL, f"face {face.get('id')} needs support and the strategy is none"))
            continue
        if not feats:
            findings.append(Draft(f"process.{kind}.support_region_unnamed", "minor", UNKNOWN,
                                  text(f"{down.get('area_mm2', 0):.3g} mm2 down-facing at z {face.get('z_mm')}"),
                                  _limit_criterion(reason, overhang, bridge), g.method("sampled down-facing normals"),
                                  "no feature locator names this surface", "supports here are neither planned nor excluded",
                                  remedy="name the surface with a feature locator and list it as a support contact region",
                                  recheck=[f"process.{kind}.support"], earliest_stage="detail"))
            parts.append((UNKNOWN, f"face {face.get('id')} unnamed"))
            continue
        if not feats & contacts:
            findings.append(Draft(f"process.{kind}.support_region_unplanned", "minor", UNKNOWN,
                                  text(f"{', '.join(sorted(feats))} needs support ({reason})"),
                                  _limit_criterion(reason, overhang, bridge), g.method("sampled down-facing normals"),
                                  "the support plan does not list this surface", "the support plan is incomplete",
                                  remedy=f"add {', '.join(sorted(feats))} to the contact regions, or reorient",
                                  recheck=[f"process.{kind}.support"], earliest_stage="detail", feature=_one(feats)))
            parts.append((UNKNOWN, f"{', '.join(sorted(feats))} not in the plan"))
            continue
        planned.append((face, reason))
        if (start.get("part_mm2") or 0) > EPS:
            on = sorted(set(start.get("features") or ()) - starts - contacts)
            if on or not start.get("features"):
                findings.append(Draft(f"process.{kind}.support_start", "minor", UNKNOWN,
                                      text(f"supports start on the part ({start.get('part_mm2'):.3g} mm2)"),
                                      policy("support_start", "equals", "on the bed or a recorded start region"),
                                      g.method("downward rays from down-facing samples"), "ray samples, not a slicer's support",
                                      "supports standing on the part mark the surface they start on",
                                      remedy=f"record start regions{' ' + ', '.join(on) if on else ''} or reorient",
                                      recheck=[f"process.{kind}.support"], earliest_stage="detail", feature=_one(feats)))
                parts.append((UNKNOWN, "support start not recorded"))
    if planned:
        names = sorted({f for face, _ in planned for f in face.get("features") or ()})
        findings.append(Draft(f"process.{kind}.overhang_planned", "info", PASS,
                              text(f"{len(planned)} surface(s) beyond the unsupported limit: {', '.join(names)}"),
                              _limit_criterion(planned[0][1], overhang, bridge),
                              g.method("sampled down-facing normals"),
                              "support removal damage, distortion and finish are not predicted from area",
                              "supports are planned on reachable non-critical surfaces",
                              recheck=[f"process.{kind}.support"], earliest_stage="detail"))
        findings.append(_trial(ctx, f"support removal at {', '.join(names)} and the supported surface finish"))
        parts.append(_graded(ctx, g, judged, f"supports planned on {', '.join(names)}", findings))
    if not needing and not undecided:
        parts.append(_graded(ctx, g, judged, "no down-facing surface beyond the unsupported limits", findings))
    return combine(method, parts, findings,
                   uncertainty="sampled normals and rays; support toolpaths and removal are not simulated", reason=NO_BASIS)


def _judge(down: Any, overhang: Limit, bridge: Limit, judged: dict[str, tuple[Limit, dict[str, Any]]]) -> None:
    """Record which limit judges this down-facing face (an island needs support whatever the limits say) and keep the
    worst value each limit saw, which a guideline-only finding reports."""
    if down.get("kind") == "island":
        return
    if _is_spanned(down):
        name, limit, value = "bridge_span", bridge, length(down["span_mm"])
    else:
        name, limit, value = "overhang_angle", overhang, angle(down.get("max_angle_deg") or 0.0)
    if name not in judged or value["value"] > judged[name][1]["value"]:
        judged[name] = (limit, value)


def _graded(ctx: Context, g: Geometry, judged: Mapping[str, tuple[Limit, dict[str, Any]]], coverage: str,
            findings: list[Draft]) -> tuple[str, str]:
    """A support pass graded by every limit a face was judged against (Context.grade)."""
    outcome = PASS
    for name, (limit, value) in sorted(judged.items()):
        verdict, coverage = ctx.grade(limit, coverage)
        outcome = UNKNOWN if verdict == UNKNOWN else outcome
    return outcome, coverage


def _one(feats: set[str]) -> str | None:
    return sorted(feats)[0] if len(feats) == 1 else None


def _is_spanned(down: Any) -> bool:
    """Whether `down` is a bridge with a measured span. A flat downward face that is not a strip (a ring, a notch) is
    a bridge with no span. The support check applies the overhang angle to it, as to any overhang."""
    return down.get("kind") == "bridge" and down.get("span_mm") is not None


def _needs_support(down: Any, overhang: Limit, bridge: Limit) -> str | None:
    """island, bridge or overhang when the face needs support; undecided when no limit says; None otherwise.
    A bridge with a measured span is judged by its span alone, any other face by the area beyond the unsupported angle."""
    kind = down.get("kind")
    if kind == "island":
        return "island"
    if _is_spanned(down):
        if not bridge.known:
            return "undecided"
        return "bridge" if bridge.breaks(down["span_mm"], "mm") else None
    if not overhang.known:
        return "undecided" if (down.get("max_angle_deg") or 0.0) > 1e-6 else None
    over = sum(area for deg, area in down.get("angles") or () if overhang.breaks(deg, "deg"))
    return "overhang" if over > EPS else None


def _limit_criterion(reason: str, overhang: Limit, bridge: Limit) -> dict[str, Any]:
    if reason == "bridge":
        return bridge.criterion("bridge_span", "mm")
    if reason == "island":
        return policy("island", "equals", "supported", note="a region that starts in mid-air always needs support")
    return overhang.criterion("overhang_angle", "deg")


def _unsupported(ctx: Context, g: Geometry, face: Any, reason: str, overhang: Limit, bridge: Limit) -> Draft:
    kind, down = _k(ctx), face["down"]
    measured = length(down["span_mm"]) if reason == "bridge" and down.get("span_mm") is not None \
        else angle(down.get("max_angle_deg") or 0.0)
    return Draft(f"process.{kind}.overhang", "major", FAIL, measured, _limit_criterion(reason, overhang, bridge),
                 g.method("sampled down-facing normals"), "sampled normals; edge anchoring approximates bridges",
                 f"a {reason} beyond the unsupported limit with support strategy none sags or fails to print",
                 remedy="plan supports on reachable non-critical surfaces, or reorient (preferred over splitting)",
                 alternatives=[{"text": "local relief: a chamfer or teardrop under the overhang",
                                "tradeoffs": "changes the geometry; needs the design owner's approval"},
                               {"text": "split the part", "tradeoffs": "adds joints, datums, fasteners, a tolerance stack and load paths"}],
                 recheck=[f"process.{kind}.support"], earliest_stage="detail", feature=_one(set(face.get("features") or ())))


def _critical_contact(ctx: Context, feature: str, surface: Any, soluble: bool, why: str) -> Draft:
    kind = _k(ctx)
    note = "; soluble or interface supports alone do not establish precision" if soluble else ""
    return Draft(f"process.{kind}.support_on_critical", "major", FAIL,
                 text(f"support contact on {surface.role} surface {feature} ({why})"),
                 policy("support_contact", "equals", "none on precision fits and running or contact surfaces",
                        note="an exception needs a finishing operation, allowance, tool access and inspection"),
                 ctx.geometry.method("down-facing samples and support rays") if ctx.geometry else "part.yaml route supports",
                 "support scars and their removal are not predicted from geometry",
                 f"the {surface.role} surface {feature} keeps support scars and removal damage: its fit and finish are not "
                 f"what the design states{note}",
                 remedy="reorient to keep supports off it, or plan finished_critical supports with a finishing operation, "
                        "allowance, removal access and inspection",
                 alternatives=[{"text": "machine or ream the surface after printing",
                                "tradeoffs": "a secondary operation, datum transfer and finishing allowance"},
                               {"text": "a purchased bushing or insert at the surface",
                                "tradeoffs": "adds a part, an interface and a retention check"}],
                 recheck=[f"process.{kind}.support", "feature.fit", "feature.inspection"], earliest_stage="detail",
                 feature=feature)


def _critical_exception(ctx: Context, feature: str, surface: Any, supports: Any, contacts: set[str], soluble: bool,
                        findings: list[Draft], *, starting: bool) -> str:
    """FAIL unless finished_critical names this surface with a later finishing operation, an allowance,
    removal access and inspection ids the route holds; a planned exception still needs physical validation."""
    if supports.get("strategy") != "finished_critical" or feature not in contacts:
        why = "supports would start on it" if starting else "unplanned"
        findings.append(_critical_contact(ctx, feature, surface, soluble, why))
        return FAIL
    later = [op for op in ctx.after() if op.kind in FINISHING_KINDS and feature in ctx.targets(op)]
    inspections = ctx.inspections()
    missing = []
    if not later:
        missing.append("a later finishing operation that targets it")
    if not supports.get("allowance"):
        missing.append("an allowance")
    if not (supports.get("removal") or {}).get("access"):
        missing.append("removal access")
    ids = list(supports.get("inspection") or ())
    if not ids or any(i not in inspections for i in ids):
        missing.append("inspection entries of the route")
    if missing:
        findings.append(Draft(f"process.{_k(ctx)}.support_exception", "major", FAIL,
                              text(f"finished_critical support on {feature} lacks {', '.join(missing)}"),
                              policy("support_exception", "equals", "finishing operation, allowance, access and inspection"),
                              "part.yaml route supports and operations", "record content only",
                              f"the supported {surface.role} surface {feature} is not restored to its stated fit",
                              remedy=f"record {', '.join(missing)}, or keep supports off {feature}",
                              recheck=[f"process.{_k(ctx)}.support", "route.operation_plan"], earliest_stage="detail",
                              feature=feature))
        return FAIL
    findings.append(Draft(f"process.{_k(ctx)}.support_exception", "info", PASS,
                          text(f"finished_critical support on {feature}: finished by {later[0].id}"),
                          policy("support_exception", "equals", "finishing operation, allowance, access and inspection"),
                          "part.yaml route supports and operations", "the restored surface is not yet measured",
                          f"{feature} is finished after support removal; only scoped surface and dimensional validation "
                          "accepts it", recheck=["feature.inspection"], earliest_stage="detail", feature=feature))
    findings.append(_trial(ctx, f"the finished {surface.role} surface {feature} after support removal"))
    return PASS


def _trial(ctx: Context, what: str) -> Draft:
    kind = _k(ctx)
    return Draft(f"process.{kind}.evidence_required", "info", UNKNOWN, unknown("no physical evidence assessed here"),
                 unknown("a coupon, slicer review or print trial"), "geometric analysis",
                 "geometry does not validate toolpaths or a successful print",
                 f"{what} is unvalidated until printed", remedy="review the slicer preview and print a coupon or trial",
                 recheck=[f"process.{kind}.trial"], earliest_stage="release", link=False)


# --- geometry ---------------------------------------------------------------------------------------------------

def geometry(ctx: Context) -> Result:
    kind = _k(ctx)
    method = f"{kind} geometry against calibrated criteria: walls by feature class, small features, holes and critical features"
    g = ctx.measured("walls, small features and holes")
    if isinstance(g, Result):
        return g
    findings: list[Draft] = []
    parts = [wall_check(ctx, g, findings), small_feature_check(ctx, g, findings), hole_size_check(ctx, g, findings)]
    parts += hole_fits(ctx, g, findings)
    parts += _critical(ctx, g, findings)
    parts += _receiving(ctx, g, findings)
    return combine(method, parts, findings, uncertainty="sampled wall scan; calibration scatter as recorded",
                   reason=_reason(findings))


def wall_check(ctx: Context, g: Geometry, findings: list[Draft]) -> tuple[str, str]:
    """The thinnest wall against the min_wall of the operation's material: its calibration, else the guideline value of
    its profile (graded by service class: Context.grade). Feature classes such as tooth tips, chamfers and decorations
    are judged against their own calibration only, never exempted silently and never against a structural guideline."""
    kind = _k(ctx)
    walls = g.facts.get("walls") or {}
    cal = ctx.limit("min_wall", MINIMUM)
    if not cal.known:
        return _no_basis(ctx, findings, Draft(
            f"process.{kind}.wall_uncalibrated", "minor", UNKNOWN,
            length(walls["thickness_mm"]) if walls.get("thickness_mm") is not None else unknown("not measured"),
            cal.criterion("wall_thickness", "mm"), g.method("minimum-wall scan"),
            "; ".join(cal.reasons), "the structural wall criterion is unknown",
            remedy=ctx.remedy("calibrate the minimum reliable wall for this material, process and orientation, "
                              "or record the nozzle diameter or line width a guideline needs",
                              "record the nozzle diameter or line width a guideline needs"),
            recheck=[f"process.{kind}.geometry"], earliest_stage="parts"),
            "structural walls", "wall criterion not calibrated")
    need = cal.threshold("mm")
    floor_only = "; the profile states no nozzle or line width, so min_wall is the plain floor" \
        if cal.status == "guideline" and cal.base_mm is None else ""
    thickness = walls.get("thickness_mm")
    if thickness is None:
        findings.append(Draft(f"process.{kind}.wall_unmeasured", "minor", UNKNOWN, unknown("no wall measured"),
                              cal.criterion("wall_thickness", "mm"), g.method("minimum-wall scan"),
                              f"{walls.get('measured', 0)} samples reached an opposing face", "walls are unassessed",
                              remedy="inspect the walls with a section or another probe", recheck=[f"process.{kind}.geometry"]))
        return UNKNOWN, "walls not measured"
    feats = set(walls.get("features") or ())
    classes = {(g.facts.get("features") or {}).get(f, {}).get("kind") for f in feats} | \
        {s.role for f, s in ctx.critical_features().items() if f in feats}
    exempt = sorted(c for c in classes if c in EXEMPT_CLASSES)
    # a feature class is never judged against a structural guideline, and every class's own calibration is checked first:
    # a structural pass never hides a calibrated miss of any class the sample carries
    owns = {c: ctx.calibrated("min_wall", feature=c) for c in exempt}
    missed = sorted(((own.bounds("mm")[1], c) for c, own in owns.items() if own.known and thickness < own.bounds("mm")[1] - 1e-9),
                    reverse=True)
    if missed:
        need_own, worst = missed[0]  # the strictest class minimum the wall misses
        findings.append(Draft(f"process.{kind}.wall_feature_class", "major", FAIL, length(thickness),
                              owns[worst].criterion("wall_thickness", ">=", need_own), g.method("minimum-wall scan"),
                              "sampled scan: the true minimum can be up to the sample spacing thinner",
                              f"the {worst} wall {thickness:.3f} mm thick is below its own calibrated minimum {need_own:g} mm",
                              remedy=f"thicken the {', '.join(c for _, c in missed)} feature", recheck=[f"process.{kind}.geometry"],
                              feature=sorted(feats)[0] if feats else None))
        return FAIL, f"{worst} wall below its calibrated minimum"
    if not cal.breaks(thickness, "mm"):
        return ctx.grade(cal, f"thinnest wall {thickness:.3f} mm >= {cal.status} {need:g} mm{floor_only}")
    if exempt:
        uncalibrated = [c for c in exempt if not owns[c].known]
        if not uncalibrated:  # the wall meets every class calibration: structural walls are not separated by the scan
            findings.append(Draft(f"process.{kind}.wall_feature_class", "info", UNKNOWN, length(thickness),
                                  owns[exempt[0]].criterion("wall_thickness", ">=", max(owns[c].bounds("mm")[1] for c in exempt)),
                                  g.method("minimum-wall scan"), "the scan reports one global minimum",
                                  f"the thinnest wall is on a {', '.join(exempt)} feature within its own calibration; structural "
                                  "walls thicker than it are not separated by this scan",
                                  remedy="section the structural regions to measure their walls", recheck=[f"process.{kind}.geometry"]))
            return UNKNOWN, "structural walls not separated from the exempt feature"
        names = ", ".join(uncalibrated)
        return _no_basis(ctx, findings, Draft(
            f"process.{kind}.wall_feature_class", "minor", UNKNOWN, length(thickness),
            owns[uncalibrated[0]].criterion("wall_thickness", ">="), g.method("minimum-wall scan"),
            f"{names} features are assessed apart from structural walls",
            f"the thinnest wall is on a {names} feature with no calibration of its own",
            remedy=ctx.remedy(f"calibrate min_wall with scope feature {uncalibrated[0]}, or justify the exception in a decision",
                              "justify the exception in a decision"),
            recheck=[f"process.{kind}.geometry"], feature=sorted(feats)[0] if feats else None),
            f"{names} wall", f"{names} wall not calibrated")
    location = walls.get("location_mm")
    findings.append(Draft(f"process.{kind}.wall", "major", FAIL, length(thickness),
                          cal.criterion("wall_thickness", "mm"), g.method("minimum-wall scan"),
                          "sampled scan: the true minimum can be up to the sample spacing thinner",
                          f"a structural wall {thickness:.3f} mm thick at {location} is below the {cal.status} minimum",
                          remedy=ctx.remedy("thicken the wall, or reorient if the calibration is orientation-specific", "thicken the wall"),
                          recheck=[f"process.{kind}.geometry"], feature=sorted(feats)[0] if len(feats) == 1 else None))
    return FAIL, f"structural wall below the {cal.status} minimum{floor_only}"


def small_feature_check(ctx: Context, g: Geometry, findings: list[Draft]) -> tuple[str, str]:
    """The smallest pin or boss (a convex full cylinder) against min_feature."""
    return _smallest(ctx, g, findings, g.pins(), "min_feature", "small_feature", "feature_diameter",
                     ("pin", "small pins and bosses", "convex full cylinders", "the smallest reliable pin or boss",
                      "enlarge it or replace it with a purchased pin"))


def hole_size_check(ctx: Context, g: Geometry, findings: list[Draft]) -> tuple[str, str]:
    """The smallest bore, critical or not, against min_hole: a hole below it closes up or prints out of round."""
    return _smallest(ctx, g, findings, g.holes(), "min_hole", "small_hole", "hole_diameter",
                     ("hole", "small holes", "bore recognition", "the smallest reliable hole",
                      "enlarge it, or drill or ream it after printing"))


def _smallest(ctx: Context, g: Geometry, findings: list[Draft], items: list[Mapping[str, Any]], characteristic: str,
              check: str, prop: str, words: tuple[str, str, str, str, str]) -> tuple[str, str]:
    """The smallest diameter of `items` against the `characteristic` limit (Context.limit, graded by Context.grade)."""
    noun, plural, method, reliable, remedy = words
    if not items or (characteristic == "min_hole" and ctx.operation.kind != "fdm"):  # only FDM has a hole guideline
        return NOT_APPLICABLE, ""
    kind = _k(ctx)
    limit = ctx.limit(characteristic, MINIMUM)
    smallest = min(items, key=lambda p: p["diameter_mm"])
    diameter, feature = smallest["diameter_mm"], _one(set(smallest.get("features") or ()))
    if not limit.known:
        return _no_basis(ctx, findings, Draft(f"process.{kind}.{check}_uncalibrated", "minor", UNKNOWN, length(diameter),
                                              limit.criterion(prop, "mm"), g.method(method), "; ".join(limit.reasons),
                                              f"{plural} are unassessed", remedy=ctx.remedy(f"calibrate {reliable}"),
                                              recheck=[f"process.{kind}.geometry"]),
                         plural, f"{plural} not calibrated")
    need = limit.threshold("mm")
    if not limit.breaks(diameter, "mm"):
        return ctx.grade(limit, f"smallest {noun} {diameter:g} mm >= {limit.status} {need:g} mm")
    findings.append(Draft(f"process.{kind}.{check}", "major", FAIL, length(diameter), limit.criterion(prop, "mm"),
                          g.method(method), "cylinder radius is exact",
                          f"the {noun} is below the {limit.status} {reliable.removeprefix('the ')}",
                          remedy=remedy, recheck=[f"process.{kind}.geometry"], feature=feature))
    return FAIL, f"{plural} below the {limit.status} minimum"


def hole_fits(ctx: Context, g: Geometry, findings: list[Draft], characteristic: str = "hole_diameter_deviation",
              oriented: bool = True) -> list[tuple[str, str]]:
    """Each critical bore of the component: a finished one needs a later finishing operation; an as-made
    one needs the calibrated `characteristic` (as-made minus nominal, by build orientation when `oriented`)
    to keep the bore inside its stated diameter limits."""
    critical = ctx.critical_features()
    parts: list[tuple[str, str]] = []
    for hole in g.holes():
        feats = set(hole.get("features") or ()) & set(critical)
        if not feats:
            continue  # a non-critical hole's as-made size governs no fit; compensation stays a slicer setting
        parts += [_bore_fit(ctx, g, findings, hole, feature, critical[feature], characteristic, oriented)
                  for feature in sorted(feats)]  # every critical surface the bore carries, each against its own limits
    return parts


def _bore_fit(ctx: Context, g: Geometry, findings: list[Draft], hole: Mapping[str, Any], feature: str, surface: Any,
              characteristic: str, oriented: bool) -> tuple[str, str]:
    """One critical surface of a bore against its own diameter limits (hole_fits)."""
    kind = _k(ctx)
    orientation = hole_class(hole["axis"])
    if surface.dimension_state != "as_manufactured":
        later = [op for op in ctx.after() if op.kind in FINISHING_KINDS and feature in ctx.targets(op)]
        if later:
            return PASS, f"{feature} is finished by {later[0].id}"
        findings.append(Draft(f"process.{kind}.finish_missing", "major", FAIL,
                              text(f"{feature} is stated {surface.dimension_state}"),
                              policy("finishing_operation", "equals", "a later operation that targets the feature"),
                              "part.yaml route", "record content only",
                              f"the {surface.role} {feature} is specified finished but no later operation finishes it",
                              remedy=f"add a reaming or finishing operation targeting {feature} with its allowance",
                              recheck=[f"process.{kind}.geometry", "route.operation_plan"], feature=feature))
        return FAIL, f"{feature} has no finishing operation"
    limits, unsupported = _diameter_limits(ctx, surface)
    if not limits and not unsupported:
        findings.append(Draft(f"process.{kind}.hole_criterion_missing", "minor", UNKNOWN, length(hole["diameter_mm"]),
                              unknown("no diameter criterion on the critical surface"), g.method("bore recognition"),
                              "the fit's diameter limits are not recorded", f"whether {feature} holds its fit is unknown",
                              remedy=f"record the {feature} diameter criterion (between, mm)",
                              recheck=[f"process.{kind}.geometry"], feature=feature))
        return UNKNOWN, f"{feature} has no diameter criterion"  # a record gap stays open whatever the basis
    cal = ctx.governing(characteristic, orientation=orientation if oriented else None, size=hole["diameter_mm"])
    if not cal.known:
        return _no_basis(ctx, findings, Draft(
            f"process.{kind}.hole_uncalibrated", "major", UNKNOWN, length(hole["diameter_mm"]),
            cal.criterion(characteristic), g.method("bore recognition"), "; ".join(cal.reasons),
            f"the as-made size of the {surface.role} {feature}"
            f"{' (' + HOLE_CLASSES[orientation] + ')' if oriented else ''} is unknown",
            remedy=ctx.remedy(f"make coupons in this material{' and orientation (' + orientation + ')' if oriented else ''}"
                              " and record the calibration, or ream the bore", "ream the bore"),
            recheck=[f"process.{kind}.geometry"], feature=feature),
            f"{feature} {characteristic}", f"{feature} deviation not calibrated")
    low, high = cal.bounds("mm")
    made = (hole["diameter_mm"] + low, hole["diameter_mm"] + high)
    broken = next((c for c in limits if _DIAMETER_BREAKS[c["operator"]](made, c["value"])), None)
    if broken is not None:
        findings.append(Draft(f"process.{kind}.hole_fit", "major", FAIL,
                              {"lower": round(made[0], 6), "upper": round(made[1], 6), "units": "mm"},
                              broken, g.method(f"bore recognition plus {cal.status} deviation"),
                              f"{cal.basis} scatter{'; ' + HOLE_CLASSES[orientation] if oriented else ''}",
                              f"the as-made {surface.role} {feature} cannot hold its stated diameter limits",
                              remedy=ctx.remedy(f"ream or machine {feature} to size, or compensate the model and re-calibrate",
                                                f"ream or machine {feature} to size, or compensate the model"),
                              alternatives=[{"text": "a purchased bushing", "tradeoffs": "adds a part and a press fit"}],
                              recheck=[f"process.{kind}.geometry", "feature.fit"], feature=feature))
        return FAIL, f"{feature} outside its limits as made"
    if unsupported:  # no miss on the criteria judged, but another cannot be: open, never a pass
        return UNKNOWN, f"{feature} diameter criterion with operator {', '.join(unsupported)} is not judged"
    return ctx.settle(cal, f"{feature} within its limits with the {cal.status} {characteristic}")


# Whether the as-made diameter interval (low, high) breaks a bound; `>` and `<` are strict, so the limit itself breaks them.
_DIAMETER_BREAKS: dict[str, Callable[[tuple[float, float], Any], bool]] = {
    "between": lambda made, value: made[0] < value[0] - 1e-9 or made[1] > value[1] + 1e-9,
    ">=": lambda made, value: made[0] < value - 1e-9,
    ">": lambda made, value: made[0] <= value + 1e-9,
    "<=": lambda made, value: made[1] > value + 1e-9,
    "<": lambda made, value: made[1] >= value - 1e-9,
}


def _diameter_limits(ctx: Context, surface: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """The diameter criteria of `surface` in mm, and the operators of those a bore cannot be judged against."""
    items, unsupported = ctx.split_criteria(surface, "diameter", _DIAMETER_BREAKS)
    convert = ctx.mi.convert
    limits = []
    for item in items:
        units = item.get("units", "mm")
        value = item["value"]
        value = [convert(v, units, "mm") for v in value] if item["operator"] == "between" else convert(value, units, "mm")
        limits.append({"property": "diameter", "operator": item["operator"], "value": value, "units": "mm", "source": item.get("source")})
    return limits, unsupported


def _tolerance(ctx: Context, surface: Any, characteristic: str) -> tuple[tuple[float, float] | None, list[str]]:
    """The `characteristic between` tolerance a critical surface states for its as-made deviation (mm), and the operators
    of its other criteria of that property, which this check cannot judge."""
    items, unsupported = ctx.split_criteria(surface, characteristic, ("between",))
    bands = [tuple(ctx.mi.convert(v, item.get("units", "mm"), "mm") for v in item["value"]) for item in items]
    if not bands:
        return None, unsupported
    return (max(b[0] for b in bands), min(b[1] for b in bands)), unsupported  # every stated band must hold


def _critical(ctx: Context, g: Geometry, findings: list[Draft]) -> list[tuple[str, str]]:
    """Every critical surface that is not a bore: its feature must be located, and its as-made deviation (with the
    first-layer flare of a bed feature) must stay inside the deviation tolerance it states. A criterion no check reads
    is not assessed (no_check), or fails when its band is narrower than the as-printed spread; one that states none is open."""
    kind = _k(ctx)
    holes = {f for hole in g.holes() for f in hole.get("features") or ()}
    parts: list[tuple[str, str]] = []
    for feature, surface in sorted(ctx.critical_features().items()):
        if g.feature(feature) is None:
            findings.append(Draft(f"process.{kind}.feature_unlocated", "major", UNKNOWN, unknown("no locator matched"),
                                  unknown("the feature's geometry"), g.method("feature locators"),
                                  "the critical surface has no manufacturing-state locator on this component",
                                  f"the {surface.role} {feature} is not assessed",
                                  remedy="add the feature to drawing_features (same state) or manufacture_features",
                                  recheck=[f"process.{kind}.geometry"], feature=feature))
            parts.append((UNKNOWN, f"{feature} not located"))
            continue
        if surface.dimension_state != "as_manufactured":
            continue
        criteria = ctx.surface_raw(surface).get("criteria") or ()
        consumed = set(_judged_properties(ctx))
        if feature in holes:
            consumed.add("diameter")  # hole_fits judges a bore's diameter (and a bore with no criterion)
            parts += [_unchecked(ctx, g, surface, item, findings) for item in criteria if item.get("property") not in consumed]
            continue
        characteristic = "profile_deviation" if surface.role in ("flank", "cam") else "dimensional_deviation"
        consumed.add(characteristic)
        band, unsupported = _tolerance(ctx, surface, characteristic)
        if band is not None:
            parts.append(_deviation(ctx, g, surface, characteristic, band, unsupported, findings))
        elif unsupported:
            parts.append((UNKNOWN, f"{feature} {characteristic} criterion with operator {', '.join(unsupported)} is not judged"))
        elif not criteria:
            findings.append(Draft(f"process.{kind}.feature_tolerance_missing", "minor", UNKNOWN, unknown("no tolerance"),
                                  unknown(f"the {characteristic} tolerance of the critical surface"),
                                  g.method("feature locators"), "the critical surface states no criterion",
                                  f"whether the {surface.role} {feature} holds its size as printed is unknown",
                                  remedy=f"state the {characteristic} tolerance of {feature} (between, mm)",
                                  recheck=[f"process.{kind}.geometry"], feature=feature))
            parts.append((UNKNOWN, f"{feature} states no tolerance"))
        parts += [_unchecked(ctx, g, surface, item, findings) for item in criteria if item.get("property") not in consumed]
        parts.append((NOT_APPLICABLE, ""))  # the criteria judged elsewhere (flatness, ...) have their own checks
    return parts


# The critical-surface properties another check of a printed route reads for every feature: flatness in thermal
# distortion. Per feature, _critical adds the one it consumes itself: a bore's diameter (hole_fits) or the deviation of
# its role. Every other criterion goes through _unchecked: the band-width check, else an explicit open finding.
JUDGED_PROPERTIES = frozenset({"flatness"})
# The criterion properties that name a dimensional size of the model (a length the print can miss), so a band of one in
# a length unit can be compared with the as-printed spread. The table is explicit: a name is never read as a size from
# its suffix (roughness_height is a roughness). A deviation, roughness or flatness is never a size; any property not
# listed stays an open finding.
SIZE_PROPERTIES = frozenset({
    "thickness", "wall_thickness", "depth", "seat_depth", "bore_depth", "pocket_depth", "height", "boss_height", "width",
    "tongue_width", "slot_width", "groove_width", "length", "diameter", "radius", "distance", "spacing", "clearance",
    "side_clearance", "gap", "size",
})
NOT_SIZES = ("roughness", "deviation", "flatness")  # explicitly excluded, whatever the table says


def _is_size(ctx: Context, prop: str, units: str | None) -> bool:
    """Whether a criterion of `prop` in `units` states a dimensional size: a listed property, none of NOT_SIZES, in a
    length unit."""
    return (prop in SIZE_PROPERTIES and not any(word in prop for word in NOT_SIZES)
            and units is not None and _length_mm(ctx, 1.0, units) is not None)


def _judged_properties(ctx: Context) -> frozenset[str]:
    """JUDGED_PROPERTIES plus join_distortion when a later joining operation judges the surface as made."""
    joined = any(op.kind == "joining" for op in ctx.after())
    return JUDGED_PROPERTIES | ({"join_distortion"} if joined else frozenset())


def _length_mm(ctx: Context, value: Any, units: str | None) -> float | None:
    """`value` in mm when `units` is a length, else None."""
    if units is None:
        return None
    try:
        return float(ctx.mi.convert(value, units, "mm"))
    except Exception:  # noqa: BLE001 - any unit the converter refuses is not a length
        return None


def _unchecked(ctx: Context, g: Geometry, surface: Any, item: Mapping[str, Any], findings: list[Draft]) -> tuple[str, str]:
    """A criterion no check measures on the model: never a pass. A `between` band of a dimensional size narrower than the
    spread of the governing deviation (calibration, else guideline, by size) fails, since no nominal holds it; anything
    else is an open (UNKNOWN) finding, since the gap is check coverage, not a printer basis."""
    kind, feature, prop = _k(ctx), surface.feature, str(item.get("property"))
    units = item.get("units")
    if _is_size(ctx, prop, units) and item.get("operator") == "between" and isinstance(item.get("value"), (list, tuple)):
        low, high = (_length_mm(ctx, v, units) for v in item["value"])
        if low is not None and high is not None:
            spread = ctx.governing("dimensional_deviation", size=max(abs(low), abs(high)))
            if spread.known:
                lower, upper = spread.bounds("mm")
                if high - low < upper - lower - 1e-9:
                    findings.append(Draft(f"process.{kind}.criterion_band", "major", FAIL,
                                          {"value": round(high - low, 6), "units": "mm"}, {**item},
                                          g.method(f"band width against the {spread.status} deviation spread"), spread.basis,
                                          f"the {prop} band of {feature} ({high - low:g} mm) is narrower than the as-printed "
                                          f"spread ({upper - lower:g} mm): no model size holds it",
                                          remedy=ctx.remedy(f"widen the {prop} band, finish {feature} after printing, or calibrate "
                                                            "a tighter dimensional_deviation",
                                                            f"widen the {prop} band, or finish {feature} after printing"),
                                          recheck=[f"process.{kind}.geometry"], feature=feature))
                    return FAIL, f"{feature} {prop} band narrower than the as-printed spread"
    # a check-coverage gap, not a missing printer basis: the finding is explicit and the obligation stays open (UNKNOWN)
    findings.append(Draft(f"process.{kind}.criterion_unchecked", "major", UNKNOWN, unknown("not measured on the model"),
                          {**item}, g.method("feature locators"), f"no check measures {prop} on the model",
                          f"whether the {surface.role} {feature} meets its {prop} criterion is not checked",
                          remedy=f"print and inspect {feature} for {prop}, or state it as a tolerance a check reads",
                          recheck=[f"process.{kind}.geometry"], feature=feature))
    return UNKNOWN, f"{feature} {prop} not checked: no check measures it"


def _reason(findings: Sequence[Draft]) -> str:
    """The not_assessed reason of a check result: the first not-assessed finding's, else no_guideline."""
    return next((f.reason for f in findings if f.outcome == NOT_ASSESSED and f.reason), NO_BASIS)


def _largest(g: Geometry) -> float | None:
    """The diagonal of the component's bounding box (mm): no feature of it, straight or diagonal, is longer. The longest
    box axis is no such bound for a feature that runs across the part."""
    box = g.facts.get("bbox_mm") or {}
    if not box.get("min") or not box.get("max"):
        return None
    return math.sqrt(sum((float(high) - float(low)) ** 2 for low, high in zip(box["min"], box["max"])))


def _deviation(ctx: Context, g: Geometry, surface: Any, characteristic: str, band: tuple[float, float],
               unsupported: list[str], findings: list[Draft]) -> tuple[str, str]:
    """The as-made deviation of one critical feature (its calibration, else the guideline interval) plus, on the bed,
    the elephant foot, against its stated `band`."""
    kind, feature = _k(ctx), surface.feature
    cal = ctx.governing(characteristic, feature=surface.role if characteristic == "profile_deviation" else None,
                        size=_largest(g))
    if not cal.known:
        return _no_basis(ctx, findings, Draft(
            f"process.{kind}.feature_uncalibrated", "major", UNKNOWN, unknown("as-made deviation"),
            cal.criterion(characteristic), g.method("feature locators"), "; ".join(cal.reasons),
            f"the as-printed accuracy of the {surface.role} {feature} is unknown",
            remedy=ctx.remedy(f"calibrate {characteristic} for this material and orientation with coupons, or finish the surface",
                              "finish the surface"),
            recheck=[f"process.{kind}.geometry", "feature.fit"], feature=feature),
            f"{feature} {characteristic}", f"{feature} {characteristic} not calibrated")
    low, high = cal.bounds("mm")
    criterion = {"property": characteristic, "operator": "between", "value": [band[0], band[1]], "units": "mm",
                 "source": dict(next(c for c in ctx.surface_raw(surface)["criteria"] if c.get("property") == characteristic)["source"])}
    if low < band[0] - 1e-9 or high > band[1] + 1e-9:
        findings.append(Draft(f"process.{kind}.feature_deviation", "major", FAIL,
                              {"lower": round(low, 6), "upper": round(high, 6), "units": "mm"}, criterion,
                              g.method(f"feature locators plus {cal.status} deviation"), cal.basis,
                              f"the as-printed {surface.role} {feature} can leave its {characteristic} tolerance",
                              remedy=ctx.remedy(f"finish {feature} after printing, relax its tolerance, or calibrate a tighter {characteristic}",
                                                f"finish {feature} after printing, or relax its tolerance"),
                              recheck=[f"process.{kind}.geometry", "feature.fit"], feature=feature))
        return FAIL, f"{feature} {characteristic} outside its tolerance"
    if any(f.get("bed") for f in g.feature_faces(feature)):
        foot = ctx.governing("elephant_foot")
        if not foot.known:
            return _no_basis(ctx, findings, Draft(
                f"process.{kind}.elephant_foot", "minor", UNKNOWN, unknown("first-layer flare"),
                foot.criterion("elephant_foot"), g.method("bed contact faces"), "; ".join(foot.reasons),
                f"the {surface.role} {feature} starts at the bed, where the first layer flares",
                remedy=ctx.remedy("calibrate the elephant foot or chamfer the bed edge of the feature",
                                  "chamfer the bed edge of the feature"),
                recheck=[f"process.{kind}.geometry"], feature=feature),
                f"{feature} elephant_foot", f"{feature} elephant foot unknown")
        flared = high + foot.bounds("mm")[1]
        if flared > band[1] + 1e-9:
            findings.append(Draft(f"process.{kind}.elephant_foot", "major", FAIL, length(flared), criterion,
                                  g.method(f"bed contact faces plus {foot.status} first-layer flare"), f"{cal.basis}; {foot.basis}",
                                  f"the first-layer flare takes the {surface.role} {feature} past its {characteristic} tolerance",
                                  remedy="chamfer the bed edge of the feature, or reorient it off the bed",
                                  recheck=[f"process.{kind}.geometry"], feature=feature))
            return FAIL, f"{feature} elephant foot outside its tolerance"
    if unsupported:
        return UNKNOWN, f"{feature} {characteristic} criterion with operator {', '.join(unsupported)} is not judged"
    return ctx.settle(cal, f"{feature} {characteristic} {cal.status} within {band[0]:g}..{band[1]:g} mm")


def _receiving(ctx: Context, g: Geometry, findings: list[Draft]) -> list[tuple[str, str]]:
    """Inserts, heat-set inserts and press fits into this component's printed bores. A critical bore's as-made size is
    judged by hole_fits against its diameter limits; retention itself waits for the print trial (an info note). A bore
    no critical surface names has no limits to hold, so it is open, never a pass on an interval alone."""
    kind, mi = _k(ctx), ctx.mi
    operations = ctx.record.assembly_operations
    if mi.is_fact(operations):
        return []
    critical = ctx.critical_features()
    parts: list[tuple[str, str]] = []
    for item in operations:
        if item.get("into") != ctx.pn or item.get("kind") not in ("press_fit", "heat_set", "insert"):
            continue
        for feature in (item.get("acceptance") or {}).get("features") or ():
            hole = next((h for h in g.holes() if feature in (h.get("features") or ())), None)
            if hole is None:
                continue  # the feature lies on another component or is not a bore of this one
            findings.append(Draft(f"process.{kind}.receiving_feature", "info", UNKNOWN, length(hole["diameter_mm"]),
                                  unknown("a retention test with the actual insert or pin"), g.method("bore recognition"),
                                  "retention depends on the as-printed bore, the material's creep and the insert",
                                  f"{item.get('kind')} {item.get('id')} into {feature}: retention is unvalidated",
                                  remedy="test retention on a printed coupon with the actual insert or pin",
                                  recheck=["assembly.order", f"process.{kind}.trial"], earliest_stage="release",
                                  feature=feature, link=False))
            if feature in critical:
                parts.append((NOT_APPLICABLE, ""))  # hole_fits judges its size against its stated limits
                continue
            findings.append(Draft(f"process.{kind}.receiving_criterion_missing", "major", UNKNOWN, length(hole["diameter_mm"]),
                                  unknown(f"the diameter limits {feature} needs for the {item.get('kind')}"),
                                  g.method("bore recognition"), "no critical surface states this bore's limits",
                                  f"whether {feature} holds {item.get('kind')} {item.get('id')} is unknown",
                                  remedy=f"declare {feature} a critical bore with the diameter limits the {item.get('kind')} needs",
                                  recheck=[f"process.{kind}.geometry", "assembly.order"], feature=feature))
            parts.append((UNKNOWN, f"{feature} receives {item.get('kind')} with no stated limits"))
    return parts


# --- thermal distortion -----------------------------------------------------------------------------------------

def thermal_distortion(ctx: Context) -> Result:
    kind = _k(ctx)
    method = f"{kind} shrinkage, warp and first-layer flare against calibrations"
    g = ctx.measured("warp and shrinkage")
    if isinstance(g, Result):
        return g
    findings: list[Draft] = []
    parts: list[tuple[str, str]] = []
    bed = g.facts.get("bed") or {}
    extent = bed.get("contact_extent")  # exact contact-face extents, apart from the sampled stability hull
    footprint = max(extent["max"][i] - extent["min"][i] for i in (0, 1)) if extent else 0.0
    # calibration selection filters by footprint first (a smaller coupon never hides a larger one), then the guideline
    cal = ctx.calibrated("warp", size=footprint)
    undersized = [] if cal.known else [r for r in cal.reasons if ctx.mi.SIZE_SCOPE_REASON in r]
    warp: Calibrated | None = cal if cal.known else ctx.guideline_value("warp", cal, size=footprint)
    if undersized and warp is not None:
        note = Draft(f"process.{kind}.warp_scope", "info" if warp.known else "minor", UNKNOWN, length(footprint),
                     unknown("; ".join(undersized)), g.method("bed footprint"), "the calibrations cover smaller footprints only",
                     "warp of this footprint is outside the calibrated scope"
                     + ("; the guideline value governs" if warp.known else ""),
                     remedy=ctx.remedy("calibrate warp at this footprint size"), recheck=[f"process.{kind}.thermal_distortion"],
                     link=not warp.known)
        if warp.known:
            findings.append(note)
        else:
            parts.append(_no_basis(ctx, findings, note, "warp", "footprint beyond the warp calibration",
                                   why="the warp calibrations cover smaller footprints only"))
            warp = None
    if warp is not None and not warp.known:
        parts.append(_no_basis(ctx, findings, Draft(
            f"process.{kind}.warp_uncalibrated", "minor", UNKNOWN, length(footprint), warp.criterion("warp"),
            g.method("bed footprint"), "; ".join(warp.reasons), "warp of the bed footprint is unknown",
            remedy=ctx.remedy("calibrate warp for this material, enclosure and footprint size"),
            recheck=[f"process.{kind}.thermal_distortion"]), "warp", "warp not calibrated"))
    elif warp is not None:
        parts.append(_flatness(ctx, warp, findings))
    return combine(method, parts, findings, uncertainty="calibrated scatter; part-specific thermal history is not simulated",
                   reason=NO_BASIS)


def _flatness(ctx: Context, warp: Calibrated, findings: list[Draft]) -> tuple[str, str]:
    worst = warp.bounds("mm")[1]
    unsupported: list[str] = []
    stated = False
    for feature, surface in ctx.critical_features().items():
        if surface.dimension_state != "as_manufactured":
            continue
        limits, other = ctx.split_criteria(surface, "flatness", ctx.mi.UPPER_BOUND_OPERATORS)
        unsupported += other
        stated = stated or bool(limits)
        for item in limits:
            limit = ctx.mi.convert(item["value"], item.get("units", "mm"), "mm")
            if ctx.mi.exceeds_upper_bound(worst, item["operator"], limit, 1e-9):
                findings.append(Draft(f"process.{_k(ctx)}.warp", "major", FAIL, length(worst),
                                      {**item, "units": item.get("units", "mm")}, f"{warp.status} warp against the criterion",
                                      warp.basis,
                                      f"the as-printed {feature} can exceed its flatness limit",
                                      remedy=f"finish {feature} after printing or reorient", feature=feature,
                                      recheck=[f"process.{_k(ctx)}.thermal_distortion"]))
                return FAIL, f"{feature} flatness"
    if unsupported:  # a miss is a FAIL above; otherwise a criterion this check cannot judge leaves the result open
        return UNKNOWN, f"flatness criterion with operator {', '.join(sorted(set(unsupported)))} is not judged against calibrated warp"
    if not stated:  # TODO(benchhand-tw6.24): report "not assessed" instead of UNKNOWN when CAD_CALIBRATION is off
        return UNKNOWN, f"no critical surface states a flatness limit to judge the {warp.status} warp against"
    return ctx.settle(warp, "warp within the stated flatness limits for this footprint")


# --- resin ------------------------------------------------------------------------------------------------------

def drainage(ctx: Context) -> Result:
    method = "resin drainage: enclosed voids that trap uncured resin"
    g = ctx.measured("drainage and trapped volumes")
    if isinstance(g, Result):
        return g
    voids = int(g.facts.get("shells") or 0) - int(g.facts.get("solids") or 0)
    if voids > 0:
        draft = Draft("process.resin.trapped_volume", "major", FAIL, {"value": voids, "units": "count"},
                      policy("enclosed_voids", "<=", 0, "count"), g.method("shells per solid"), "exact B-rep shells",
                      "an enclosed cavity traps uncured resin and can crack the part during curing",
                      remedy="add drain and vent holes reachable for washing", recheck=["process.resin.drainage"])
        return Result(FAIL, method, "enclosed voids", [draft])
    return Result(PASS, method, "no enclosed void; open cups and suction regions are not assessed",
                  [_trial(ctx, "drainage of open cups and suction regions in the approved orientation")])


def post_cure(ctx: Context) -> Result:
    method = "resin wash and post-cure plan"
    profile, op = ctx.profile, ctx.operation
    missing = []
    for name in ("wash", "post_cure"):
        fact = profile.parameters.get(name) if profile is not None else None
        if fact is None or not fact.known:
            missing.append(f"capability {name}")
    if not op.raw.get("post_processing"):
        missing.append("the operation's post_processing")
    findings = [Draft("process.resin.ageing", "info", UNKNOWN, unknown("no ageing or stability evidence"),
                      unknown("ageing, brittleness and dimensional stability of the cured resin"), "record content",
                      "curing state, ageing and brittleness are not geometric", "long-term behaviour is unvalidated",
                      remedy="test cured coupons for the service conditions", recheck=["process.resin.trial"],
                      earliest_stage="release", link=False)]
    if missing:
        findings.append(Draft("process.resin.post_cure_unplanned", "minor", UNKNOWN, text(f"missing {', '.join(missing)}"),
                              policy("post_cure_plan", "equals", "wash, cure and post-processing recorded"),
                              "record content", "record content only", "cleaning and curing are unplanned",
                              remedy="record the wash and post-cure equipment and the post-processing steps",
                              recheck=["process.resin.post_cure"]))
        return Result(UNKNOWN, method, f"missing {', '.join(missing)}", findings)
    return Result(PASS, method, "wash, post-cure and post-processing recorded", findings)


FDM = {"orientation": gated(orientation), "geometry": gated(geometry), "support": gated(support),
       "thermal_distortion": gated(thermal_distortion)}
RESIN = {"orientation": gated(orientation), "geometry": gated(geometry), "support": gated(support),
         "drainage": drainage, "post_cure": gated(post_cure)}
