# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""A load case that names the stop reaction it comes from (#153).

An FEA load may carry `reaction: IF-###`, the interface whose stop reaction the load stands for. Before the case
solves, fea_static reads the stop-reaction bundle that cad-analysis stop_reaction.py published for the version
(hw/checks/<V>/stop-reaction/). It refuses the case, with a code of its own, when the bundle cannot carry the load:

- reaction_needs_version: the case is standalone, so no version has a bundle to read.
- reaction_missing: the version has no stop-reaction bundle, or the bundle holds no assessment of the interface.
- reaction_stale: the bundle fails its seal, its files cannot be read, it belongs to another build of the version,
  or stop_reaction.py (with the modules and data it reads, #140's code table) changed since the bundle was made.
- reaction_unbounded: a stop of the interface touches the pivot axis, so its rigid reaction has no bound. Only a
  contact case approved in a design record closes it, never a load copied from a number.
- reaction_unknown: the interface is no stop, or its reaction is not established (a stop whose force is unknown has
  none). The bundle's reason follows.
- reaction_below_design: the load is smaller than the design reaction. With two stops, the larger one governs, unless
  the load names `limit: min | max`: then only that stop's reaction and status apply. A limit with no stop (the parts
  do not touch there) is invalid_case.

Nothing passes silently: every path that cannot read a design reaction is a refusal. cad-record's layout supplies the
bundle path, the seal check and the manifest name, as evidence.py does for a producer packet. The FEA recipe holds
the bundle's path and manifest sha256, so a new stop-reaction result changes the recipe and a repeat run needs --replace.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INTERFACE_REF = re.compile(r"IF-[0-9]{3}")
SUBJECT = "stop-reaction"  # hw/checks/<V>/stop-reaction/, the subject stop_reaction.py publishes
EVIDENCE = "stop-reaction.json"  # the bundle's evidence/ file: the reaction of each stop of each interface
LIMITS = ("min", "max")  # the joint limits a load may name
BELOW_TOLERANCE = 1e-9  # relative: a load equal to the design reaction passes, whatever the rounding


class ReactionRefused(ValueError):
    """A reaction load the stop-reaction bundle cannot carry. `code` names the refusal."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Basis:
    """The stop-reaction result a load stands on: the bundle read, its manifest sha256 and the design reaction in N."""

    interface: str  # the interface, with ":min" or ":max" when the load names a limit
    bundle: str
    manifest_sha256: str
    design_n: float

    def recipe(self) -> dict[str, Any]:
        return {"bundle": self.bundle, "manifest_sha256": self.manifest_sha256, "design_reaction_n": self.design_n}


def _json(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReactionRefused("reaction_stale", f"the stop-reaction bundle's {what} cannot be read: {error}") from None


def _code_changed(manifest: Mapping[str, Any]) -> None:
    """Refuse a bundle that stop_reaction.py made with other code than the code in this tree: same closure as the producer."""
    import stop_reaction  # deferred: it loads the cad-record and cad-motion modules

    recorded = (manifest.get("recipe") or {}).get("code") if isinstance(manifest.get("recipe"), Mapping) else None
    if not isinstance(recorded, Mapping) or not recorded:
        raise ReactionRefused("reaction_stale", "the stop-reaction bundle records no code fingerprint, so it cannot be tied to "
                                                "the stop-reaction code: run stop_reaction.py again with --replace")
    current = stop_reaction.code_shas.closure(Path(stop_reaction.__file__), stop_reaction.VERDICT_DATA)
    changed = sorted(name for name in {*recorded, *current} if recorded.get(name) != current.get(name))
    if changed:
        raise ReactionRefused("reaction_stale", f"the stop-reaction code changed since the bundle was made ({', '.join(changed)}): "
                                                "run stop_reaction.py again with --replace")


def read(layout: Any, hw: Path, version: str, version_dir: Path, interface: str, limit: str | None = None) -> Basis:
    """The design reaction of `interface` (of its `limit` stop when named) from the sealed stop-reaction bundle of
    `version`, or a refusal."""
    bundle = layout.bundle_dir(hw, "checks", version, SUBJECT)
    if not bundle.is_dir():
        raise ReactionRefused("reaction_missing", f"{version} has no stop-reaction bundle for {interface}: run "
                                                  f"stop_reaction.py --hw HW --version {version}")
    problems = layout.verify(bundle)
    if problems:
        raise ReactionRefused("reaction_stale", f"the stop-reaction bundle of {version} fails its seal ({'; '.join(problems)}): "
                                                "run stop_reaction.py again with --replace")
    manifest = _json(bundle / layout.MANIFEST, layout.MANIFEST)
    current = hashlib.sha256((version_dir / layout.MANIFEST).read_bytes()).hexdigest()
    if manifest.get("version_manifest_sha256") != current:
        raise ReactionRefused("reaction_stale", f"the stop-reaction bundle belongs to another build of {version}: run "
                                                "stop_reaction.py again with --replace")
    _code_changed(manifest)
    detail = _json(bundle / "evidence" / EVIDENCE, EVIDENCE)
    entry = (detail.get("interfaces") or {}).get(interface) if isinstance(detail, Mapping) else None
    if not isinstance(entry, Mapping):
        raise ReactionRefused("reaction_missing", f"the stop-reaction bundle of {version} holds no assessment of {interface}: "
                                                  "the interface owes no stop-reaction check, or the check has not run")
    return _basis(layout, hw, bundle, interface, entry, limit)


def _basis(layout: Any, hw: Path, bundle: Path, interface: str, entry: Mapping[str, Any], limit: str | None) -> Basis:
    if entry.get("outcome") == "not_applicable":
        raise ReactionRefused("reaction_unknown", f"the reaction of {interface} is not established: "
                                                  f"{entry.get('reason') or 'the interface is no stop'}")
    ends = {label: end for label, end in (entry.get("ends") or {}).items() if isinstance(end, Mapping)}
    stops = sorted(label for label, end in ends.items() if end.get("touching") is True)  # a stop exists where the parts touch
    if limit is not None:
        if limit not in stops:
            raise ReactionRefused("invalid_case", f"loads name limit {limit} of {interface}, but the stop-reaction bundle holds "
                                  f"a stop at {' and '.join(stops) or 'no limit'} for it")
        ends = {limit: ends[limit]}
    reactions = {label: end["reaction"] for label, end in ends.items() if isinstance(end.get("reaction"), Mapping)}
    unbounded = sorted(label for label, reaction in reactions.items() if reaction.get("status") == "unbounded")
    if unbounded:
        raise ReactionRefused("reaction_unbounded", f"the stop of {interface} at its {' and '.join(unbounded)} limit has no bounded "
                              "rigid reaction (its nearest edge is on the pivot axis): size it with an FEA contact case approved "
                              "in a design record, not with a load")
    designs = [reaction["design_reaction_n"] for reaction in reactions.values()
               if isinstance(reaction.get("design_reaction_n"), (int, float)) and math.isfinite(reaction["design_reaction_n"])]
    if not designs:
        raise ReactionRefused("reaction_unknown", f"the reaction of {interface} is not established: "
                                                  f"{entry.get('reason') or 'the bundle holds no design reaction for it'}")
    return Basis(name(interface, limit), layout.relative(hw, bundle), hashlib.sha256((bundle / layout.MANIFEST).read_bytes()).hexdigest(),
                 float(max(designs)))


def name(interface: str, limit: str | None) -> str:
    return interface if limit is None else f"{interface}:{limit}"


def resolve(layout: Any, hw: Path, version: str, version_dir: Path, loads: Sequence[Mapping[str, Any]]) -> dict[str, Basis]:
    """The basis of each interface the loads name, after checking each such load against its design reaction.
    Raises ReactionRefused at the first load that cannot stand."""
    bases: dict[str, Basis] = {}
    for index, load in enumerate(loads):
        interface = load.get("reaction")
        if interface is None:
            continue
        limit = load.get("limit")
        key = name(interface, limit)
        if key not in bases:
            bases[key] = read(layout, hw, version, version_dir, interface, limit)
        magnitude = math.sqrt(sum(component * component for component in load["force"]))
        design = bases[key].design_n
        if magnitude < design * (1.0 - BELOW_TOLERANCE):
            raise ReactionRefused("reaction_below_design", f"loads[{index}] carries {magnitude:.4g} N but the modelled stop "
                                  f"reaction of {key} is {design:.4g} N ({'the stop at that limit' if limit else 'the larger stop governs'}): size the load for the "
                                  "design reaction, or record it with a contact case approved in a design record")
    return bases


def refuse_standalone(loads: Sequence[Mapping[str, Any]]) -> None:
    """A standalone case has no version, so no bundle to read: it cannot name a reaction."""
    named = [index for index, load in enumerate(loads) if "reaction" in load]
    if named:
        raise ReactionRefused("reaction_needs_version", f"loads[{named[0]}].reaction needs --hw and --version: the stop-reaction "
                                                        "bundle belongs to a built version")
