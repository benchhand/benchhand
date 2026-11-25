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

from typing import Any, Mapping

GROUPS = ("made", "purchased", "existing")  # export group order
_BY_MAKE_BUY = {"make": "made", "buy": "purchased", "existing": "existing"}


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
    """True when the part belongs to the delivered product: made or bought, never existing equipment."""
    return group(part) != "existing"


def groups_of(parts: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """PN -> export group for every part record."""
    return {pn: group(part) for pn, part in parts.items()}
