"""Purchased-item handler: identity, specification, ratings, mounting and availability only. The made-part checks (wall,
overhang, cutter, kerf) belong to made parts. The function of a bought item is its rating."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .core import FAIL, PASS, UNKNOWN, Context, Draft, Result, policy, text, unknown

TRACEABLE = frozenset({"datasheet", "standard", "supplier", "test", "calibration"})

# The standards bodies a bought part's designation may name, alone or combined (DIN EN ISO 4017, ISO/EN/DIN 898-1).
STANDARDS = ("DIN", "ISO", "EN", "ASME", "ANSI")

_BODY = "|".join(STANDARDS)
_NUMBER = r"[A-Z]?\d+(?:\.\d+)*(?:-\d+)*"
_TIMES = r"\s*[x\u00d7]\s*\d+(?:\.\d+)?"
# A size is a metric or unified designator (M5, #10-24, M5x20), or a number pair (5x0.6, 1/4-20 x 1). The standard's own
# number alone (DIN 471) is not one.
_FIT = r"(?:\s*[a-zA-Z]{1,2}\d{1,2})?"  # a tolerance class after the diameter: ISO 2338 - 5 m6 x 20
_SIZE = rf"(?:[M#]\d+(?:\.\d+)?(?:-\d+)?(?:{_TIMES})?|\d+(?:[./]\d+)*(?:-\d+)?{_FIT}{_TIMES})(?!\d)"
# A bare nominal size (ISO 7089 - 5 - 200 HV) follows the standard number after a space, so a part of a multi-part
# standard number (EN 10025-2) is not read as one.
_BARE = r"\d{1,4}(?:\.\d+)?(?=\s|$|[,;)])"
STANDARD_DESIGNATION = re.compile(
    rf"(?<![A-Za-z])(?P<bodies>(?:{_BODY})(?:[ /]+(?:{_BODY}))*)\s+(?P<number>{_NUMBER})"
    rf"(?:(?:\s*[-\u2013\u2014]\s*|\s+){_SIZE}|\s+(?:[-\u2013\u2014]\s+)?{_BARE})")

# Standards that specify features or tolerances (threads, fits, general tolerances), not purchasable parts. A feature
# standard matches by body plus number: ISO 965 covers ISO 965-1, DIN ISO 965-1 and DIN EN ISO 965-1, not DIN 965 (a screw).
FEATURE_STANDARDS = ("ISO 965", "ISO 261", "ISO 262", "ISO 68", "ISO 286", "ISO 2768", "ISO 1101", "ASME Y14.5", "ASME B1.1")
_FEATURE = tuple(tuple(entry.split(" ", 1)) for entry in FEATURE_STANDARDS)

DESIGNATION_RULE = "a standard designation with a size (DIN 471 - 5x0.6), or a manufacturer part number"


def _part_standard(match: re.Match[str]) -> bool:
    bodies = set(re.split(r"[ /]+", match["bodies"]))
    root = re.sub(r"(?:-\d+)+$", "", match["number"])
    return not any(body in bodies and root == number for body, number in _FEATURE)


def designation_parses(value: Any) -> bool:
    """Whether `value` names a recognized part standard and a size, which identifies the part without an order code."""
    return isinstance(value, str) and any(_part_standard(m) for m in STANDARD_DESIGNATION.finditer(value))


def _fact(purchase: Any, name: str) -> Any:
    data = (purchase or {}).get(name)
    return data if isinstance(data, Mapping) else None


def _known(data: Any) -> bool:
    return isinstance(data, Mapping) and data.get("status") == "known"


def _missing(ctx: Context, family: str, what: str, consequence: str, severity: str = "major") -> Result:
    draft = Draft(f"purchase.{family}_unknown", severity, UNKNOWN, unknown(f"{what} not recorded"),
                  policy(f"purchase_{family}", "equals", "known and traceable"), "part.yaml route purchase", "not recorded",
                  consequence, remedy=f"record the {what} from the supplier's datasheet or the standard",
                  recheck=[f"purchase.{family}"], earliest_stage="parts")
    return Result(UNKNOWN, f"purchase {family}", f"{what} unknown", [draft])


def identity(ctx: Context) -> Result:
    data = _fact(ctx.operation.purchase, "identity")
    if not _known(data):
        return _missing(ctx, "identity", "manufacturer and part number or standard designation",
                        "the bought item is not identified: anything could be supplied")
    return _designation(ctx, data)


def _designation(ctx: Context, identity_fact: Mapping[str, Any]) -> Result:
    """`procurement.designation` (the canonical field) names a standard and a size, or the part has a manufacturer part
    number: either one identifies the part, and a standard part needs no order code. A BOM title that parses as a standard
    designation stands in for an empty field, with an advisory."""
    procurement = ctx.part.raw.get("procurement") or {}
    field_value = procurement.get("designation")
    held = f"identified as {identity_fact['value']}; certificates and incoming inspection are evidence the ledger holds"
    if designation_parses(field_value):
        return Result(PASS, "purchase identity", held)
    if procurement.get("manufacturer_part_number"):
        return Result(PASS, "purchase identity", held)
    criterion = policy("identification", "equals", DESIGNATION_RULE)
    if not field_value and designation_parses(ctx.part.title):
        draft = Draft("purchase.designation_in_title", "info", PASS, text(f"title {ctx.part.title}"), criterion,
                      "part.yaml title", "the title is not the canonical designation field",
                      "the designation lives only in the title, which a rename or translation can change",
                      remedy="move the designation into procurement.designation", recheck=["purchase.identity"])
        return Result(PASS, "purchase identity", held, [draft])
    if "manufacturer_part_number" in (procurement.get("open") or {}):
        draft = Draft("purchase.designation_open", "major", UNKNOWN, unknown("manufacturer part number left open"), criterion,
                      "part.yaml procurement.open", "not yet chosen", "what to order is not settled",
                      remedy="record the manufacturer part number, or a standard designation with a size in "
                      "procurement.designation", recheck=["purchase.identity"], earliest_stage="parts")
        return Result(UNKNOWN, "purchase identity", "manufacturer part number open", [draft])
    recorded = field_value or ctx.part.title or "nothing"
    draft = Draft("purchase.designation_missing", "major", FAIL, text(f"designation {recorded}"), criterion,
                  "part.yaml procurement", "none: the record names neither",
                  "the bought item is not identified: a non-standard part without a manufacturer part number, or a "
                  "standard number without a size, cannot be ordered",
                  remedy="record procurement.designation as a standard designation with a size (DIN 471 - 5x0.6), or the "
                  "manufacturer part number", recheck=["purchase.identity"], earliest_stage="parts")
    return Result(FAIL, "purchase identity", "no standard designation with a size and no manufacturer part number", [draft])


def rating(ctx: Context) -> Result:
    data = _fact(ctx.operation.purchase, "ratings")
    if not _known(data):
        return _missing(ctx, "rating", "ratings (load, speed, temperature)",
                        "whether the item survives its service is unknown")
    return Result(PASS, "purchase ratings", f"ratings recorded ({data['value']}); comparing them with the service loads "
                  "is the interface and engineering checks'")


def dimensions(ctx: Context) -> Result:
    purchase = ctx.operation.purchase or {}
    data = _fact(purchase, "specification")
    if not _known(data):
        return _missing(ctx, "dimensions", "specification (standard, grade or datasheet)",
                        "the item's dimensions and material are not traceable")
    sources = list(data.get("sources") or ()) + list(purchase.get("supplier_evidence") or ())
    if not any(s.get("kind") in TRACEABLE for s in sources):
        draft = Draft("purchase.dimensions_untraceable", "minor", UNKNOWN, text(f"specification {data['value']}"),
                      policy("specification_source", "one_of", sorted(TRACEABLE)), "part.yaml route purchase sources",
                      "only user statements back the specification", "the modelled dimensions rest on no datasheet",
                      remedy="attach the supplier datasheet or the standard", recheck=["purchase.dimensions"], earliest_stage="parts")
        return Result(UNKNOWN, "purchase specification", "specification not traceable", [draft])
    return Result(PASS, "purchase specification", "traceable specification; the modelled envelope against the datasheet and "
                  "the interface fit are not compared here")


def mounting(ctx: Context) -> Result:
    pn = ctx.pn
    interfaces = [i for i, item in ctx.record.interfaces.items() if pn in (item.raw.get("from"), item.raw.get("to"))]
    operations = ctx.record.assembly_operations
    ops = [] if ctx.mi.is_fact(operations) else [o.get("id") for o in operations if pn in (o.get("part"), o.get("into"))]
    if not interfaces and not ops:
        draft = Draft("purchase.mounting_unknown", "major", UNKNOWN, unknown("no interface or assembly operation"),
                      policy("mounting", "equals", "an interface and an assembly operation that retain it"),
                      "interfaces.yaml and assembly.yaml operations", "not recorded",
                      "how the item is mounted and retained is unknown",
                      remedy="record its interface and the assembly operation that installs and retains it",
                      recheck=["purchase.mounting"])
        return Result(UNKNOWN, "purchase mounting", "mounting unknown", [draft])
    return Result(PASS, "purchase mounting", f"mounted through {', '.join(interfaces + ops)}; retention and access are the "
                  "interface and assembly checks'")


def availability(ctx: Context) -> Result:
    if not _known(_fact(ctx.operation.purchase, "availability")):
        return _missing(ctx, "availability", "availability (supplier, lead time)", "the item may not be obtainable",
                        severity="minor")
    return Result(PASS, "purchase availability", "availability recorded")


PURCHASED = {"identity": identity, "rating": rating, "dimensions": dimensions, "mounting": mounting,
             "availability": availability}
