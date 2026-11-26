# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The project's scope: its service class and flow profile, read once from the record, and RULES, the one table
that says per service class what each rule owes.

A rule gives each service class one disposition (DISPOSITIONS, strictest first):

- `required`: blocks at its due stage;
- `check_before_use`: a physical obligation the release may close as checked before use;
- `advisory`: runs, never blocks, never passes, and shows a finding;
- `out_of_scope`: not owed by this class.

An unknown class, or one the table does not list, resolves every rule to `required`. Rules are read in order and the
first whose selector matches an obligation decides it, so a narrower prefix (`optical.route`, `motion.play`) comes
before the family it belongs to. Scope never removes an obligation from `manufacturing_intent.required_checks`; it
decides only what a gate makes of it. A release must resolve every obligation whose disposition is not
`out_of_scope` (`in_release_scope`). SERVICE_POLICY holds the other release columns of a class: the evidence levels
it may claim and whether an unvalidated trial (a waived physical obligation) is permitted.

It loads manufacturing_intent.py, flow_profile_gate.py and layout.py (its canonical JSON digest) by path. It reads
no project file; the modules it loads read the shipped product schema. Load it by path and register it in
`sys.modules` as `cad_record_project_scope`.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent


def _load_module(name: str, path: Path) -> Any:
    """A sibling module loaded once by path and registered in sys.modules."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"{path.name} not found at {path}")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


mi = _load_module("cad_record_manufacturing_intent", SCRIPTS / "manufacturing_intent.py")
profile_gate = _load_module("cad_record_flow_profile_gate", SCRIPTS / "flow_profile_gate.py")
layout = _load_module("cad_record_layout", SCRIPTS / "layout.py")

LEVELS: tuple[str, ...] = tuple(mi.EVIDENCE_LEVELS)
PHYSICAL = "physically_validated"
# manufacturing.schema.json $defs/service_class, without `unknown`, in order of consequence.
SERVICE_CLASSES = ("appearance_concept", "fit_assembly", "functional_demo", "working_prototype", "production",
                   "safety_critical")
DISPOSITIONS = ("required", "check_before_use", "advisory", "out_of_scope")
STRICTEST = DISPOSITIONS[0]
OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True)
class Obligations:
    """The obligations a rule covers: families by prefix (None: every family) at the given evidence levels (None:
    every level, one outside the schema too, such as a typo in a requirement's evidence_required)."""

    families: tuple[str, ...] | None
    levels: frozenset[str] | None

    def matches(self, obligation: Mapping[str, Any]) -> bool:
        family, level = obligation["family"], obligation["level"]
        return ((self.families is None or family.startswith(self.families))
                and (self.levels is None or level in self.levels))

    def as_dict(self) -> dict[str, Any]:
        return {"families": None if self.families is None else list(self.families),
                "levels": None if self.levels is None else [level for level in LEVELS if level in self.levels]}


@dataclass(frozen=True)
class Rule:
    """One row of RULES. `purpose` names the record fact that makes the rule not applicable (None for a class rule);
    `summary` is the line the rules table shows."""

    id: str
    applies_to: Obligations
    per_class: Mapping[str, str]
    purpose: str | None
    summary: str

    def __post_init__(self) -> None:
        if set(self.per_class) != set(SERVICE_CLASSES):
            raise ValueError(f"rule {self.id} must give every service class a disposition")
        if not set(self.per_class.values()) <= set(DISPOSITIONS):
            raise ValueError(f"rule {self.id} gives a disposition outside {DISPOSITIONS}")

    def disposition(self, service_class: str | None) -> str:
        """The disposition for `service_class`; the strictest for an unknown or unlisted class."""
        return self.per_class.get(service_class or "", STRICTEST)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "applies_to": self.applies_to.as_dict(),
                "per_class": {cls: self.per_class[cls] for cls in SERVICE_CLASSES}, "purpose": self.purpose,
                "summary": self.summary}


def _owed_from(first: str) -> Mapping[str, str]:
    """`required` from service class `first` on, `out_of_scope` below it."""
    start = SERVICE_CLASSES.index(first)
    return types.MappingProxyType({cls: OUT_OF_SCOPE if index < start else "required"
                                   for index, cls in enumerate(SERVICE_CLASSES)})


_ALL = frozenset(LEVELS)
_LOWER = frozenset({"cad_valid", "geometrically_manufacturable", "process_planned"})
_MAKE = ("intent.", "cad.", "route.", "process.", "purchase.", "optical.route", "export.")
_FIT = ("interface.", "feature.", "assembly.", "motion.play")
_FUNCTION = ("engineering.", "function.", "contact.", "motion.", "optical.")

RULES: tuple[Rule, ...] = (
    Rule("obligation.requirements", Obligations(("requirement.", "policy."), None), _owed_from("appearance_concept"),
         None, "Approved requirements and the evidence policy, at every level: a project may demand more"),
    Rule("obligation.make", Obligations(_MAKE, _LOWER), _owed_from("appearance_concept"), None,
         "Make checks (intent, CAD, route, process, purchase, optical route, export) at the CAD, geometry and "
         "process levels"),
    Rule("obligation.make_evidence", Obligations(_MAKE, _ALL - _LOWER), _owed_from("fit_assembly"), None,
         "Make checks at the simulation and physical levels"),
    Rule("obligation.fit", Obligations(_FIT, _ALL), _owed_from("fit_assembly"), None,
         "Fit checks: interfaces, features, assembly and motion play"),
    Rule("obligation.function", Obligations(_FUNCTION, _ALL), _owed_from("functional_demo"), None,
         "Function checks: engineering, function, contact, motion and optical"),
    Rule("obligation.other", Obligations(None, None), _owed_from("working_prototype"), None,
         "Every other check family, and any check at a level outside the schema"),
)
if RULES[-1].applies_to != Obligations(None, None):
    raise ValueError("the last rule must cover every family at every level, so every obligation has a rule")
_BY_ID: Mapping[str, Rule] = types.MappingProxyType({rule.id: rule for rule in RULES})


@dataclass(frozen=True)
class ServicePolicy:
    """The release columns of a service class besides RULES."""

    claims: frozenset[str]  # the evidence levels a release may claim
    trial: bool  # whether a waived physical obligation (an unvalidated trial) is permitted


SERVICE_POLICY: Mapping[str, ServicePolicy] = types.MappingProxyType({
    "appearance_concept": ServicePolicy(_LOWER, False),
    "fit_assembly": ServicePolicy(_LOWER | {PHYSICAL}, True),
    "functional_demo": ServicePolicy(_ALL, True),
    "working_prototype": ServicePolicy(_ALL, True),
    "production": ServicePolicy(_ALL, False),
    "safety_critical": ServicePolicy(_ALL, False),
})


def obligation_rule(obligation: Mapping[str, Any]) -> Rule:
    """The rule that decides `obligation` (schema form): the first in RULES whose selector matches."""
    return next(rule for rule in RULES if rule.applies_to.matches(obligation))


def obligation_disposition(obligation: Mapping[str, Any], service_class: str | None) -> str:
    """The disposition of `obligation` (schema form) for `service_class`."""
    return obligation_rule(obligation).disposition(service_class)


def in_release_scope(obligation: Mapping[str, Any], service_class: str | None) -> bool:
    """Whether a release of `service_class` must resolve `obligation` (schema form)."""
    return obligation_disposition(obligation, service_class) != OUT_OF_SCOPE


def rules_sha256(service_class: str | None) -> str:
    """sha256 of RULES and `service_class` as the record's canonical JSON (layout.recipe_sha256): a result judged
    under other rules is stale."""
    return str(layout.recipe_sha256({"rules": [rule.as_dict() for rule in RULES], "service_class": service_class}))


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


@dataclass(frozen=True)
class ProjectProfile:
    """product.yaml flow.profile, typed through flow_profile_gate's readers."""

    human_approval: str | None
    fea: tuple[bool | None, str | None]  # (enabled, mode), flow_profile_gate.fea_setting
    stack_up: str | None
    critique: str | None
    fdm_planned: bool
    pip: bool | None  # processes.fdm.print_in_place

    @classmethod
    def of(cls, product: Mapping[str, Any], parts: Mapping[str, Any]) -> ProjectProfile | None:
        """The profile of `product`, or None while none is recorded. `parts` are the part records by PN."""
        profile = profile_gate.profile_of(product)
        if not isinstance(profile, Mapping):
            return None
        pip = profile_gate.value_at(profile, "processes/fdm/print_in_place")
        return cls(_text(profile.get("human_approval")), profile_gate.fea_setting(profile),
                   _text(profile.get("stack_up")), _text(profile.get("critique")),
                   bool(profile_gate.plans_fdm(product, parts)), pip if isinstance(pip, bool) else None)


@dataclass(frozen=True)
class ProjectScope:
    """The service class (None when unknown) and flow profile of one record, and their dispositions."""

    service_class: str | None
    profile: ProjectProfile | None

    @classmethod
    def of(cls, record: Any) -> ProjectScope:
        """The scope of `record` (a manufacturing_intent.Record)."""
        service = record.use_case.service_class
        known = service.value if service.known and service.value in SERVICE_CLASSES else None
        parts = {pn: part.raw for pn, part in record.parts.items()}
        return cls(known, ProjectProfile.of(record.product, parts))

    def disposition(self, rule_id: str) -> str:
        """The disposition of rule `rule_id` for this project."""
        return _BY_ID[rule_id].disposition(self.service_class)

    def obligation_disposition(self, obligation: Mapping[str, Any]) -> str:
        """The disposition of `obligation` (schema form) for this project."""
        return obligation_disposition(obligation, self.service_class)

    @property
    def rules_sha256(self) -> str:
        return rules_sha256(self.service_class)
