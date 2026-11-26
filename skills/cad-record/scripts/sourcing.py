# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Which part records belong to the delivered product, and the export group of each.

`make_buy` is the sole sourcing authority (part.schema.json). `delivered` is the one predicate every
product export asks (cad-model's product.step and product.glb, the BOM, the release package): a part made
or bought is delivered; existing equipment, which the product mounts onto and which is kept for fit,
interference and motion checks, is not. The record has no other reference-only kind, so a reference body
such as a harness bundle stub is recorded as make_buy existing and falls under the same rule.

`group` names the top-level group an occurrence is placed in by every assembly export: `made`,
`purchased` and, in the in-context export only, `existing`. Standard library only, so FreeCAD job code
(Python 3.11) can load it.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

GROUPS = ("made", "purchased", "existing")  # export group order
EXISTING = "existing"  # the make_buy value outside the delivered product
# The product exports a version writes beside the in-context assembly.step and assembly.glb, and where a release
# package puts the product and the in-context scene. The names are still under review: change them here only
# (and in layout.json).
PRODUCT_STEP = "product.step"
PRODUCT_GLB = "product.glb"
RELEASE_PRODUCT_STEP = f"definition/{PRODUCT_STEP}"
RELEASE_CONTEXT_STEP = "context/assembly.step"
_STEP_STRING = r"'((?:[^']|'')*)'"
_STEP_PRODUCT = re.compile(rf"(?<![A-Z_])PRODUCT\s*\(\s*{_STEP_STRING}\s*,\s*{_STEP_STRING}")  # PRODUCT(id, name, ...)
_PN = re.compile(r"([A-Z]{2,5}-[0-9]{4})(?![0-9])")
_BY_MAKE_BUY = {"make": "made", "buy": "purchased", EXISTING: "existing"}


class SourcingError(ValueError):
    """A part record whose make_buy is missing or not a schema value."""


def group(part: Mapping[str, Any]) -> str:
    """The export group of one part record."""
    make_buy = part.get("make_buy")
    found = _BY_MAKE_BUY.get(make_buy) if isinstance(make_buy, str) else None
    if found is None:
        raise SourcingError(f"{part.get('PN', 'a part')} has make_buy {make_buy!r}; expected make, buy or existing")
    return found


def delivered(part: Mapping[str, Any]) -> bool:
    """True when the part belongs to the delivered product: made or bought, never existing equipment.
    A record without a schema make_buy counts as delivered; validate.py reports it."""
    return part.get("make_buy") != EXISTING


def groups_of(parts: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """PN -> export group for every part record."""
    return {pn: group(part) for pn, part in parts.items()}


def step_pns(text: str) -> set[str]:
    """The part numbers that lead the id or the name of a PRODUCT entity in a STEP file's text: what a product
    export holds, read without a CAD kernel."""
    return {match.group(1) for fields in _STEP_PRODUCT.findall(text) for field in fields
            if (match := _PN.match(field.replace("''", "'")))}
