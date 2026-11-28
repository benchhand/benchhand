# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Purchased-item handler: identity, specification, ratings, mounting and availability only. The made-part checks (wall,
overhang, cutter, kerf) belong to made parts. The function of a bought item is its rating."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .core import FAIL, PASS, UNKNOWN, Context, Draft, Result, commodity_identity, occurrence_ids, policy, text, unknown

TRACEABLE = frozenset({"datasheet", "standard", "supplier", "test", "calibration"})

DESIGNATION_RULE = "a standard designation with a size (DIN 471 - 5x0.6), or a manufacturer part number"


designation_parses = commodity_identity().designation_parses


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


def _commodity_label(ctx: Context) -> str | None:
    """The order line when this purchase is a commodity item: an eligible designation and no open identity field. One
    decision for the identity and availability checks, the BOM and the build guide (cad-record commodity_identity.py)."""
    label: str | None = commodity_identity().commodity_label(ctx.part.raw, ctx.operation.purchase)
    return label


def identity(ctx: Context) -> Result:
    label = _commodity_label(ctx)
    if label:
        return Result(PASS, "purchase identity", f"identified by designation {label}; any supplier of that standard or "
                      "family is acceptable")
    data = _fact(ctx.operation.purchase, "identity")
    if not _known(data):
        return _missing(ctx, "identity", "manufacturer and part number, or a standard or family designation",
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
    """Mounted: an interface or an operation names the part. A part placed twice or more needs an operation naming
    each occurrence as part or into, since an interface names the part number, not one placement."""
    pn = ctx.pn
    interfaces = list(ctx.mi.interfaces_of(ctx.record, pn))
    operations = ctx.record.assembly_operations
    occurrence_id, placed = occurrence_ids(ctx)
    mine = [o.id for o in placed] if len(placed) > 1 else [pn, *(o.id for o in placed)]
    named: dict[str, set[str]] = {}
    for op in () if ctx.mi.is_fact(operations) else operations:
        for ident in {occurrence_id(op.get("part")), occurrence_id(op.get("into"))} & set(mine):
            named.setdefault(ident, set()).add(op.get("id"))
    ops = sorted({op for idents in named.values() for op in idents})
    if len(placed) > 1:
        missing = [o.ref for o in placed if o.id not in named]
        if missing:
            names = ", ".join(missing)
            draft = Draft("purchase.mounting_unmounted", "major", FAIL, text(f"no assembly operation for {names}"),
                          policy("mounting", "equals", "an assembly operation for every occurrence"),
                          "assembly.yaml occurrences and operations", "every occurrence is read from the inventory",
                          f"{names} of {len(placed)} placements are not installed by any operation",
                          remedy=f"record the assembly operation that installs and retains {names}",
                          recheck=["purchase.mounting"])
            return Result(FAIL, "purchase mounting", f"{names} not mounted", [draft])
        interfaces = []
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
    if _commodity_label(ctx):
        return Result(PASS, "purchase availability", "commodity: obtainable from any supplier of the designation; "
                      "reference catalogue entries are non-binding examples")
    data = _fact(ctx.operation.purchase, "availability")
    claimed = _known(data) and str(data.get("value", "")).strip().lower() == "commodity"
    if not _known(data) or claimed:  # `commodity` is for an eligible designation; any other item needs a named supplier
        return _missing(ctx, "availability", "availability (a named supplier)", "the item may not be obtainable",
                        severity="minor")
    return Result(PASS, "purchase availability", "availability recorded")


PURCHASED = {"identity": identity, "rating": rating, "dimensions": dimensions, "mounting": mounting,
             "availability": availability}
