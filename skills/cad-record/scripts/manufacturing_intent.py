#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.10"
# dependencies = ["jsonschema>=4.23", "pyyaml>=6.0", "referencing>=0.28.4"]
# ///
"""Record version 2 manufacturing intent: the typed, immutable view of a design record's use case,
classified statements, per-part intent, interface functions and capability, material and selection
profiles, and the policy every other skill reads it through.

The standard library is enough to normalize and check already-parsed record documents, so CAD
environments without jsonschema can load this module by path; register it in `sys.modules` under
`cad_record_manufacturing_intent` before executing it (dataclasses look it up there). `read_documents`
needs PyYAML and `schema_problems` needs jsonschema; both import them only when called.

Invariants this module protects: an unknown is never a default, a zero or a pass; requirements stay
apart from capability; a property resolves only under matching form, orientation, conditioning,
temperature and basis; achievable accuracy comes only from calibrations; every expected check is
enumerated before any producer runs. The field contracts are schemas/manufacturing*.schema.json.
"""

from __future__ import annotations

import hashlib
import math
import re
import types
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeGuard
from urllib.parse import urljoin

if TYPE_CHECKING:
    from jsonschema.exceptions import ValidationError
    from referencing.jsonschema import Schema, SchemaRegistry, SchemaResource
    from typing_extensions import TypeIs


def _sibling(name: str, filename: str) -> Any:
    """A cad-record module beside this one, loaded once by path and registered in sys.modules as `name`."""
    import importlib.util  # noqa: PLC0415 - needed only to read or check files
    import sys  # noqa: PLC0415

    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / filename)
        if spec is None or spec.loader is None:
            raise ImportError(f"{filename} not found beside {Path(__file__).name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[name]
            raise
    return sys.modules[name]


if TYPE_CHECKING:  # mypy reads them from quantities.py beside this file
    from quantities import UNITS, IntentError, Quantity, _is_finite_number, _unknown_unit, convert, quantity, unit
else:
    _quantities = _sibling("cad_record_quantities", "quantities.py")
    UNITS, IntentError, Quantity, convert, quantity, unit = (
        _quantities.UNITS, _quantities.IntentError, _quantities.Quantity, _quantities.convert, _quantities.quantity,
        _quantities.unit)
    _is_finite_number, _unknown_unit = _quantities._is_finite_number, _quantities._unknown_unit

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"

RECORD_VERSION = 2

# --- problem codes this module reports (validate.py maps them to report findings) -------------------------------
VERSION_UNSUPPORTED = "record.version_unsupported"
INCOMPLETE = "record.incomplete"
SCHEMA_INVALID = "intent.schema"
LOAD_DIRECTION_TOLERANCE = 1e-6
COLOR_INVALID = "intent.color_invalid"
CODES: Mapping[str, str] = types.MappingProxyType({
    VERSION_UNSUPPORTED: "a record_version other than 2, or none: this Benchhand cannot read it",
    INCOMPLETE: "a record version 2 record lacks a block or file version 2 requires",
    SCHEMA_INVALID: "a document violates its JSON schema",
    "intent.reference": "a reference names no question, assumption, requirement, interface, part, load, profile, operation, inspection, evidence, case or decision",
    "intent.duplicate": "an id is declared twice",
    COLOR_INVALID: "a part intent.color that is not the sRGB #RRGGBB string part.schema.json defines; it reads as no color",
    "intent.units": "a quantity's unit is unknown or of the wrong dimension",
    "intent.number": "a non-finite number, a reversed interval or an improper rotation",
    "load.direction_not_unit": "a load direction whose norm is not 1 within 1e-6, a zero vector included",
    "intent.route": "a route cycle, a dependency on an unknown operation, or a route that contradicts make_buy and procurement",
    "intent.dual_authority": "installation operations in both assembly.yaml operations and drawing.operations",
    "intent.allowable": "an allowable without justification, or in a typical_not_allowable profile",
    "intent.profile": "a profile that contradicts its level, form or process, a template used as a capability, or an unknown parameter or property name",
    "intent.source_artifact": "a source artifact that is missing or whose bytes do not match its sha256",
    "intent.coverage": "a route operation kind with no check contract; its coverage is unknown",
})

# --- closed vocabularies ---------------------------------------------------------------------------------------
EVIDENCE_LEVELS = ("cad_valid", "geometrically_manufacturable", "process_planned", "simulation_supported", "physically_validated")
OUTCOMES = ("pass", "fail", "unknown", "not_applicable", "not_assessed")
SEVERITIES = ("critical", "major", "minor", "info")
SERVICE_CLASSES = ("appearance_concept", "fit_assembly", "functional_demo", "working_prototype", "production", "safety_critical", "unknown")
FUNCTIONS = ("structural", "cosmetic", "locating", "sliding", "rotating", "sealing", "spring", "thermal", "electrical", "optical", "other")
PRIMARY_PROCESSES = ("fdm", "resin", "cnc", "laser", "waterjet", "plasma", "sheet_forming", "casting", "moulding", "extrusion", "purchased", "other")
ADDITIVE_KINDS = frozenset({"fdm", "resin"})
SHEET_CUTTING_KINDS = frozenset({"laser", "waterjet", "plasma"})
SECONDARY_KINDS = frozenset({"drilling", "reaming", "tapping", "finishing", "coating", "joining", "inspection"})
KNOWN_ROUTE_KINDS = frozenset(set(PRIMARY_PROCESSES) - {"other"}) | SECONDARY_KINDS
STAGES = ("ground", "intent", "specify", "concept", "layout", "parts", "detail", "bom", "analysis", "critique", "reconcile",
          "design_freeze", "release")

# Material property name -> the dimensions its value may have. Extend here, never per project.
MATERIAL_PROPERTIES: Mapping[str, tuple[str, ...]] = types.MappingProxyType({
    "density": ("mass_density",),
    "youngs_modulus": ("stress",), "flexural_modulus": ("stress",), "shear_modulus": ("stress",), "creep_modulus": ("stress",),
    "poisson_ratio": ("ratio",),
    "fu_fy_ratio": ("ratio",),
    "yield_strength": ("stress",), "tensile_strength": ("stress",), "flexural_strength": ("stress",),
    "compressive_strength": ("stress",), "shear_strength": ("stress",), "interlayer_strength": ("stress",),
    "fatigue_strength": ("stress",),
    "elongation_at_yield": ("ratio",), "elongation_at_break": ("ratio",),
    "impact_strength_charpy_notched": ("energy_per_area",), "impact_strength_charpy_unnotched": ("energy_per_area",),
    "impact_strength_izod_notched": ("energy_per_area", "energy_per_length"),
    "hardness_shore_d": ("ratio",), "hardness_rockwell": ("ratio",), "hardness_brinell": ("ratio",),
    "coefficient_of_friction": ("ratio",),
    "thermal_expansion": ("thermal_expansion",), "thermal_conductivity": ("thermal_conductivity",),
    "heat_deflection_temperature": ("temperature",), "vicat_softening_temperature": ("temperature",),
    "glass_transition_temperature": ("temperature",), "melting_temperature": ("temperature",),
    "max_service_temperature": ("temperature",),
    "water_absorption": ("ratio",), "moisture_absorption_equilibrium": ("ratio",), "shrinkage": ("ratio",),
    "light_transmission": ("ratio",), "haze": ("ratio",), "refractive_index": ("ratio",),
})

TEXT = "text"  # a capability parameter whose value is text or a boolean, never a quantity
_ENVELOPE = {"bed_x": ("length",), "bed_y": ("length",), "max_thickness": ("length",)}
_TOOLING = {"tool_set": TEXT, "range": ("length",), "resolution": ("length",)}
# Capability parameter name -> dimensions (or TEXT) per process kind. These describe the machine; achievable
# accuracy is never a parameter, it is a calibration.
CAPABILITY_PARAMETERS: Mapping[str, Mapping[str, Any]] = types.MappingProxyType({
    "fdm": {"nozzle_diameter": ("length",), "nozzle_material": TEXT, "filament_diameter": ("length",), "layer_height": ("length",),
            "line_width": ("length",), "hotend_max_temperature": ("temperature",), "bed_max_temperature": ("temperature",),
            "enclosure": TEXT, "chamber_temperature": ("temperature",), "filament_drying": TEXT, "support_materials": TEXT,
            "max_speed": ("speed",)},
    "resin": {"layer_height": ("length",), "xy_resolution": ("length",), "light_source": TEXT, "wash": TEXT, "post_cure": TEXT,
              "resin_heating": ("temperature",)},
    "cnc": {"axes": ("count",), "travel_x": ("length",), "travel_y": ("length",), "travel_z": ("length",),
            "spindle_max_speed": ("frequency",), "spindle_power": ("power",), "tool_holder": TEXT, "max_tool_length": ("length",),
            "coolant": TEXT, "probing": TEXT},
    "laser": {"laser_type": TEXT, "power": ("power",), "wavelength": ("length",), "assist_gas": TEXT, "extraction": TEXT,
              "operations": TEXT, **_ENVELOPE},
    "waterjet": {"pump_pressure": ("stress",), "abrasive": TEXT, **_ENVELOPE},
    "plasma": {"source": TEXT, "gas": TEXT, **_ENVELOPE},
    "sheet_forming": {"press_brake_force": ("force",), "max_bend_length": ("length",), "tooling": TEXT, "max_thickness": ("length",)},
    "casting": {"method": TEXT, "max_part_size": ("length",), "tooling": TEXT},
    "moulding": {"method": TEXT, "clamp_force": ("force",), "shot_volume": ("volume",), "tooling": TEXT},
    "extrusion": {"die": TEXT, "max_profile": ("length",)},
    "drilling": dict(_TOOLING), "reaming": dict(_TOOLING), "tapping": dict(_TOOLING), "finishing": dict(_TOOLING),
    "coating": {"coating_type": TEXT, "method": TEXT, "max_part_size": ("length",)},
    "joining": {"method": TEXT, "consumables": TEXT},
    "inspection": {"instrument": TEXT, "range": ("length",), "resolution": ("length",)},
})

# Expected dimensions of the use-case facts that hold quantities.
USE_CASE_FACTS = (
    "purpose", "users",
    "environment.temperature", "environment.heat_sources", "environment.moisture", "environment.chemicals", "environment.uv",
    "environment.corrosion",
    "motion.speed", "motion.range", "motion.duty_cycle", "motion.friction", "motion.wear", "motion.service_life",
    "accuracy.tolerance", "accuracy.repeatability", "accuracy.surface_finish", "accuracy.dimensional_stability",
    "consequences.failure", "consequences.severity", "consequences.forbidden_outcomes", "consequences.standards",
    "consequences.supervision", "consequences.approval_authority",
    "logistics.quantity", "logistics.budget", "logistics.lead_time", "logistics.maintenance", "logistics.repair",
    "capability.operator", "capability.outsourcing",
    "release_scope.purpose", "release_scope.operating_limits", "release_scope.exclusions",
)
_USE_CASE_DIMENSIONS = {
    "environment.temperature": ("temperature",), "motion.speed": ("speed", "frequency"), "motion.range": ("length", "angle"),
    "motion.service_life": ("time", "count"), "logistics.quantity": ("count",), "logistics.lead_time": ("time",),
}

# The equipment list is the optional machine offer that GROUND makes once. The template records it as not applicable, so a
# declined or unanswered offer leaves no unknown to ask about again; the capability profiles a route needs are checked apart.
EQUIPMENT_OPTIONAL = ("optional: offered once at GROUND; with no machine recorded the shipped guideline values govern FDM "
                      "and each route's capability is checked on its own")

# The use-case questions the project template asks: (question, use-case paths it answers,
# material_to_choice, safety_relevant). One authority, so the template and its fixtures ask the same.
USE_CASE_QUESTIONS: tuple[tuple[str, tuple[str, ...], bool, bool], ...] = (
    ("Which service class applies: appearance or concept model, fit or assembly prototype, functional demonstration with "
     "limited loads and supervised operation, working prototype, production or end-use product, or safety-critical, "
     "regulated or high-consequence application?", ("service_class",), True, True),
    ("What must the product do, and who uses it?", ("purpose", "users"), True, False),
    ("What operating temperature range and heat sources apply?", ("environment.temperature", "environment.heat_sources"), True, True),
    ("What moisture, chemical, UV and corrosion exposure applies?",
     ("environment.moisture", "environment.chemicals", "environment.uv", "environment.corrosion"), True, False),
    ("Which loads act on it: magnitude, direction, static or sustained, cyclic, impact and vibration?", ("loads",), True, True),
    ("Which motions occur: speed, range, duty cycle, friction, wear and expected service life?",
     ("motion.speed", "motion.range", "motion.duty_cycle", "motion.friction", "motion.wear", "motion.service_life"), True, False),
    ("What accuracy, repeatability, surface finish and dimensional stability are required?",
     ("accuracy.tolerance", "accuracy.repeatability", "accuracy.surface_finish", "accuracy.dimensional_stability"), True, False),
    ("What are the consequences of failure, the forbidden outcomes, the applicable standards or safety requirements, the "
     "supervision and the approving authority?",
     ("consequences.failure", "consequences.severity", "consequences.forbidden_outcomes", "consequences.standards",
      "consequences.supervision", "consequences.approval_authority"), True, True),
    ("What quantity, budget, lead time, maintenance and repair needs apply?",
     ("logistics.quantity", "logistics.budget", "logistics.lead_time", "logistics.maintenance", "logistics.repair"), True, False),
    ("Which operators are available, and is outsourcing acceptable?",
     ("capability.operator", "capability.outsourcing"), True, False),
    ("What may a release claim: its purpose, operating limits, exclusions and the evidence each requirement needs?",
     ("release_scope.purpose", "release_scope.operating_limits", "release_scope.exclusions", "evidence_policy"), False, True),
)

# Documents of the record, by path below hw/, and the schema each follows.
DOCUMENT_SCHEMAS: tuple[tuple[str, str], ...] = (
    ("product.yaml", "product.schema.json"),
    ("requirements.yaml", "requirements.schema.json"),
    ("interfaces.yaml", "interfaces.schema.json"),
    ("assembly.yaml", "assembly.schema.json"),
    ("parts/*/part.yaml", "part.schema.json"),
    ("manufacturing/capabilities.yaml", "manufacturing-capabilities.schema.json"),
    ("manufacturing/materials.yaml", "manufacturing-materials.schema.json"),
    ("manufacturing/selection.yaml", "manufacturing-selection.schema.json"),
    ("manufacturing/findings.yaml", "manufacturing-findings.schema.json"),
    ("manufacturing/evidence.yaml", "manufacturing-evidence.schema.json"),
    ("manufacturing/guidelines/*.yaml", "manufacturing-guideline.schema.json"),
)
REQUIRED_DESIGN_FILES = ("manufacturing/capabilities.yaml", "manufacturing/materials.yaml", "manufacturing/selection.yaml")


class UnsupportedRecordVersion(IntentError):
    """product.yaml names a record_version this Benchhand cannot read."""


@dataclass(frozen=True)
class Problem:
    """One finding about the record. `path` is project-relative (hw/...); `pointer` locates it in the document."""

    code: str
    path: str
    pointer: str
    message: str
    severity: str = "error"  # error or warning
    action: str | None = None

    def finding(self) -> dict[str, Any]:
        """The validate.py report item: path, message, severity, code, canonical_location and action."""
        location = f"{self.path}#/{self.pointer}" if self.pointer else self.path
        return {"path": location, "message": self.message, "severity": self.severity, "code": self.code,
                "canonical_location": None, "action": self.action}


def freeze(value: Any) -> Any:
    """`value` with every mapping read-only and every list a tuple, recursively."""
    if isinstance(value, Mapping):
        return freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


def freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """`value` read-only, its items frozen (see freeze)."""
    return types.MappingProxyType({key: freeze(item) for key, item in value.items()})


def thaw(value: Any) -> Any:
    """The plain dict/list form of a frozen value, such as for canonical JSON."""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    if isinstance(value, Quantity):
        return value.as_dict()
    return value


_EMPTY: Mapping[str, Any] = types.MappingProxyType({})


def _mapping(value: object) -> Mapping[str, Any]:
    """`value` when it is a mapping, else an empty one: an absent or malformed block reads as empty
    (schema_problems reports a malformed one)."""
    return value if isinstance(value, Mapping) else _EMPTY


def _is_quantity(value: object) -> TypeGuard[Mapping[str, Any]]:
    return isinstance(value, Mapping) and "units" in value and any(key in value for key in ("value", "lower", "upper"))


# --- facts -----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Source:
    kind: str | None  # None only in a schema-invalid document; schema_problems reports it
    reference: str | None
    revision: str | None = None
    url: str | None = None
    accessed: str | None = None
    artifact: str | None = None  # relative to hw/
    sha256: str | None = None
    conditions: str | None = None
    origin: str | None = None  # who stated a user value or sets a policy
    reason: str | None = None
    decision: str | None = None
    date: str | None = None
    document: str | None = None  # the document that holds a policy
    locator: str | None = None  # where in the source the value is stated

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> Source:
        return cls(**{name: data.get(name) for name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class Fact:
    """known (value, sources, basis), unknown (reason, question) or not_applicable (reason, decision)."""

    status: str
    value: Any = None  # a Quantity for a quantity fact, else the frozen value
    sources: tuple[Source, ...] = ()
    basis: str | None = None
    reason: str | None = None
    question: str | None = None
    decision: str | None = None
    uncertainty: Mapping[str, Any] | None = None
    conditions: str | None = None
    assumption: str | None = None
    derivation: str | None = None
    note: str | None = None

    @property
    def known(self) -> bool:
        return self.status == "known"

    @property
    def unknown(self) -> bool:
        return self.status == "unknown"

    @property
    def not_applicable(self) -> bool:
        return self.status == "not_applicable"

    @classmethod
    def unknown_because(cls, reason: str, question: str | None = None) -> Fact:
        return cls("unknown", reason=reason, question=question)


def is_fact(value: object) -> TypeIs[Fact]:
    """Whether a field holds a Fact (unknown or not applicable) instead of its known list or id."""
    return isinstance(value, Fact)


def fact(data: Mapping[str, Any]) -> Fact:
    """A Fact from its schema form (structure as the schema checks it; see normalize_record for the rest)."""
    status = data.get("status")
    if status == "known":
        value = data.get("value")
        value = quantity(value) if _is_quantity(value) else freeze(value)
        return Fact("known", value, tuple(Source.from_data(s) for s in data.get("sources") or ()), data.get("basis"),
                    uncertainty=freeze(data.get("uncertainty")), conditions=data.get("conditions"),
                    assumption=data.get("assumption"), derivation=data.get("derivation"), decision=data.get("decision"),
                    note=data.get("note"))
    if status == "unknown":
        return Fact("unknown", reason=data.get("reason"), question=data.get("question"))
    if status == "not_applicable":
        return Fact("not_applicable", reason=data.get("reason"), decision=data.get("decision"))
    raise IntentError(f"a fact's status is known, unknown or not_applicable, got {status!r}")


def _looks_like_fact(value: object) -> TypeGuard[Mapping[str, Any]]:
    return isinstance(value, Mapping) and value.get("status") in ("known", "unknown", "not_applicable")


# --- statements ------------------------------------------------------------------------------------------------
# A field typed `X | None` that the schema requires holds None only in a schema-invalid document, which
# schema_problems reports; the reader keeps it absent rather than raising or inventing a value.

@dataclass(frozen=True)
class Criterion:
    property: str
    operator: str
    value: Any
    source: Source
    units: str | None = None
    id: str | None = None
    state: str | None = None
    conditions: str | None = None

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> Criterion:
        return cls(data["property"], data["operator"], freeze(data["value"]), Source.from_data(data["source"]),
                   data.get("units"), data.get("id"), data.get("state"), data.get("conditions"))


UPPER_BOUND_OPERATORS = ("<=", "<")
LOWER_BOUND_OPERATORS = (">=", ">")
STRICT_OPERATORS = ("<", ">")


def tightens(old: tuple[str, float], new: tuple[str, float]) -> bool:
    """Whether the one-sided bound `new` is at least as tight as `old`; each is an (operator, value) pair.

    Both bounds must be on the same side: `<=` or `<` (upper), or `>=` or `>` (lower). A smaller upper limit, or a
    larger lower limit, is tighter. At equal limits a strict bound (`<`, `>`) is tighter than its non-strict
    one (`<=`, `>=`), and equal bounds tighten each other. Bounds on opposite sides, or an operator that is not
    one-sided (`between`, `equals`, `one_of`, unknown), cannot be compared: ValueError. Pure: no I/O, no state."""
    (old_operator, old_value), (new_operator, new_value) = old, new
    for sides in (UPPER_BOUND_OPERATORS, LOWER_BOUND_OPERATORS):
        if old_operator in sides and new_operator in sides:
            break
    else:
        raise ValueError(f"bounds {old_operator!r} and {new_operator!r} are not two one-sided bounds on the same side")
    if new_value == old_value:
        return new_operator in STRICT_OPERATORS or old_operator not in STRICT_OPERATORS
    return new_value < old_value if old_operator in UPPER_BOUND_OPERATORS else new_value > old_value


def exceeds_upper_bound(worst: float, operator: str, limit: float, tolerance: float = 0.0) -> bool:
    """Whether a worst case of `worst` breaks the upper bound `worst <operator> limit` (`<=` or `<`).

    A worst case on the limit, within `tolerance`, breaks `<` and meets `<=`. ValueError for any other operator,
    so a lower bound is never read as an upper one. Pure."""
    if operator not in UPPER_BOUND_OPERATORS:
        raise ValueError(f"{operator!r} is not an upper bound: use '<=' or '<'")
    return worst >= limit - tolerance if operator == "<" else worst > limit + tolerance


@dataclass(frozen=True)
class Requirement:
    id: str
    text: str | None
    priority: str  # must, should or could
    method: str | None
    criteria_text: str | None
    acceptance: tuple[Criterion, ...]
    evidence_required: tuple[str, ...]
    waiver_policy: str | None
    risk: Mapping[str, Any] | None
    subjects: tuple[Mapping[str, Any], ...]

    @property
    def mandatory(self) -> bool:
        return self.priority.lower() == "must"


@dataclass(frozen=True)
class Preference:
    id: str
    text: str | None
    weight: float | None
    source: Source | None
    criterion: Criterion | None


@dataclass(frozen=True)
class Assumption:
    id: str
    text: str | None
    status: str | None
    owner: str | None
    validation: str | None
    requirements: tuple[str, ...]


@dataclass(frozen=True)
class OpenQuestion:
    id: str
    question: str | None
    status: str | None  # open or resolved
    material_to_choice: bool
    safety_relevant: bool
    subjects: tuple[Mapping[str, Any], ...]

    @property
    def blocks_final_choice(self) -> bool:
        return self.status == "open" and (self.material_to_choice or self.safety_relevant)


@dataclass(frozen=True)
class Load:
    id: str | None
    kind: str | None  # static, sustained, cyclic, impact or vibration
    magnitude: Fact
    direction: tuple[float, float, float] | None  # a unit vector; None only when invalid, which validation reports
    requirements: tuple[str, ...]
    application: Mapping[str, Any] | None = field(default=None, hash=False)  # what `direction` is stated against; None when omitted, an empty mapping when present but not a subject; a mapping is unhashable, equality still compares it
    timing: Mapping[str, Fact] = field(default_factory=lambda: types.MappingProxyType({}), hash=False)  # the duration, frequency and cycles it records

    def facts(self) -> dict[str, Fact]:
        """The load's facts by field name: its magnitude and the duration, frequency and cycles it records."""
        return {"magnitude": self.magnitude, **self.timing}


@dataclass(frozen=True)
class UseCase:
    status: str | None  # unknown, draft or confirmed
    decision: str | None
    service_class: Fact
    facts: Mapping[str, Fact]  # USE_CASE_FACTS path -> Fact
    loads: tuple[Load, ...] | Fact
    equipment: tuple[str, ...] | Fact
    evidence_policy: tuple[Mapping[str, Any], ...] | Fact
    evidence_policy_decision: str | None

    @classmethod
    def unknown(cls, reason: str) -> UseCase:
        blank = Fact.unknown_because(reason)
        return cls("unknown", None, blank, types.MappingProxyType({path: blank for path in USE_CASE_FACTS}), blank, blank, blank, None)


@dataclass(frozen=True)
class FunctionEntry:
    function: str | None
    description: str | None
    requirements: tuple[str, ...]


@dataclass(frozen=True)
class Component:
    id: str | None
    witness: tuple[float, float, float] | None
    locator: Mapping[str, Any] | None
    material_profile: str | Fact | None
    functions: tuple[FunctionEntry, ...]
    multiplicity: int


@dataclass(frozen=True)
class CriticalSurface:
    id: str | None
    feature: str | None
    role: str | None
    dimension_state: str | None
    region: str | None
    component: str | None
    interfaces: tuple[str, ...]
    requirements: tuple[str, ...]
    inspection: str | None


@dataclass(frozen=True)
class Operation:
    """One route operation. `capability` is a profile id or an unknown Fact (None for a purchase)."""

    id: str | None
    kind: str | None
    capability: str | Fact | None
    provider: str | None
    components: tuple[str, ...]  # empty: every component
    depends_on: tuple[str, ...]  # explicit dependencies; empty: the previous operation
    input_state: str | None
    output_state: str | None
    targets: tuple[Mapping[str, Any], ...]
    setup: Mapping[str, Any] | None
    orientation: Mapping[str, Any] | Fact | None
    supports: Mapping[str, Any] | Fact | None
    allowances: tuple[Mapping[str, Any], ...]
    inspections: tuple[Mapping[str, Any], ...]
    requirements: tuple[str, ...]
    purchase: Mapping[str, Any] | None
    raw: Mapping[str, Any]

    @property
    def known_kind(self) -> bool:
        return self.kind in KNOWN_ROUTE_KINDS


@dataclass(frozen=True)
class PartIntent:
    functions: tuple[FunctionEntry, ...] | Fact
    material_profile: str | Fact | None  # None when the intent names none
    selection: str | None
    service: Mapping[str, Any] | Fact
    components: tuple[Component, ...]
    critical_surfaces: tuple[CriticalSurface, ...] | Fact
    route: tuple[Operation, ...] | Fact
    rationale: Mapping[str, Any] | None
    color: str | None = None  # sRGB #RRGGBB for 3MF export, or None

    @classmethod
    def unknown(cls, reason: str) -> PartIntent:
        blank = Fact.unknown_because(reason)
        return cls(blank, blank, None, blank, (), blank, blank, None)


@dataclass(frozen=True)
class Part:
    pn: str
    title: str | None
    material: str | None  # the legacy declaration, never a property source
    process: str | None  # the scalar primary-process label
    sourcing: str  # make, made_to_drawing, catalogue or existing
    transparency: str | None
    intent: PartIntent
    raw: Mapping[str, Any]

    @property
    def made(self) -> bool:
        """Custom-made (make or made_to_drawing); a catalogue buy is not, nor is existing equipment."""
        return self.sourcing in ("make", "made_to_drawing")

    @property
    def existing(self) -> bool:
        """Pre-existing equipment the product mounts onto: outside the product, never bought or made."""
        return self.sourcing == "existing"

    def provider_of(self, stated: str | None) -> str:
        """Who runs a route operation of this part: the operation's `stated` provider, else the recorded sourcing
        (make_buy and procurement.mode): local for a part made in-house, outsourced for one bought to a drawing or from a
        catalogue."""
        return stated or ("local" if self.sourcing == "make" else "outsourced")


@dataclass(frozen=True)
class InterfaceFunctional:
    critical: Fact
    functions: tuple[FunctionEntry, ...] | Fact
    requirements: tuple[str, ...]
    raw: Mapping[str, Any]

    @classmethod
    def unknown(cls, reason: str) -> InterfaceFunctional:
        blank = Fact.unknown_because(reason)
        return cls(blank, blank, (), types.MappingProxyType({}))


@dataclass(frozen=True)
class Interface:
    id: str
    type: str | None
    functional: InterfaceFunctional
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class Calibration:
    id: str | None
    characteristic: str | None
    scope: Mapping[str, Any]
    limits: Quantity
    uncertainty: Quantity | None
    samples: int
    source: Source
    date: str | None
    valid_until: str | None


CALIBRATION_STATES = ("standard", "custom", "unknown")


@dataclass(frozen=True)
class CapabilityProfile:
    id: str
    revision: int
    process: str | None
    provider: str | None
    template: bool
    parameters: Mapping[str, Fact]
    materials: tuple[Mapping[str, Any], ...] | Fact
    calibrations: tuple[Calibration, ...]
    raw: Mapping[str, Any]
    calibration_state: str | None = None  # standard, custom or unknown; None when the profile records none

@dataclass(frozen=True)
class MaterialProperty:
    name: str | None
    value: Fact  # value is a Quantity when known
    test_standard: Fact
    temperature: Fact  # value is a Quantity when known
    conditioning: str
    orientation: Fact | None
    gauge: str | None = None  # elongation gauge (schema enum), None when the property states none


@dataclass(frozen=True)
class MaterialProfile:
    id: str
    revision: int
    level: str | None  # family or grade
    family: str | None
    grade: Fact
    form: str | None
    conditioning: str
    typical_not_allowable: bool
    properties: tuple[MaterialProperty, ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class ProposedPart:
    """A part a selection candidate introduces that the record does not hold yet (`parts[].proposed`).
    Never a Record.parts entry and never a source of check obligations: it becomes a part only when the
    approved change creates its part.yaml."""

    pn: str
    candidate: str
    title: str | None
    sourcing: str | None  # make, made_to_drawing or catalogue
    reason: str | None
    functions: tuple[FunctionEntry, ...]  # empty when the proposal states none
    transparency: str | None


@dataclass(frozen=True)
class Selection:
    status: str  # not_assessed, assessed or confirmed
    candidates: Mapping[str, Mapping[str, Any]]
    recommendation: Mapping[str, Any] | None
    raw: Mapping[str, Any]
    proposed: Mapping[str, Mapping[str, ProposedPart]] = field(default_factory=lambda: _EMPTY)  # candidate id -> PN -> proposal


@dataclass(frozen=True)
class Record:
    """A normalized record. An unsupported-version record carries unknowns and a record.version_unsupported problem."""

    version: int
    product: Mapping[str, Any]
    use_case: UseCase
    requirements: Mapping[str, Requirement]
    preferences: Mapping[str, Preference]
    assumptions: Mapping[str, Assumption]
    questions: Mapping[str, OpenQuestion]
    parts: Mapping[str, Part]
    interfaces: Mapping[str, Interface]
    assembly_operations: tuple[Mapping[str, Any], ...] | Fact
    capabilities: Mapping[str, CapabilityProfile]
    materials: Mapping[str, MaterialProfile]
    selection: Selection
    evidence: tuple[Mapping[str, Any], ...]
    dispositions: tuple[Mapping[str, Any], ...]
    artifacts: Mapping[str, str]  # source artifact path (relative to hw/) -> sha256 the record names
    problems: tuple[Problem, ...]
    assembly_inventory: Mapping[str, Any] = field(default_factory=lambda: _EMPTY)  # assembly.yaml occurrences, subassemblies
    assembly_document: Any = None  # assembly.yaml as written: the GLB-STEP agreement and declared dimensions depend on all of it
    guideline_digests: Mapping[str, Mapping[str, Mapping[str, str]]] = field(default_factory=lambda: _EMPTY)  # profile id -> material profile name ("" without materials) -> {id@revision: sha256}


# --- record version --------------------------------------------------------------------------------------------

def record_version(product: Mapping[str, Any]) -> int:
    """2, the only record version; UnsupportedRecordVersion for any other or absent record_version."""
    version = product.get("record_version")
    if isinstance(version, bool) or version != RECORD_VERSION:
        raise UnsupportedRecordVersion(f"product.yaml record_version {version!r} is not supported; this Benchhand reads "
                                       f"record version {RECORD_VERSION} only")
    return RECORD_VERSION


def version_problem(product: Mapping[str, Any] | None, path: str = "hw/product.yaml") -> Problem | None:
    """The blocking record.version_unsupported problem, or None for a current record. A missing or
    non-mapping product.yaml names no record_version, so it is refused too.
    validate.py, status and gated commands report it beside every other finding."""
    try:
        record_version(product if isinstance(product, Mapping) else {})
    except UnsupportedRecordVersion as error:
        return Problem(VERSION_UNSUPPORTED, path, "record_version", str(error),
                       action="Use the Benchhand release that wrote this record; nothing was changed.")
    return None


# --- reading documents -----------------------------------------------------------------------------------------


def read_documents(hw: Path) -> tuple[dict[str, Any], list[Problem]]:
    """Every record document below `hw` by its path below hw/ (DOCUMENT_SCHEMAS), parsed with PyYAML,
    and a problem for each one that does not parse. Absent files are absent."""
    import yaml  # noqa: PLC0415 - optional dependency, needed only to read files

    loader = _sibling("cad_record_yaml_loader", "yaml_loader.py")
    hw = Path(hw)
    documents: dict[str, Any] = {}
    problems: list[Problem] = []
    for pattern, _schema in DOCUMENT_SCHEMAS:
        for path in sorted(hw.glob(pattern)):
            if not path.is_file():
                continue
            rel = path.relative_to(hw).as_posix()
            try:
                documents[rel] = loader.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
                problems.append(Problem(SCHEMA_INVALID, f"hw/{rel}", "", f"cannot read YAML: {error}"))
    return documents, problems


def schema_for(rel: str) -> str | None:
    """The schema file a document below hw/ follows, or None."""
    for pattern, schema in DOCUMENT_SCHEMAS:
        if re.fullmatch(pattern.replace(".", r"\.").replace("*", "[^/]+"), rel):
            return schema
    return None


def color_is_valid(value: Any) -> bool:
    """Whether `value` satisfies part.schema.json's intent color: its type, maxLength and pattern, read from the schema."""
    rule = _color_rule()
    return isinstance(value, str) and len(value) <= rule["maxLength"] and re.search(rule["pattern"], value) is not None


@lru_cache(maxsize=1)
def _color_rule() -> Mapping[str, Any]:
    import json  # noqa: PLC0415 - needed only to read the schema

    schema = json.loads((SCHEMAS / "part.schema.json").read_text(encoding="utf-8"))
    return types.MappingProxyType(schema["$defs"]["intent"]["properties"]["color"])


def _wrong_branch_type(error: ValidationError) -> bool:
    """A root type mismatch, including alternatives that all reject the same instance type."""
    if error.context and not error.relative_path:
        return all(_wrong_branch_type(branch) for branch in error.context)
    return error.validator == "type" and not error.relative_path


def _schema_bases(resource: SchemaResource, base_uri: str, bases: dict[int, str]) -> None:
    """Index schema object identities with their reference scope, using the library's schema traversal."""
    bases[id(resource.contents)] = base_uri
    base_uri = urljoin(base_uri, resource.id() or "")
    for child in resource.subresources():
        _schema_bases(child, base_uri, bases)


def _combine_discriminators(keyword: str, branches: list[dict[str, Any]]) -> dict[str, Any]:
    """Empty projections impose no constraint: drop them in AND, or leave an OR unrestricted."""
    if keyword == "allOf":
        branches = [branch for branch in branches if branch]
    if not branches or not all(branches):
        return {}
    return branches[0] if len(branches) == 1 else {keyword: branches}


def _discriminator_schema(schema: Schema, registry: SchemaRegistry, bases: Mapping[int, str],
                          seen: frozenset[tuple[str, int, bool]], *, properties: bool = True, enums: bool = True) -> dict[str, Any]:
    """Project direct const/enum constraints through references and root compositions, with a cycle guard.

    Alternatives keep their OR semantics (oneOf becomes anyOf), and allOf retains conjunction.
    Property value schemas contribute literal constraints only, not discriminators of nested objects.
    """
    if not isinstance(schema, Mapping):
        return {}
    base_uri = urljoin(bases[id(schema)], schema.get("$id", ""))
    key = (base_uri, id(schema), properties)
    if key in seen:
        return {}
    seen = seen | {key}
    direct = {keyword: schema[keyword] for keyword in (("const", "enum") if enums else ("const",)) if keyword in schema}
    if properties:
        fields = {name: _discriminator_schema(rule, registry, bases, seen, properties=False, enums=enums)
                  for name, rule in schema.get("properties", {}).items()}
        fields = {name: rule for name, rule in fields.items() if rule}
        if fields:
            direct["properties"] = fields
    clauses = [direct] if direct else []
    if "$ref" in schema:
        resolved = registry.resolver(base_uri).lookup(schema["$ref"])
        clauses.append(_discriminator_schema(resolved.contents, registry, bases, seen, properties=properties, enums=enums))
    for keyword in ("oneOf", "anyOf", "allOf"):
        if keyword in schema:
            clauses.append(_combine_discriminators("allOf" if keyword == "allOf" else "anyOf", [
                _discriminator_schema(branch, registry, bases, seen, properties=properties, enums=enums) for branch in schema[keyword]]))
    return _combine_discriminators("allOf", clauses)


def _discriminator_fields(schema: Mapping[str, Any]) -> set[tuple[str, ...]]:
    fields: set[tuple[str, ...]] = {(name,) for name in schema.get("properties", {})}
    if "const" in schema or "enum" in schema:
        fields.add(())
    for keyword in ("allOf", "anyOf"):
        for branch in schema.get(keyword, []):
            fields.update(_discriminator_fields(branch))
    return fields


def _only_discriminators(schema: Mapping[str, Any], fields: set[tuple[str, ...]]) -> dict[str, Any]:
    """Restrict a projected schema to fields shared by the candidate branches."""
    result = {keyword: schema[keyword] for keyword in ("const", "enum") if () in fields and keyword in schema}
    if "properties" in schema:
        result["properties"] = {name: rule for name, rule in schema["properties"].items() if (name,) in fields}
    for keyword in ("allOf", "anyOf"):
        if keyword in schema:
            result[keyword] = [_only_discriminators(branch, fields) for branch in schema[keyword]]
    return result


def _compatible_errors(error: ValidationError, registry: SchemaRegistry, bases: Mapping[int, str]) -> list[ValidationError]:
    """Errors in a uniquely compatible branch, or none when branch selection is ambiguous.

    Root types and literal discriminators are checked before preferring nested failures. A lone
    type-compatible branch still checks constants, but its value enums do not select a branch.
    Projection preserves nested alternatives, so a matching fact_open status survives a missing reason.
    """
    from jsonschema import Draft202012Validator  # noqa: PLC0415 - optional, needed only to validate

    ruled_out = {branch.schema_path[0] for branch in error.context if _wrong_branch_type(branch)}
    candidates = [branch for branch in error.context if branch.schema_path[0] not in ruled_out]
    if not candidates:
        return candidates
    enums = len({branch.schema_path[0] for branch in candidates}) > 1
    projections = {index: _discriminator_schema(branch, registry, bases, frozenset(), enums=enums)
                   for index, branch in enumerate(error.schema[error.validator]) if index not in ruled_out}
    discriminators = set.intersection(*(_discriminator_fields(schema) for schema in projections.values())) if projections else set()
    compatible = {index for index, schema in projections.items()
                  if Draft202012Validator(_only_discriminators(schema, discriminators)).is_valid(error.instance)}
    candidates = [branch for branch in error.context if branch.schema_path[0] in compatible]
    if len({branch.schema_path[0] for branch in candidates}) > 1:
        nested = {branch.schema_path[0] for branch in candidates}
        nested.difference_update(branch.schema_path[0] for branch in candidates if not branch.relative_path)
        candidates = [branch for branch in candidates if branch.schema_path[0] in nested]
    return candidates if len({branch.schema_path[0] for branch in candidates}) == 1 else []


def _deepest_error(error: ValidationError, registry: SchemaRegistry, bases: Mapping[int, str]) -> ValidationError:
    """The deepest error in the branch selected by root types, literal discriminators and nested failures.

    This selection does not infer compatibility from every domain constraint. Without one selected
    branch, `error` itself stands. An unknown fact's missing reason cannot select a known-status branch.
    `best_match` is not enough: it stops at an anyOf whose branches tie and then quotes the whole value.
    """
    from jsonschema.exceptions import relevance  # noqa: PLC0415 - jsonschema is optional, needed only to validate

    if not error.context:
        return error
    return min((_deepest_error(branch, registry, bases) for branch in _compatible_errors(error, registry, bases)),
               key=lambda leaf: (-len(leaf.absolute_path), leaf.validator in {"oneOf", "anyOf"}, relevance(leaf)), default=error)


def schema_problems(documents: Mapping[str, Any]) -> list[Problem]:
    """JSON-schema problems of every document (needs jsonschema), offline: every bundled schema resolves by $id."""
    from referencing.jsonschema import DRAFT202012  # noqa: PLC0415 - jsonschema's dependency, needed only to validate

    schemas = _sibling("cad_record_schema_registry", "schema_registry.py")
    registry = schemas.registry(SCHEMAS)
    bases: dict[int, str] = {}
    for uri, resource in registry.items():
        _schema_bases(resource, uri, bases)
    problems = []
    for rel, document in sorted(documents.items()):
        name = schema_for(rel)
        if name is None:
            continue
        validator = schemas.document_validator(SCHEMAS, name)
        _schema_bases(DRAFT202012.create_resource(validator.schema), "", bases)
        for top in sorted(validator.iter_errors(document), key=lambda e: [str(p) for p in e.absolute_path]):
            error = _deepest_error(top, registry, bases)
            pointer = "/".join(str(part) for part in error.absolute_path)
            problems.append(Problem("intent.schema", f"hw/{rel}", pointer, f"{pointer + ': ' if pointer else ''}{error.message}"))
    return problems


def _band_text(band: Mapping[str, Any]) -> str:
    low, high, units = band.get("min"), band.get("max"), band.get("units")
    tail = f" {units}" if units else ""
    if low is not None and high is not None:
        return f"{low:g} to {high:g}{tail}"
    return f"at least {low:g}{tail}" if low is not None else f"at most {high:g}{tail}"


def _outside(value: float, band: Mapping[str, Any]) -> bool:
    """Whether `value` lies beyond an edge of `band`; a conversion's rounding noise (10000 um is 10.000000000000002 mm)
    stays inside."""
    low, high = band.get("min"), band.get("max")
    if low is not None and value < low and not math.isclose(value, low, rel_tol=1e-9):
        return True
    return high is not None and value > high and not math.isclose(value, high, rel_tol=1e-9)


def _is_number(value: Any) -> TypeGuard[float]:
    """Whether `value` is a number a band can judge. An integer too large for a float is past every hard bound, which
    schema_problems reports, and cannot be formatted or converted, so it is no number here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        float(value)
    except OverflowError:
        return False
    return True


def _band_values(band: Mapping[str, Any], instance: Any) -> list[tuple[str, float, int | str | None]]:
    """The (label, value in the band's units, member) of every number `instance` holds that the band governs; the member
    is the key of a quantity's end (value, lower, upper) or the index of an array element, None for a bare number. A quantity is read in the band's units. A quantity whose unit is not a
    known unit that converts to them is left to the hard bounds (schema_problems reports it)."""
    if isinstance(instance, Mapping) and "units" in instance:
        written, units = instance["units"], band.get("units")
        found: list[tuple[str, float, int | str | None]] = []
        if not isinstance(written, str) or not isinstance(units, str):
            return found
        for key in ("value", "lower", "upper"):
            number = instance.get(key)
            if _is_number(number):
                try:
                    found.append((f"{key} {number:g} {written}", convert(number, written, units), key))
                except IntentError:
                    continue
        return found
    if isinstance(instance, (list, tuple)):
        return [(f"{item:g}", item, index) for index, item in enumerate(instance) if _is_number(item)]
    return [(f"{instance:g}", instance, None)] if _is_number(instance) else []


def _plausible_keyword(_validator: Any, band: Any, instance: Any, _schema: Any) -> Iterator[Any]:
    """The jsonschema `x-plausible` keyword: one error for each number outside the band, at the member that holds it
    (a quantity's end or an array element), so a sibling's hard-bound error does not hide it. A band relative to a sibling
    (`relative_to`) needs that sibling, which a keyword does not see: it yields every number of that band unjudged, and
    plausibility_problems compares each with the sibling (`_relative_message`)."""
    from jsonschema import ValidationError  # noqa: PLC0415 - needed only to validate

    if not isinstance(band, Mapping):
        return
    relative = "relative_to" in band
    # a relative band has no units of its own: the numbers are read as written
    reading = {**band, "units": instance["units"]} if relative and isinstance(instance, Mapping) and "units" in instance else band
    for label, value, member in _band_values(reading, instance):
        if relative or _outside(value, band):
            yield ValidationError(f"{label} is outside the plausible band {_band_text(band)}",
                                  path=[] if member is None else [member])


def _plausible_errors(errors: Iterable[Any]) -> Iterator[Any]:
    """The x-plausible errors among `errors` and, below a oneOf or anyOf the band made fail, among their contexts."""
    for error in errors:
        if error.validator == "x-plausible":
            yield error
        yield from _plausible_errors(error.context or ())


def _at(document: Any, path: Sequence[Any]) -> Any:
    """The member of `document` at `path`, or None when the path leaves it."""
    for part in path:
        try:
            document = document[part]
        except (KeyError, IndexError, TypeError):
            return None
    return document


def _relative_message(document: Any, error: Any) -> str | None:
    """The warning text of a number outside a band relative to its sibling field (layer_height at most 0.8 of the
    nozzle_diameter beside it), or None when it is inside the band or the sibling is not a known quantity. The sibling
    sits beside the field, at the error's path with the member, the fact's value key and the field name replaced. The upper edge reads the
    sibling's upper end and the lower edge its lower end, so an interval is judged on its widest case; the sibling is
    converted to the number's units."""
    band, path, instance = error.validator_value, list(error.absolute_path), error.instance
    if len(path) < 3 or not isinstance(instance, Mapping):
        return None
    sibling = _at(document, [*path[:-3], band["relative_to"]])
    quantity = sibling.get("value") if isinstance(sibling, Mapping) and sibling.get("status") == "known" else None
    number = instance.get(path[-1])
    if not isinstance(quantity, Mapping) or not isinstance(quantity.get("units"), str) or not _is_number(number):
        return None
    parts = []
    for edge, word, key in (("min", "at least", "lower"), ("max", "at most", "upper")):
        reference = quantity.get(key, quantity.get("value"))
        if band.get(edge) is None or not _is_number(reference):
            continue
        try:
            limit = band[edge] * convert(reference, quantity["units"], instance["units"])
        except IntentError:
            return None
        if _outside(number, {edge: limit}):
            parts.append(f"{word} {band[edge]:g} of {band['relative_to']} {reference:g} {quantity['units']}")
    if not parts:
        return None
    return f"{path[-1]} {number:g} {instance['units']} is outside the plausible band {' to '.join(parts)}"


def _leaves(errors: Iterable[Any]) -> Iterator[Any]:
    """The errors among `errors` with no context and, for a oneOf or anyOf, the errors of its branches: the places a
    value was refused, not the alternatives that held it."""
    for error in errors:
        if error.context:
            yield from _leaves(error.context)
        else:
            yield error


def _field(path: Sequence[Any]) -> str:
    """The field a warning is about: the last name in `path`; a quantity's end (value, lower, upper) and a fact's `value`
    are not names of their own, so the name before them."""
    names = [str(part) for part in path if not isinstance(part, int)]
    while len(names) > 1 and names[-1] in ("value", "lower", "upper"):
        names.pop()
    return names[-1] if names else "document"


def plausibility_problems(documents: Mapping[str, Any]) -> list[Problem]:
    """A warning `plausibility.<field>` for each value inside its schema's hard bound and outside the `x-plausible` band that
    applies to it (needs jsonschema). The band is the one the validator reaches for that value, through `$ref`, `allOf` and
    `if`/`then`, so a calibration's limits take the band of their characteristic, and a quantity is read in the band's
    units (15000 um against a band in mm). A number that fails its schema gets none (schema_problems reports it), while a sibling end of the same interval or array that is inside its hard bound still warns. A band
    relative to a sibling value (layer_height to nozzle_diameter) is not checked."""
    schemas = _sibling("cad_record_schema_registry", "schema_registry.py")
    problems = []
    for rel, document in sorted(documents.items()):
        name = schema_for(rel)
        if name is None:
            continue
        leaves = list(_leaves(schemas.document_validator(SCHEMAS, name).iter_errors(document)))
        refused = [tuple(e.absolute_path) for e in leaves]
        mistyped = [tuple(e.absolute_path) for e in leaves if e.validator == "type"]  # a value of the wrong type has no members to band
        banded = schemas.keyword_validator(SCHEMAS, name, "x-plausible", _plausible_keyword)
        seen = set()
        for error in _plausible_errors(banded.iter_errors(document)):
            path = tuple(error.absolute_path)
            if any(hard[: len(path)] == path for hard in refused):  # the member itself fails its hard bound
                continue
            if any(path[: len(wrong)] == wrong and len(wrong) < len(path) for wrong in mistyped):  # [1] where a number is due
                continue
            pointer = "/".join(str(part) for part in path)
            text = _relative_message(document, error) if "relative_to" in error.validator_value else error.message
            if text is None:
                continue
            message = f"{pointer + ': ' if pointer else ''}{text}"
            if (pointer, message) not in seen:
                seen.add((pointer, message))
                problems.append(Problem(f"plausibility.{_field(path)}", f"hw/{rel}", pointer, message, "warning"))
    return problems


def check_documents(documents: Mapping[str, Any], paths: Iterable[str]) -> list[Problem]:
    """schema_problems, plausibility_problems (warnings) and the normalized record's problems of `documents`, only those whose `Problem.path` is in `paths`.

    `paths` takes the `hw/`-prefixed form Problem.path uses (`hw/requirements.yaml`), not bare rels.
    The record is normalized without an `hw` directory, so file-backed references (decisions, cases,
    source artifacts) are not checked; `check_record` checks them."""
    wanted = frozenset(paths)
    problems = schema_problems(documents) + plausibility_problems(documents) + list(normalize_record(documents).problems)
    return [problem for problem in problems if problem.path in wanted]


def load_record(hw: Path, *, snapshot: bool = False) -> Record:
    """The normalized record at `hw`, with the problems of reading and checking it (schema aside). With `snapshot`, `hw`
    is a version's record/ folder: the guideline sets are read from its own copies only (see `with_guidelines`)."""
    documents, problems = read_documents(hw)
    record = normalize_record(documents, hw=hw)
    return with_guidelines(_with_problems(record, problems + list(record.problems)), hw, snapshot=snapshot)


def check_record(hw: Path, *, snapshot: bool = False) -> tuple[Record, list[Problem]]:
    """load_record plus schema_problems and plausibility_problems: everything this contract reports about the record at `hw`."""
    documents, problems = read_documents(hw)
    record = normalize_record(documents, hw=hw)
    record = with_guidelines(_with_problems(record, list(record.problems)), hw, snapshot=snapshot)
    return record, problems + schema_problems(documents) + plausibility_problems(documents) + list(record.problems)


def with_guidelines(record: Record, hw: Path, *, snapshot: bool = False) -> Record:
    """`record` with the digest of every guideline set its capability profiles can resolve through (impact.py folds
    them into the `source` fingerprint), and a problem for each guideline file that discovery refused. `hw` is the
    project's hw/, or with `snapshot` a version's record/ folder, whose own copies are the only sets read."""
    guidelines = _sibling("cad_record_guidelines", "guidelines.py")
    problems = list(record.problems)
    try:
        catalog = guidelines.discover(hw, snapshot=snapshot)
    except ImportError as error:  # the validator is missing: a check that cannot run is an error, never "no change"
        raise IntentError(f"the guideline sets cannot be checked: {error}. Run with jsonschema installed.") from error
    for refusal in catalog.refused:
        shown = f"hw/{refusal.path}" if refusal.path.startswith(f"{guidelines.PROJECT_RELATIVE.as_posix()}/") else refusal.path
        problems.append(Problem("intent.guideline", shown, "", "; ".join(refusal.problems)))
    values = {name: getattr(record, name) for name in record.__dataclass_fields__}
    values["problems"] = tuple(problems)
    values["guideline_digests"] = freeze(guidelines.record_digests(record, catalog))
    return Record(**values)


def with_guideline_files(record: Record, files: Mapping[str, bytes], *, snapshot: bool = False) -> Record:
    """`with_guidelines` for record files held as bytes (a git index, a version's record snapshot): the project guideline
    sets among `files` are written to a scratch folder and read from there, so discovery sees the same layout as on disk."""
    import tempfile  # noqa: PLC0415 - needed only to read guideline sets

    prefix = "manufacturing/guidelines/"
    with tempfile.TemporaryDirectory(prefix="cad-guidelines-") as scratch:
        for name, data in files.items():
            if name.startswith(prefix) and name.endswith(".yaml") and "/" not in name[len(prefix):]:
                target = Path(scratch) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        return with_guidelines(record, Path(scratch), snapshot=snapshot)


def _with_problems(record: Record, problems: list[Problem]) -> Record:
    values = {name: getattr(record, name) for name in record.__dataclass_fields__}
    values["problems"] = tuple(problems)
    return Record(**values)


# --- normalization ---------------------------------------------------------------------------------------------

def normalize_record(documents: Mapping[str, Any], hw: Path | None = None) -> Record:
    """The Record of parsed `documents` (keyed by path below hw/, as read_documents returns them). With
    `hw`, decision, case and source-artifact references are also checked against the files."""
    return _Reader(documents, Path(hw) if hw is not None else None).record()


class _Reader:
    """One normalization pass: builds the DTOs and collects every problem, never raising for record content."""

    def __init__(self, documents: Mapping[str, Any], hw: Path | None) -> None:
        self.documents = documents
        self.hw = hw
        self.problems: list[Problem] = []
        self.defined: dict[str, set[str]] = {}
        self.refs: list[tuple[str, str, str, str]] = []  # (kind, id, path, pointer)
        self.artifacts: dict[str, str] = {}
        self._inventory: Any = None  # the assembly occurrence inventory, built on first use; False when assembly.yaml has none

    # -- reporting --
    def problem(self, code: str, path: str, pointer: str, message: str, severity: str = "error") -> None:
        self.problems.append(Problem(code, path, pointer, message, severity))

    def define(self, kind: str, ident: Any, path: str, pointer: str) -> None:
        if not isinstance(ident, str):
            return
        names = self.defined.setdefault(kind, set())
        if ident in names:
            self.problem("intent.duplicate", path, pointer, f"{kind.split(':', 1)[0].replace('_', ' ')} {ident} is declared twice")
        names.add(ident)

    def ref(self, kind: str, ident: Any, path: str, pointer: str) -> None:
        if isinstance(ident, str):
            self.refs.append((kind, ident, path, pointer))

    def doc(self, rel: str) -> Mapping[str, Any] | None:
        data = self.documents.get(rel)
        return data if isinstance(data, Mapping) else None

    # -- numbers and facts --
    def numbers(self, value: Any, path: str, pointer: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                self.numbers(item, path, f"{pointer}/{key}" if pointer else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self.numbers(item, path, f"{pointer}/{index}" if pointer else str(index))
        elif isinstance(value, float) and not math.isfinite(value):
            self.problem("intent.number", path, pointer, f"{pointer}: {value} is not a finite number")

    def quantity(self, data: Any, path: str, pointer: str, dimensions: tuple[str, ...] | None = None) -> Quantity | None:
        if not isinstance(data, Mapping):
            return None
        try:
            value = quantity(data)
        except IntentError as error:
            code = "intent.units" if "unit" in str(error) else "intent.number"
            self.problem(code, path, pointer, f"{pointer}: {error}")
            return None
        if dimensions and value.dimension not in dimensions:
            self.problem("intent.units", path, pointer, f"{pointer}: {value.units} is a {value.dimension}; "
                                                      f"this needs a {' or '.join(dimensions)}")
        return value

    def source(self, data: Any, path: str, pointer: str) -> Source | None:
        if not isinstance(data, Mapping):
            return None
        source = Source.from_data(data)
        if source.artifact and source.sha256:
            known = self.artifacts.setdefault(source.artifact, source.sha256)
            if known != source.sha256:
                self.problem("intent.source_artifact", path, pointer,
                             f"{pointer}: {source.artifact} is named with two different sha256 values")
        self.ref("decision", source.decision, path, pointer)
        return source

    def fact(self, data: Any, path: str, pointer: str, dimensions: tuple[str, ...] | None = None) -> Fact:
        if not _looks_like_fact(data):
            return Fact.unknown_because(f"{pointer} is not a recorded fact")
        self.ref("question", data.get("question"), path, pointer)
        self.ref("assumption", data.get("assumption"), path, pointer)
        self.ref("decision", data.get("decision"), path, pointer)
        sources = data.get("sources")
        for index, item in enumerate(sources if isinstance(sources, list) else ()):
            self.source(item, path, f"{pointer}/sources/{index}")
        if data.get("status") == "known" and _is_quantity(data.get("value")):
            if self.quantity(data["value"], path, f"{pointer}/value", dimensions) is None:
                return Fact.unknown_because(f"{pointer} holds an invalid quantity")
            uncertainty = data.get("uncertainty")
            if isinstance(uncertainty, Mapping) and "lower" in uncertainty and "upper" in uncertainty:
                lower, upper = uncertainty["lower"], uncertainty["upper"]
                if all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in (lower, upper)) and lower > upper:
                    self.problem("intent.number", path, f"{pointer}/uncertainty", f"{pointer}/uncertainty: {lower} > {upper}")
        try:
            return fact(data)
        except IntentError as error:
            self.problem("intent.number", path, pointer, f"{pointer}: {error}")
            return Fact.unknown_because(str(error))

    def subject(self, data: Any, path: str, pointer: str) -> Mapping[str, Any]:
        if not isinstance(data, Mapping):
            return types.MappingProxyType({})
        self.ref("part", data.get("PN"), path, pointer)
        self.ref("interface", data.get("interface"), path, pointer)
        self.ref("assembly_operation", data.get("assembly_operation"), path, pointer)
        for requirement in data.get("requirements") or ():
            self.ref("requirement", requirement, path, pointer)
        pn = data.get("PN")
        if isinstance(pn, str):
            if data.get("component"):
                self.ref(f"component:{pn}", data["component"], path, pointer)
            if data.get("operation"):
                self.ref(f"operation:{pn}", data["operation"], path, pointer)
            if isinstance(data.get("occurrence"), str):
                self.subject_occurrence(pn, data["occurrence"], path, pointer)
        return freeze_mapping(data)

    def assembly_inventory(self) -> Any:
        """assembly_data's Inventory of assembly.yaml, or None when it cannot be normalized (validate.py reports why)."""
        if self._inventory is None:
            assembly_data = _sibling("cad_record_assembly_data", "assembly_data.py")
            frames = {pn: part.get("model_frame", assembly_data.DEFAULT_FRAME) for rel, part in self.documents.items()
                      if re.fullmatch(r"parts/([^/]+)/part\.yaml", rel) and isinstance(part, Mapping)
                      and isinstance(pn := part.get("PN"), str)}
            try:
                self._inventory = assembly_data.normalize(self.doc("assembly.yaml") or {}, frames)
            except assembly_data.InventoryError:
                self._inventory = False
        return self._inventory or None

    def subject_occurrence(self, pn: str, occurrence: str, path: str, pointer: str) -> None:
        inventory = self.assembly_inventory()
        if inventory is None:
            return
        try:
            inventory.endpoint(pn, occurrence)
        except _sibling("cad_record_assembly_data", "assembly_data.py").UnresolvedReference as error:
            self.problem("intent.reference", path, pointer, f"{pointer}: {error}")

    def criterion(self, data: Any, path: str, pointer: str) -> Criterion | None:
        if not isinstance(data, Mapping) or not {"property", "operator", "value", "source"} <= set(data):
            return None
        self.source(data["source"], path, f"{pointer}/source")
        units = data.get("units")
        if units is not None and units not in UNITS:
            self.problem("intent.units", path, pointer, f"{pointer}: unknown unit {units!r}")
        value = data["value"]
        if data["operator"] == "between" and isinstance(value, list) and len(value) == 2 and all(
                isinstance(n, (int, float)) and not isinstance(n, bool) for n in value) and value[0] > value[1]:
            self.problem("intent.number", path, f"{pointer}/value", f"{pointer}/value: interval {value[0]}..{value[1]} is reversed")
        return Criterion.from_data(data)

    def functions(self, data: Any, path: str, pointer: str) -> tuple[FunctionEntry, ...] | Fact:
        if _looks_like_fact(data):
            return self.fact(data, path, pointer)
        return self.function_entries(data, path, pointer)

    def function_entries(self, data: Any, path: str, pointer: str) -> tuple[FunctionEntry, ...]:
        """A function list; a component's and a proposal's functions are always one (the schema allows no fact)."""
        entries = []
        for index, item in enumerate(data if isinstance(data, list) else ()):
            if not isinstance(item, Mapping):
                continue
            for requirement in item.get("requirements") or ():
                self.ref("requirement", requirement, path, f"{pointer}/{index}")
            entries.append(FunctionEntry(item.get("function"), item.get("description"), tuple(item.get("requirements") or ())))
        return tuple(entries)

    def id_or_fact(self, data: Any, kind: str, path: str, pointer: str) -> str | Fact | None:
        """A profile id, its recorded fact, or None when absent (or neither, which the schema reports)."""
        if _looks_like_fact(data):
            return self.fact(data, path, pointer)
        if not isinstance(data, str):
            return None
        self.ref(kind, data, path, pointer)
        return data

    # -- documents --
    def record(self) -> Record:
        product_rel = "product.yaml"
        product = self.doc(product_rel) or {}
        for rel, data in self.documents.items():
            self.numbers(data, f"hw/{rel}", "")
        problem = version_problem(product)
        if problem is not None:
            self.problems.append(problem)
            return self.unsupported(product)
        self.completeness()
        use_case = self.use_case(product.get("use_case"), f"hw/{product_rel}")
        requirements, preferences, assumptions, questions = self.statements()
        capabilities = self.capabilities()
        materials = self.materials()
        selection = self.selection()
        parts = self.parts()
        interfaces = self.interfaces()
        operations = self.assembly_operations()
        evidence, dispositions = self.ledgers()
        self.check_profile_use(parts, capabilities, materials)
        self.resolve()
        return Record(RECORD_VERSION, freeze(product), use_case, requirements, preferences, assumptions, questions, parts,
                      interfaces, operations, capabilities, materials, selection, evidence, dispositions,
                      types.MappingProxyType(dict(self.artifacts)), tuple(self.problems),
                      freeze({key: (self.doc("assembly.yaml") or {}).get(key) for key in ("occurrences", "subassemblies")}),
                      freeze(self.doc("assembly.yaml")))

    def unsupported(self, product: Mapping[str, Any]) -> Record:
        """A record of an unsupported version: every manufacturing-intent field unknown, never defaulted."""
        reason = "the record version is not supported"
        parts = {}
        for rel in sorted(self.documents):
            match = re.fullmatch(r"parts/([^/]+)/part\.yaml", rel)
            data = self.doc(rel)
            pn = data.get("PN") if data is not None else None
            if match and data is not None and isinstance(pn, str):
                parts[pn] = self.part_fields(pn, data, PartIntent.unknown(reason))
        interfaces = {}
        for item in (self.doc("interfaces.yaml") or {}).get("interfaces") or ():
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                interfaces[item["id"]] = Interface(item["id"], item.get("type"), InterfaceFunctional.unknown(reason), freeze(item))
        operations: tuple[Mapping[str, Any], ...] | Fact = Fact.unknown_because(reason)
        requirements = self.requirements_only()
        return Record(0, freeze(product), UseCase.unknown(reason), requirements, types.MappingProxyType({}),
                      types.MappingProxyType({}), types.MappingProxyType({}), types.MappingProxyType(parts),
                      types.MappingProxyType(interfaces), operations, types.MappingProxyType({}), types.MappingProxyType({}),
                      Selection("not_assessed", types.MappingProxyType({}), None, types.MappingProxyType({})), (), (),
                      types.MappingProxyType({}), tuple(self.problems))

    def completeness(self) -> None:
        requirements = self.doc("requirements.yaml")
        if requirements is not None:
            missing = [key for key in ("preferences", "assumptions", "open_questions") if key not in requirements]
            if missing:
                self.problem(INCOMPLETE, "hw/requirements.yaml", "", f"record version 2 needs {', '.join(missing)} "
                             "(empty lists when there are none)")
        for rel in sorted(self.documents):
            if (re.fullmatch(r"parts/[^/]+/part\.yaml", rel) and (part := self.doc(rel)) is not None and "intent" not in part
                    and part.get("make_buy") != "existing"):  # existing equipment is outside the product and carries no intent
                self.problem(INCOMPLETE, f"hw/{rel}", "", "record version 2 needs intent; record unknowns explicitly")
        for index, item in enumerate((self.doc("interfaces.yaml") or {}).get("interfaces") or ()):
            if isinstance(item, Mapping) and "functional" not in item:
                self.problem(INCOMPLETE, "hw/interfaces.yaml", f"interfaces/{index}",
                             f"{item.get('id')}: record version 2 needs functional; record unknowns explicitly")
        assembly = self.doc("assembly.yaml")
        if assembly is not None:
            if "operations" not in assembly:
                self.problem(INCOMPLETE, "hw/assembly.yaml", "", "record version 2 needs operations, or an unknown with its reason")
            drawing = assembly.get("drawing")
            if isinstance(drawing, Mapping) and "operations" in drawing:
                self.problem("intent.dual_authority", "hw/assembly.yaml", "drawing/operations",
                             "record version 2 keeps installation operations in top-level operations only")
        for rel in REQUIRED_DESIGN_FILES:
            if rel not in self.documents:
                self.problem(INCOMPLETE, f"hw/{rel}", "", f"record version 2 needs hw/{rel}")

    def check_load_direction(self, load: Mapping[str, Any], path: str, pointer: str) -> tuple[float, float, float] | None:
        """A load's direction as floats, or None when it is not a unit vector. The schema reports a malformed
        array and intent.number a non-finite component; this reports a well-formed one whose norm is not 1."""
        direction = load.get("direction")
        if not (isinstance(direction, list) and len(direction) == 3
                and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in direction)):
            return None
        if any(isinstance(c, float) and not math.isfinite(c) for c in direction):
            return None  # intent.number reports a non-finite component
        try:
            vector = tuple(float(c) for c in direction)
        except (OverflowError, TypeError, ValueError):  # an integer beyond float range
            vector = ()
        norm = math.hypot(*vector) if vector else math.inf
        if abs(norm - 1.0) > LOAD_DIRECTION_TOLERANCE:
            self.problem("load.direction_not_unit", path, f"{pointer}/direction",
                         f"load {load.get('id')}: direction has norm {norm:.9g}, not 1 within {LOAD_DIRECTION_TOLERANCE:g}")
            return None
        return (vector[0], vector[1], vector[2])

    def use_case(self, data: Any, path: str) -> UseCase:
        if not isinstance(data, Mapping):
            return UseCase.unknown("product.yaml has no use_case")
        facts = {}
        for dotted in USE_CASE_FACTS:
            value: Any = data
            for key in dotted.split("."):
                value = value.get(key) if isinstance(value, Mapping) else None
            facts[dotted] = self.fact(value, path, "use_case/" + dotted.replace(".", "/"), _USE_CASE_DIMENSIONS.get(dotted))
        service_class = self.fact(data.get("service_class"), path, "use_case/service_class")
        loads_data = data.get("loads")
        loads: tuple[Load, ...] | Fact
        if isinstance(loads_data, list):
            entries = []
            for index, item in enumerate(loads_data):
                if not isinstance(item, Mapping):
                    continue
                pointer = f"use_case/loads/{index}"
                self.define("load", item.get("id"), path, pointer)
                timing = {key: self.fact(item[key], path, f"{pointer}/{key}", dimensions)
                          for key, dimensions in (("duration", ("time",)), ("frequency", ("frequency",)), ("cycles", ("count",)))
                          if key in item}
                direction = self.check_load_direction(item, path, pointer)
                application = self.subject(item["application"], path, f"{pointer}/application") if "application" in item else None
                for requirement in item.get("requirements") or ():
                    self.ref("requirement", requirement, path, pointer)
                entries.append(Load(item.get("id"), item.get("kind"),
                                    self.fact(item.get("magnitude"), path, f"{pointer}/magnitude", ("force", "torque")),
                                    direction,
                                    tuple(item.get("requirements") or ()), application, types.MappingProxyType(timing)))
            loads = tuple(entries)
        else:
            loads = self.fact(loads_data, path, "use_case/loads")
        capability = _mapping(data.get("capability"))
        equipment_data = capability.get("equipment")
        equipment: tuple[str, ...] | Fact
        if isinstance(equipment_data, list):
            for index, ident in enumerate(equipment_data):
                self.ref("capability", ident, path, f"use_case/capability/equipment/{index}")
            equipment = tuple(equipment_data)
        else:
            equipment = self.fact(equipment_data, path, "use_case/capability/equipment")
        policy_data = data.get("evidence_policy")
        policy: tuple[Mapping[str, Any], ...] | Fact
        policy_decision = None
        if isinstance(policy_data, Mapping) and "obligations" in policy_data:
            for index, item in enumerate(policy_data.get("obligations") or ()):
                if isinstance(item, Mapping):
                    self.ref("requirement", item.get("requirement"), path, f"use_case/evidence_policy/obligations/{index}")
                    if "subject" in item:
                        self.subject(item["subject"], path, f"use_case/evidence_policy/obligations/{index}/subject")
            policy = freeze(policy_data.get("obligations") or [])
            policy_decision = policy_data.get("decision")
            self.ref("decision", policy_decision, path, "use_case/evidence_policy/decision")
        else:
            policy = self.fact(policy_data, path, "use_case/evidence_policy")
        self.ref("decision", data.get("decision"), path, "use_case/decision")
        return UseCase(data.get("status"), data.get("decision"), service_class, types.MappingProxyType(facts), loads,
                       equipment, policy, policy_decision)

    def requirements_only(self) -> Mapping[str, Requirement]:
        found: dict[str, Requirement] = {}
        data = self.doc("requirements.yaml") or {}
        for index, item in enumerate(data.get("requirements") or ()):
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                continue
            pointer = f"requirements/{index}"
            self.define("requirement", item["id"], "hw/requirements.yaml", pointer)
            verification = _mapping(item.get("verification"))
            acceptance = tuple(c for c in (self.criterion(entry, "hw/requirements.yaml", f"{pointer}/acceptance/{i}")
                                           for i, entry in enumerate(item.get("acceptance") or ())) if c is not None)
            subjects = tuple(self.subject(s, "hw/requirements.yaml", f"{pointer}/subjects/{i}")
                             for i, s in enumerate(item.get("subjects") or ()))
            found[item["id"]] = Requirement(item["id"], item.get("text"), str(item.get("priority", "")), verification.get("method"),
                                            verification.get("criteria"), acceptance, tuple(item.get("evidence_required") or ()),
                                            item.get("waiver_policy"), freeze(item.get("risk")), subjects)
        return types.MappingProxyType(found)

    def statements(self) -> tuple[Mapping[str, Requirement], Mapping[str, Preference], Mapping[str, Assumption],
                                  Mapping[str, OpenQuestion]]:
        path = "hw/requirements.yaml"
        requirements = self.requirements_only()
        data = self.doc("requirements.yaml") or {}
        preferences: dict[str, Preference] = {}
        for index, item in enumerate(data.get("preferences") or ()):
            if not isinstance(item, Mapping):
                continue
            pointer = f"preferences/{index}"
            ident = item.get("id")
            self.define("preference", ident, path, pointer)
            for i, subject in enumerate(item.get("subjects") or ()):
                self.subject(subject, path, f"{pointer}/subjects/{i}")
            source = self.source(item.get("source"), path, f"{pointer}/source")
            criterion = self.criterion(item.get("criterion"), path, f"{pointer}/criterion")
            if isinstance(ident, str):  # an entry without an id is unreferenceable; the schema reports it
                preferences[ident] = Preference(ident, item.get("text"), item.get("weight"), source, criterion)
        assumptions: dict[str, Assumption] = {}
        for index, item in enumerate(data.get("assumptions") or ()):
            if not isinstance(item, Mapping):
                continue
            pointer = f"assumptions/{index}"
            ident = item.get("id")
            self.define("assumption", ident, path, pointer)
            for requirement in item.get("requirements") or ():
                self.ref("requirement", requirement, path, pointer)
            self.ref("decision", item.get("decision"), path, pointer)
            if isinstance(item.get("bounds"), Mapping):
                self.quantity(item["bounds"], path, f"{pointer}/bounds")
            if isinstance(ident, str):
                assumptions[ident] = Assumption(ident, item.get("text"), item.get("status"), item.get("owner"),
                                                item.get("validation"), tuple(item.get("requirements") or ()))
        questions: dict[str, OpenQuestion] = {}
        for index, item in enumerate(data.get("open_questions") or ()):
            if not isinstance(item, Mapping):
                continue
            pointer = f"open_questions/{index}"
            ident = item.get("id")
            self.define("question", ident, path, pointer)
            subjects = tuple(self.subject(s, path, f"{pointer}/subjects/{i}") for i, s in enumerate(item.get("subjects") or ()))
            resolution = item.get("resolution")
            if isinstance(resolution, Mapping):
                self.ref("decision", resolution.get("decision"), path, f"{pointer}/resolution")
                if "source" in resolution:
                    self.source(resolution["source"], path, f"{pointer}/resolution/source")
            if isinstance(ident, str):
                questions[ident] = OpenQuestion(ident, item.get("question"), item.get("status"),
                                                bool(item.get("material_to_choice")), bool(item.get("safety_relevant")),
                                                subjects)
        return (requirements, types.MappingProxyType(preferences), types.MappingProxyType(assumptions),
                types.MappingProxyType(questions))

    def part_fields(self, pn: str, data: Mapping[str, Any], intent: PartIntent) -> Part:
        make_buy = data.get("make_buy")
        procurement = _mapping(data.get("procurement"))
        sourcing = ("make" if make_buy == "make" else "existing" if make_buy == "existing" else
                    "made_to_drawing" if procurement.get("mode") == "made_to_drawing" else "catalogue")
        return Part(pn, data.get("title"), data.get("material"), data.get("process"), sourcing,
                    data.get("transparency"), intent, freeze(data))

    def parts(self) -> Mapping[str, Part]:
        found: dict[str, Part] = {}
        for rel in sorted(self.documents):
            if not re.fullmatch(r"parts/[^/]+/part\.yaml", rel):
                continue
            data = self.doc(rel)
            pn = data.get("PN") if data is not None else None
            if data is None or not isinstance(pn, str):
                continue
            path = f"hw/{rel}"
            self.define("part", pn, path, "PN")
            intent_data = data.get("intent")
            intent = self.intent(pn, intent_data, path) if isinstance(intent_data, Mapping) else \
                PartIntent.unknown("part.yaml has no intent")
            part = self.part_fields(pn, data, intent)
            if part.existing:
                self.fact(_mapping(data.get("existing")).get("dimensions"), path, "existing/dimensions")
            self.check_route_sourcing(part, path)
            found[part.pn] = part
        return types.MappingProxyType(found)

    def intent(self, pn: str, data: Mapping[str, Any], path: str) -> PartIntent:
        functions = self.functions(data.get("functions"), path, "intent/functions")
        material = self.id_or_fact(data.get("material_profile"), "material", path, "intent/material_profile")
        self.ref("candidate", data.get("selection"), path, "intent/selection")
        service_data = data.get("service")
        service: Mapping[str, Any] | Fact
        if _looks_like_fact(service_data):
            service = self.fact(service_data, path, "intent/service")
        else:
            service_data = service_data if isinstance(service_data, Mapping) else {}
            for requirement in service_data.get("requirements") or ():
                self.ref("requirement", requirement, path, "intent/service/requirements")
            for load in service_data.get("loads") or ():
                self.ref("load", load, path, "intent/service/loads")
            if "life" in service_data:
                self.fact(service_data["life"], path, "intent/service/life", ("time", "count"))
            service = freeze(service_data)
        components = []
        for index, item in enumerate(data.get("components") or ()):
            if not isinstance(item, Mapping):
                continue
            pointer = f"intent/components/{index}"
            self.define(f"component:{pn}", item.get("id"), path, pointer)
            witness = item.get("witness")
            point = ((float(witness[0]), float(witness[1]), float(witness[2]))
                     if isinstance(witness, list) and len(witness) == 3 else None)
            components.append(Component(
                item.get("id"), point,
                freeze(item.get("locator")), self.id_or_fact(item.get("material_profile"), "material", path, f"{pointer}/material_profile"),
                self.function_entries(item.get("functions"), path, f"{pointer}/functions"), int(item.get("multiplicity", 1))))
        surfaces_data = data.get("critical_surfaces")
        surfaces: tuple[CriticalSurface, ...] | Fact
        if _looks_like_fact(surfaces_data):
            surfaces = self.fact(surfaces_data, path, "intent/critical_surfaces")
        else:
            entries = []
            for index, item in enumerate(surfaces_data if isinstance(surfaces_data, list) else ()):
                if not isinstance(item, Mapping):
                    continue
                pointer = f"intent/critical_surfaces/{index}"
                self.define(f"critical_surface:{pn}", item.get("id"), path, pointer)
                if item.get("component"):
                    self.ref(f"component:{pn}", item["component"], path, pointer)
                for interface in item.get("interfaces") or ():
                    self.ref("interface", interface, path, pointer)
                for requirement in item.get("requirements") or ():
                    self.ref("requirement", requirement, path, pointer)
                if item.get("inspection"):
                    self.ref(f"inspection:{pn}", item["inspection"], path, pointer)
                for i, criterion in enumerate(item.get("criteria") or ()):
                    self.criterion(criterion, path, f"{pointer}/criteria/{i}")
                entries.append(CriticalSurface(item.get("id"), item.get("feature"), item.get("role"), item.get("dimension_state"),
                                               item.get("region"), item.get("component"), tuple(item.get("interfaces") or ()),
                                               tuple(item.get("requirements") or ()), item.get("inspection")))
            surfaces = tuple(entries)
        route = self.route(pn, data.get("route"), path)
        rationale = data.get("rationale")
        if isinstance(rationale, Mapping):
            self.ref("decision", rationale.get("decision"), path, "intent/rationale/decision")
            self.ref("candidate", rationale.get("selection"), path, "intent/rationale/selection")
        color = data.get("color")
        if "color" in data and not color_is_valid(color):
            self.problem(COLOR_INVALID, path, "intent/color",
                         f"{pn}: intent.color {color!r} is not an sRGB #RRGGBB string; quote it, because an unquoted # "
                         "starts a YAML comment and leaves null. The part is exported without a color")
            color = None
        return PartIntent(functions, material, data.get("selection"), service, tuple(components), surfaces, route,
                          freeze(rationale) if isinstance(rationale, Mapping) else None, color)

    def route(self, pn: str, data: Any, path: str) -> tuple[Operation, ...] | Fact:
        if _looks_like_fact(data):
            return self.fact(data, path, "intent/route")
        operations = []
        for index, item in enumerate(data if isinstance(data, list) else ()):
            if not isinstance(item, Mapping):
                continue
            pointer = f"intent/route/{index}"
            ident, kind = item.get("id"), item.get("kind")
            self.define(f"operation:{pn}", ident, path, pointer)
            if isinstance(kind, str) and kind not in KNOWN_ROUTE_KINDS:
                self.problem("intent.coverage", path, f"{pointer}/kind",
                             f"{pn} {ident}: route kind {kind!r} has no check contract; its coverage is unknown, never a pass",
                             severity="warning")
            for component in item.get("components") or ():
                self.ref(f"component:{pn}", component, path, pointer)
            for requirement in item.get("requirements") or ():
                self.ref("requirement", requirement, path, pointer)
            for evidence in item.get("evidence") or ():
                self.ref("evidence", evidence, path, pointer)
            for i, criterion in enumerate(item.get("criteria") or ()):
                self.criterion(criterion, path, f"{pointer}/criteria/{i}")
            capability = self.id_or_fact(item.get("capability"), "capability", path, f"{pointer}/capability")
            setup = item.get("setup") if isinstance(item.get("setup"), Mapping) else None
            if setup is not None:
                self.setup(setup, path, f"{pointer}/setup")
            orientation = self.orientation(item.get("orientation"), path, f"{pointer}/orientation")
            supports = item.get("supports")
            if _looks_like_fact(supports):
                supports = self.fact(supports, path, f"{pointer}/supports")
            elif isinstance(supports, Mapping):
                if "allowance" in supports:
                    self.quantity(supports["allowance"], path, f"{pointer}/supports/allowance", ("length",))
                for inspection in supports.get("inspection") or ():
                    self.ref(f"inspection:{pn}", inspection, path, f"{pointer}/supports/inspection")
                supports = freeze(supports)
            for i, allowance in enumerate(item.get("allowances") or ()):
                if isinstance(allowance, Mapping):
                    self.quantity(allowance.get("amount"), path, f"{pointer}/allowances/{i}/amount", ("length",))
                    self.source(allowance.get("source"), path, f"{pointer}/allowances/{i}/source")
            for i, inspection in enumerate(item.get("inspection") or ()):
                if isinstance(inspection, Mapping):
                    self.define(f"inspection:{pn}", inspection.get("id"), path, f"{pointer}/inspection/{i}")
                    self.ref("requirement", inspection.get("requirement"), path, f"{pointer}/inspection/{i}")
                    self.ref("capability", inspection.get("capability"), path, f"{pointer}/inspection/{i}")
                    for evidence in inspection.get("evidence") or ():
                        self.ref("evidence", evidence, path, f"{pointer}/inspection/{i}")
                    if "criterion" in inspection:
                        self.criterion(inspection["criterion"], path, f"{pointer}/inspection/{i}/criterion")
            purchase = self.purchase(item.get("purchase"), path, f"{pointer}/purchase")
            operations.append(Operation(
                ident, kind, capability, item.get("provider"), tuple(item.get("components") or ()),
                tuple(item.get("depends_on") or ()), item.get("input_state"), item.get("output_state"),
                freeze(item.get("targets") or []), freeze(setup), orientation, supports,
                freeze(item.get("allowances") or []), freeze(item.get("inspection") or []),
                tuple(item.get("requirements") or ()), freeze(purchase), freeze(item)))
        self.check_dependencies(pn, operations, path)
        return tuple(operations)

    def purchase(self, data: Any, path: str, pointer: str) -> Mapping[str, Any] | None:
        """Check the facts and supplier evidence of a purchase block (a part operation's or a candidate step's)."""
        if not isinstance(data, Mapping):
            return None
        for key in ("identity", "specification", "ratings", "optical", "availability"):
            if key in data:
                self.fact(data[key], path, f"{pointer}/{key}")
        if "thickness" in data:
            self.fact(data["thickness"], path, f"{pointer}/thickness", ("length",))
        for i, source in enumerate(data.get("supplier_evidence") or ()):
            self.source(source, path, f"{pointer}/supplier_evidence/{i}")
        return data

    def setup(self, setup: Mapping[str, Any], path: str, pointer: str) -> None:
        stock = _mapping(setup.get("stock"))
        self.id_or_fact(stock.get("material_profile"), "material", path, f"{pointer}/stock/material_profile")
        for key in ("grade", "form", "dimensions"):
            if key in stock:
                self.fact(stock[key], path, f"{pointer}/stock/{key}")
        if "thickness" in stock:
            self.fact(stock["thickness"], path, f"{pointer}/stock/thickness", ("length",))
        workholding = _mapping(setup.get("workholding"))
        if "method" in workholding:
            self.fact(workholding["method"], path, f"{pointer}/workholding/method")
        for key, dimensions in (("clamping_force", ("force",)), ("allowable_deformation", ("length",))):
            if key in workholding:
                self.fact(workholding[key], path, f"{pointer}/workholding/{key}", dimensions)
        access = _mapping(setup.get("access"))
        for index, tool in enumerate(access.get("tools") or ()):
            if isinstance(tool, Mapping):
                self.ref("capability", tool.get("capability"), path, f"{pointer}/access/tools/{index}")
                for key in ("diameter", "reach"):
                    if key in tool:
                        self.fact(tool[key], path, f"{pointer}/access/tools/{index}/{key}", ("length",))
        if "inspection_access" in setup:
            self.fact(setup["inspection_access"], path, f"{pointer}/inspection_access")

    def orientation(self, data: Any, path: str, pointer: str) -> Mapping[str, Any] | Fact | None:
        if data is None:
            return None
        if _looks_like_fact(data):
            return self.fact(data, path, pointer)
        if not isinstance(data, Mapping):
            return None
        rotation = _mapping(data.get("rotation"))
        quaternion = rotation.get("quaternion")
        if isinstance(quaternion, list) and len(quaternion) == 4 and all(isinstance(q, (int, float)) for q in quaternion):
            norm = math.sqrt(sum(float(q) ** 2 for q in quaternion))
            if not math.isfinite(norm) or abs(norm - 1.0) > 1e-6:
                self.problem("intent.number", path, f"{pointer}/rotation/quaternion",
                             f"{pointer}: quaternion norm {norm:.9g} is not 1; a rotation needs a unit quaternion")
        axis = rotation.get("axis")
        if isinstance(axis, list) and len(axis) == 3 and all(isinstance(a, (int, float)) for a in axis):
            if math.sqrt(sum(float(a) ** 2 for a in axis)) == 0.0:
                self.problem("intent.number", path, f"{pointer}/rotation/axis", f"{pointer}: a rotation axis cannot be zero")
        self.ref("decision", data.get("decision"), path, f"{pointer}/decision")
        self.ref("candidate", data.get("selection"), path, f"{pointer}/selection")
        return freeze_mapping(data)

    def check_dependencies(self, pn: str, operations: list[Operation], path: str) -> None:
        ids = [op.id for op in operations]
        graph: dict[str | None, tuple[str | None, ...]] = {}
        for index, op in enumerate(operations):
            for dependency in op.depends_on:
                if dependency not in ids:
                    self.problem("intent.route", path, f"intent/route/{index}/depends_on",
                                 f"{pn} {op.id} depends on {dependency}, which is not an operation of its route")
            graph[op.id] = op.depends_on or ((ids[index - 1],) if index else ())
        state: dict[str | None, int] = {}

        def visit(node: str | None) -> bool:
            if state.get(node) == 1:
                return True
            if state.get(node) == 2:
                return False
            state[node] = 1
            cyclic = any(visit(n) for n in graph.get(node, ()) if n in graph)
            state[node] = 2
            return cyclic

        if any(visit(node) for node in graph):
            self.problem("intent.route", path, "intent/route", f"{pn}: the route's depends_on form a cycle")

    def check_route_sourcing(self, part: Part, path: str) -> None:
        route = part.intent.route
        if is_fact(route) or part.existing:
            return  # existing equipment has no route; the part schema refuses one
        kinds = [op.kind for op in route]
        if part.sourcing == "catalogue":
            made = sorted({str(k) for k in kinds if k not in ("purchased", "inspection")})
            if made:
                self.problem("intent.route", path, "intent/route",
                             f"{part.pn} is a catalogue buy but its route makes it ({', '.join(made)}); a modified purchased "
                             "item is procurement.mode made_to_drawing")
            if "purchased" not in kinds:
                self.problem("intent.route", path, "intent/route", f"{part.pn} is a catalogue buy; its route needs its purchase")
        elif "purchased" in kinds:
            self.problem("intent.route", path, "intent/route",
                         f"{part.pn} is custom-made ({part.sourcing}); record bought stock in setup.stock, not a purchased operation, "
                         "and list the operations the maker performs")

    def interfaces(self) -> Mapping[str, Interface]:
        path = "hw/interfaces.yaml"
        found: dict[str, Interface] = {}
        for index, item in enumerate((self.doc("interfaces.yaml") or {}).get("interfaces") or ()):
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                continue
            pointer = f"interfaces/{index}"
            self.define("interface", item["id"], path, pointer)
            data = item.get("functional")
            if not isinstance(data, Mapping):
                functional = InterfaceFunctional.unknown("interfaces.yaml has no functional block")
            else:
                base = f"{pointer}/functional"
                critical = self.fact(data.get("critical"), path, f"{base}/critical")
                functions = self.functions(data.get("functions"), path, f"{base}/functions")
                for requirement in data.get("requirements") or ():
                    self.ref("requirement", requirement, path, base)
                for load in data.get("loads") or ():
                    self.ref("load", load, path, f"{base}/loads")
                if "failure_consequence" in data:
                    self.fact(data["failure_consequence"], path, f"{base}/failure_consequence")
                for i, surface in enumerate(data.get("surfaces") or ()):
                    if isinstance(surface, Mapping):
                        self.ref("part", surface.get("PN"), path, f"{base}/surfaces/{i}")
                        if surface.get("component") and surface.get("PN"):
                            self.ref(f"component:{surface['PN']}", surface["component"], path, f"{base}/surfaces/{i}")
                        if "source" in surface:
                            self.source(surface["source"], path, f"{base}/surfaces/{i}/source")
                for i, acceptance in enumerate(data.get("acceptance") or ()):
                    if isinstance(acceptance, Mapping):
                        self.criterion(acceptance.get("criterion"), path, f"{base}/acceptance/{i}/criterion")
                variation = _mapping(data.get("variation"))
                self.ref("case", variation.get("stack_case"), path, f"{base}/variation/stack_case")
                self.ref("decision", variation.get("decision"), path, f"{base}/variation/decision")
                contact = _mapping(data.get("contact"))
                for i, material in enumerate(contact.get("materials") or ()):
                    self.id_or_fact(material, "material", path, f"{base}/contact/materials/{i}")
                for key, dimensions in (("load", ("force", "torque")), ("speed", ("speed", "frequency")),
                                        ("life", ("time", "count")), ("finish", None), ("lubrication", None), ("duty", None)):
                    if key in contact:
                        self.fact(contact[key], path, f"{base}/contact/{key}", dimensions)
                for evidence in contact.get("evidence") or ():
                    self.ref("evidence", evidence, path, f"{base}/contact/evidence")
                play = _mapping(data.get("play"))
                compliance = _mapping(play.get("compliance"))
                for block, prefix, keys in ((play, "play", ("axial", "radial", "angular", "backlash")),
                                            (compliance, "play/compliance", ("axial", "radial", "angular"))):
                    for key in keys:
                        if key in block:
                            play_dimensions = {"axial": ("length",), "radial": ("length",), "angular": ("angle",)}.get(key, ("length", "angle"))
                            self.fact(block[key], path, f"{base}/{prefix}/{key}", play_dimensions)
                for case in play.get("scenario_cases") or ():
                    self.ref("motion_case", case, path, f"{base}/play/scenario_cases")
                for operation in data.get("assembly_operations") or ():
                    self.ref("assembly_operation", operation, path, f"{base}/assembly_operations")
                for decision in data.get("decisions") or ():
                    self.ref("decision", decision, path, f"{base}/decisions")
                functional = InterfaceFunctional(critical, functions, tuple(data.get("requirements") or ()), freeze(data))
            found[item["id"]] = Interface(item["id"], item.get("type"), functional, freeze(item))
        return types.MappingProxyType(found)

    def assembly_operations(self) -> tuple[Mapping[str, Any], ...] | Fact:
        path = "hw/assembly.yaml"
        data = (self.doc("assembly.yaml") or {}).get("operations")
        if _looks_like_fact(data):
            return self.fact(data, path, "operations")
        if not isinstance(data, list):
            return Fact.unknown_because("assembly.yaml has no operations")
        for index, item in enumerate(data):
            if not isinstance(item, Mapping):
                continue
            pointer = f"operations/{index}"
            self.define("assembly_operation", item.get("id"), path, pointer)
            self.ref("interface", item.get("interface"), path, pointer)
            for key, dimensions in (("engagement", ("length",)), ("torque", ("torque",)), ("retention", None), ("service_access", None),
                                    ("duration", ("time",))):
                if key in item:
                    self.fact(item[key], path, f"{pointer}/{key}", dimensions)
            cure = item.get("cure")
            if isinstance(cure, Mapping):
                for key, dimensions in (("time", ("time",)), ("temperature", ("temperature",))):
                    if key in cure:
                        self.fact(cure[key], path, f"{pointer}/cure/{key}", dimensions)
            edge = item.get("edge_distance")
            if isinstance(edge, Mapping):
                criterion = edge.get("criterion")
                self.criterion(criterion, path, f"{pointer}/edge_distance/criterion")
                if isinstance(criterion, Mapping) and criterion.get("units") in UNITS and unit(criterion["units"])[0] != "length":
                    self.problem("intent.units", path, f"{pointer}/edge_distance/criterion",
                                 f"{pointer}/edge_distance/criterion: {criterion['units']} is not a length")
                self.fact(edge.get("distance"), path, f"{pointer}/edge_distance/distance", ("length",))
            for i, tool in enumerate(item.get("tool_access") or ()):
                if isinstance(tool, Mapping):
                    self.ref("capability", tool.get("capability"), path, f"{pointer}/tool_access/{i}")
                    if "reach" in tool:
                        self.fact(tool["reach"], path, f"{pointer}/tool_access/{i}/reach", ("length",))
                    if "envelope" in tool:
                        self.fact(tool["envelope"], path, f"{pointer}/tool_access/{i}/envelope")
        for index, item in enumerate(data):
            if isinstance(item, Mapping):
                for predecessor in item.get("predecessors") or ():
                    self.ref("assembly_operation", predecessor, path, f"operations/{index}/predecessors")
        return tuple(freeze(item) for item in data)

    def capabilities(self) -> Mapping[str, CapabilityProfile]:
        path = "hw/manufacturing/capabilities.yaml"
        found: dict[str, CapabilityProfile] = {}
        for index, item in enumerate((self.doc("manufacturing/capabilities.yaml") or {}).get("profiles") or ()):
            if isinstance(item, Mapping):
                profile = self.capability_profile(item, path, f"profiles/{index}")
                if profile is not None:
                    found[profile.id] = profile
        return types.MappingProxyType(found)

    def capability_profile(self, item: Mapping[str, Any], path: str, pointer: str, register: bool = True) -> CapabilityProfile | None:
        ident, process = item.get("id"), item.get("process")
        if register:
            self.define("capability", ident, path, pointer)
        vocabulary = CAPABILITY_PARAMETERS.get(process) if isinstance(process, str) else None

        def parameters(data: Any, where: str) -> Mapping[str, Fact]:
            result = {}
            for name, value in (data.items() if isinstance(data, Mapping) else ()):
                expected = vocabulary.get(name) if vocabulary is not None else None
                if vocabulary is not None and name not in vocabulary:
                    self.problem("intent.profile", path, f"{where}/{name}",
                                 f"{ident}: {name} is not a {process} parameter ({', '.join(sorted(vocabulary))}); achievable "
                                 "accuracy belongs in calibrations")
                dimensions = expected if isinstance(expected, tuple) else None
                parsed = self.fact(value, path, f"{where}/{name}", dimensions)
                if expected == TEXT and parsed.known and isinstance(parsed.value, Quantity):
                    self.problem("intent.units", path, f"{where}/{name}", f"{ident}: {name} is text, not a quantity")
                if isinstance(expected, tuple) and parsed.known and not isinstance(parsed.value, Quantity):
                    self.problem("intent.units", path, f"{where}/{name}", f"{ident}: {name} needs a quantity in "
                                                                          f"{' or '.join(expected)}")
                result[name] = parsed
            return types.MappingProxyType(result)

        params = parameters(item.get("parameters"), f"{pointer}/parameters")
        for index, tool in enumerate(item.get("tools") or ()):
            if isinstance(tool, Mapping):
                parameters(tool.get("parameters"), f"{pointer}/tools/{index}/parameters")
        for key in ("supplier", "operator", "workholding"):
            if key in item:
                self.fact(item[key], path, f"{pointer}/{key}")
        machine = _mapping(item.get("machine"))
        if "identity" in machine:
            self.fact(machine["identity"], path, f"{pointer}/machine/identity")
        envelope = _mapping(item.get("envelope"))
        for axis in ("x", "y", "z"):
            if axis in envelope:
                self.fact(envelope[axis], path, f"{pointer}/envelope/{axis}", ("length",))
        if "source" in item:
            self.source(item["source"], path, f"{pointer}/source")
        materials_data = item.get("materials")
        materials: tuple[Mapping[str, Any], ...] | Fact
        if _looks_like_fact(materials_data):
            materials = self.fact(materials_data, path, f"{pointer}/materials")
        else:
            for i, approval in enumerate(materials_data if isinstance(materials_data, list) else ()):
                if isinstance(approval, Mapping):
                    where = f"{pointer}/materials/{i}"
                    if register:
                        self.ref("material", approval.get("material"), path, where)
                    self.source(approval.get("source"), path, f"{where}/source")
                    if "thickness" in approval:
                        self.fact(approval["thickness"], path, f"{where}/thickness", ("length",))
                    for key in ("coating", "extraction"):
                        if key in approval:
                            self.fact(approval[key], path, f"{where}/{key}")
            materials = freeze(materials_data if isinstance(materials_data, list) else [])
        calibrations = []
        for index, data in enumerate(item.get("calibrations") or ()):
            if not isinstance(data, Mapping):
                continue
            where = f"{pointer}/calibrations/{index}"
            self.define(f"calibration:{ident}", data.get("id"), path, where)
            scope = _mapping(data.get("scope"))
            if register:
                self.ref("material", scope.get("material"), path, f"{where}/scope/material")
            for key in ("size", "thickness"):
                if key in scope:
                    self.quantity(scope[key], path, f"{where}/scope/{key}", ("length",))
            result = _mapping(data.get("result"))
            limits = self.quantity(result.get("limits"), path, f"{where}/result/limits")
            uncertainty = self.quantity(result.get("uncertainty"), path, f"{where}/result/uncertainty") if "uncertainty" in result else None
            source = self.source(data.get("source"), path, f"{where}/source")
            if source is not None and source.kind not in ("calibration", "test", "supplier"):
                self.problem("intent.profile", path, f"{where}/source",
                             f"{ident} {data.get('id')}: a calibration rests on calibration, test or supplier evidence, not {source.kind}")
            self.ref("decision", data.get("decision"), path, where)
            if limits is not None and source is not None:
                calibrations.append(Calibration(data.get("id"), data.get("characteristic"), freeze(scope), limits, uncertainty,
                                                int(result.get("samples", 0)), source, data.get("date"), data.get("valid_until")))
        status = self.calibration_state(item, bool(calibrations), ident, path, pointer)
        if not isinstance(ident, str):
            return None
        return CapabilityProfile(ident, int(item.get("revision", 0)), process, item.get("provider"), bool(item.get("template")),
                                 params, materials, tuple(calibrations), freeze(item), status)

    def calibration_state(self, item: Mapping[str, Any], measured: bool, ident: Any, path: str, pointer: str) -> str | None:
        """The typed status of a profile's calibration_state: None when none is recorded, `unknown` (with a warning) when
        it is not a mapping with a known status, since a snapshot is normalised without schema validation. A source is
        read like any other. `custom` with no measured calibration warns. It never blocks anything."""
        raw = item.get("calibration_state")
        if raw is None:
            return None
        where = f"{pointer}/calibration_state"
        state = _mapping(raw)
        status = state.get("status")
        if not isinstance(status, str) or status not in CALIBRATION_STATES:
            self.problem("intent.calibration_state", path, where,
                         f"{ident}: calibration_state is not a status of {', '.join(CALIBRATION_STATES)}; read as unknown", "warning")
            status = "unknown"
        self.source(state.get("source"), path, f"{where}/source")
        if status == "custom" and not measured:
            self.problem("intent.calibration_state", path, where,
                         f"{ident}: calibration_state custom has no measured calibration; record one in calibrations, "
                         "or state standard or unknown", "warning")
        return status

    def materials(self) -> Mapping[str, MaterialProfile]:
        path = "hw/manufacturing/materials.yaml"
        found: dict[str, MaterialProfile] = {}
        for index, item in enumerate((self.doc("manufacturing/materials.yaml") or {}).get("profiles") or ()):
            if isinstance(item, Mapping):
                profile = self.material_profile(item, path, f"profiles/{index}")
                if profile is not None:
                    found[profile.id] = profile
        return types.MappingProxyType(found)

    def material_profile(self, item: Mapping[str, Any], path: str, pointer: str, register: bool = True) -> MaterialProfile | None:
        ident = item.get("id")
        if register:
            self.define("material", ident, path, pointer)
        level, form = item.get("level"), item.get("form")
        grade = self.fact(item.get("grade"), path, f"{pointer}/grade")
        if level == "grade" and not grade.known:
            self.problem("intent.profile", path, f"{pointer}/grade", f"{ident}: a grade profile names its grade")
        if level == "family" and grade.known:
            self.problem("intent.profile", path, f"{pointer}/grade", f"{ident}: a family profile has no grade; family guidance "
                                                                       "is never a grade specification")
        for key in ("manufacturer", "specification"):
            if key in item:
                self.fact(item[key], path, f"{pointer}/{key}")
        state = _mapping(item.get("process_state"))
        if _looks_like_fact(state.get("process")):
            self.fact(state["process"], path, f"{pointer}/process_state/process")
        typical_only = bool(item.get("typical_not_allowable"))
        properties = []
        for index, data in enumerate(item.get("properties") or ()):
            if not isinstance(data, Mapping):
                continue
            where = f"{pointer}/properties/{index}"
            name = data.get("name")
            dimensions = MATERIAL_PROPERTIES.get(name) if isinstance(name, str) else None
            if dimensions is None:
                self.problem("intent.profile", path, f"{where}/name",
                             f"{ident}: {name!r} is not a material property name ({', '.join(MATERIAL_PROPERTIES)})")
            value = self.fact(data.get("value"), path, f"{where}/value", dimensions)
            if value.known and value.basis == "allowable" and typical_only:
                self.problem("intent.allowable", path, f"{where}/value",
                             f"{ident} {name}: a typical_not_allowable profile holds no allowables")
            if value.known and value.basis == "allowable" and level == "family":
                self.problem("intent.allowable", path, f"{where}/value", f"{ident} {name}: family guidance is never an allowable")
            conditions = _mapping(data.get("conditions"))
            orientation = (self.fact(conditions["orientation"], path, f"{where}/conditions/orientation")
                           if "orientation" in conditions else None)
            if form == "printed" and orientation is None:
                self.problem("intent.profile", path, f"{where}/conditions",
                             f"{ident} {name}: a printed property states its orientation, or why it is unknown")
            properties.append(MaterialProperty(
                name, value, self.fact(conditions.get("test_standard"), path, f"{where}/conditions/test_standard"),
                self.fact(conditions.get("temperature"), path, f"{where}/conditions/temperature", ("temperature",)),
                conditions.get("conditioning", "unknown"), orientation, conditions.get("gauge")))
        if not isinstance(ident, str):
            return None
        return MaterialProfile(ident, int(item.get("revision", 0)), level, item.get("family"), grade, form,
                               state.get("conditioning", "unknown"), typical_only, tuple(properties), freeze(item))

    def existing_pns(self) -> frozenset[str]:
        """The part numbers whose part.yaml records make_buy existing."""
        found = set()
        for rel in self.documents:
            if re.fullmatch(r"parts/[^/]+/part\.yaml", rel) and isinstance(data := self.doc(rel), Mapping):
                if data.get("make_buy") == "existing" and isinstance(data.get("PN"), str):
                    found.add(data["PN"])
        return frozenset(found)

    def candidate_part_profiles(self, part: Mapping[str, Any], path: str, where: str) -> None:
        """The material, capability and purchase references of one selection candidate part entry."""
        self.id_or_fact(part.get("material_profile"), "material", path, f"{where}/material_profile")
        for j, step in enumerate(part.get("route") or ()):
            if isinstance(step, Mapping):
                self.id_or_fact(step.get("capability"), "capability", path, f"{where}/route/{j}/capability")
                self.purchase(step.get("purchase"), path, f"{where}/route/{j}/purchase")

    def selection(self) -> Selection:
        path = "hw/manufacturing/selection.yaml"
        data = self.doc("manufacturing/selection.yaml") or {}
        candidates: dict[str, Mapping[str, Any]] = {}
        proposed: dict[str, Mapping[str, ProposedPart]] = {}
        existing = self.existing_pns()
        for index, item in enumerate(data.get("candidates") or ()):
            if not isinstance(item, Mapping):
                continue
            pointer = f"candidates/{index}"
            ident = item.get("id")
            self.define("candidate", ident, path, pointer)
            introduced: dict[str, ProposedPart] = {}
            for i, part in enumerate(item.get("parts") or ()):
                if not isinstance(part, Mapping):
                    continue
                where = f"{pointer}/parts/{i}"
                self.define(f"candidate_part:{index}", part.get("PN"), path, where)
                proposal = part.get("proposed")
                if isinstance(proposal, Mapping):
                    self.ref("proposed_part", part.get("PN"), path, where)
                    entries = self.function_entries(proposal.get("functions"), path, f"{where}/proposed/functions")
                    pn = part.get("PN")
                    if isinstance(pn, str) and isinstance(ident, str):
                        introduced[pn] = ProposedPart(pn, ident, proposal.get("title"), proposal.get("sourcing"),
                                                      proposal.get("reason"), entries, proposal.get("transparency"))
                else:
                    self.ref("part", part.get("PN"), path, where)
                pn = part.get("PN")
                if not (isinstance(pn, str) and pn in existing):  # selection drops an existing-equipment entry unread
                    self.candidate_part_profiles(part, path, where)
            if introduced and isinstance(ident, str):
                proposed[ident] = types.MappingProxyType(introduced)
            for interface in item.get("interfaces") or ():
                self.ref("interface", interface, path, pointer)
            for i, evaluation in enumerate(item.get("evaluations") or ()):
                if isinstance(evaluation, Mapping):
                    self.ref("requirement", evaluation.get("requirement"), path, f"{pointer}/evaluations/{i}")
                    if "criterion" in evaluation:
                        self.criterion(evaluation["criterion"], path, f"{pointer}/evaluations/{i}/criterion")
                    if "source" in evaluation:
                        self.source(evaluation["source"], path, f"{pointer}/evaluations/{i}/source")
                    if isinstance(evaluation.get("value"), Mapping):
                        self.quantity(evaluation["value"], path, f"{pointer}/evaluations/{i}/value")
            for key, dimensions in (("quantity", ("count",)), ("cost", None), ("lead_time", ("time",)), ("maintenance", None)):
                if key in item:
                    self.fact(item[key], path, f"{pointer}/{key}", dimensions)
            for i, score in enumerate(item.get("preference_scores") or ()):
                if isinstance(score, Mapping):
                    self.ref("preference", score.get("preference"), path, f"{pointer}/preference_scores/{i}")
            if isinstance(ident, str):  # an id-less candidate is unreferenceable; the schema reports it
                candidates[ident] = freeze_mapping(item)
        recommendation = data.get("recommendation")
        if isinstance(recommendation, Mapping):
            for key in ("primary", "prototype", "end_use", "fallback"):
                self.ref("candidate", recommendation.get(key), path, f"recommendation/{key}")
            for requirement in recommendation.get("governing_requirements") or ():
                self.ref("requirement", requirement, path, "recommendation/governing_requirements")
        assessment = _mapping(data.get("assessment"))
        self.ref("decision", assessment.get("decision"), path, "assessment/decision")
        return Selection(data.get("status", "not_assessed"), types.MappingProxyType(candidates),
                         freeze(recommendation) if isinstance(recommendation, Mapping) else None, freeze(data),
                         types.MappingProxyType(proposed))

    def ledgers(self) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]]:
        evidence_path, findings_path = "hw/manufacturing/evidence.yaml", "hw/manufacturing/findings.yaml"
        entries = [e for e in (self.doc("manufacturing/evidence.yaml") or {}).get("entries") or () if isinstance(e, Mapping)]
        for index, entry in enumerate(entries):
            pointer = f"entries/{index}"
            self.define("evidence", entry.get("id"), evidence_path, pointer)
            self.ref("requirement", entry.get("requirement"), evidence_path, pointer)
            self.subject(entry.get("subject"), evidence_path, f"{pointer}/subject")
            if "criterion" in entry:
                self.criterion(entry["criterion"], evidence_path, f"{pointer}/criterion")
            if "source" in entry:
                self.source(entry["source"], evidence_path, f"{pointer}/source")
            for decision in entry.get("decisions") or ():
                self.ref("decision", decision, evidence_path, pointer)
            waiver = _mapping(entry.get("waiver"))
            self.ref("decision", waiver.get("decision"), evidence_path, f"{pointer}/waiver")
        dispositions = [d for d in (self.doc("manufacturing/findings.yaml") or {}).get("dispositions") or () if isinstance(d, Mapping)]
        for index, disposition in enumerate(dispositions):
            pointer = f"dispositions/{index}"
            self.ref("decision", disposition.get("decision"), findings_path, pointer)
            for evidence in disposition.get("replacement_evidence") or ():
                self.ref("evidence", evidence, findings_path, pointer)
            waiver = _mapping(disposition.get("waiver"))
            self.ref("decision", waiver.get("decision"), findings_path, f"{pointer}/waiver")
            if waiver.get("subject"):
                self.subject(waiver["subject"], findings_path, f"{pointer}/waiver/subject")
        return freeze(entries), freeze(dispositions)

    def check_profile_use(self, parts: Mapping[str, Part], capabilities: Mapping[str, CapabilityProfile],
                          materials: Mapping[str, MaterialProfile]) -> None:
        for pn, part in parts.items():
            path = f"hw/parts/{pn}/part.yaml"
            route = part.intent.route
            if is_fact(route):
                continue
            for index, op in enumerate(route):
                profile = capabilities.get(op.capability) if isinstance(op.capability, str) else None
                if profile is None:
                    continue
                if profile.template:
                    self.problem("intent.profile", path, f"intent/route/{index}/capability",
                                 f"{pn} {op.id}: {profile.id} is a template, not a capability; record the actual machine or supplier")
                if profile.process != op.kind:
                    self.problem("intent.profile", path, f"intent/route/{index}/capability",
                                 f"{pn} {op.id}: {profile.id} is a {profile.process} capability, the operation is {op.kind}")

    def _decision_problem(self, ident: str) -> str | None:
        """Why decision `ident` names no record under hw/decisions/, or None when it names one."""
        layout = _sibling("cad_record_layout", "layout.py")
        try:
            found = layout.decision_file(self.hw, ident)
        except layout.LayoutError as error:
            return f"cannot be resolved: {error}"
        return None if found is not None else f"has no hw/decisions/{ident}-<slug>.md"

    def resolve(self) -> None:
        for kind, ident, path, pointer in self.refs:
            if kind == "decision":
                if self.hw is not None and (why := self._decision_problem(ident)) is not None:
                    self.problem("intent.reference", path, pointer, f"{pointer}: decision {ident} {why}")
                continue
            if kind in ("case", "motion_case"):
                folder = "analysis" if kind == "case" else "motion"
                name = "case.yaml" if kind == "case" else "plan.yaml"
                if self.hw is not None and not (self.hw / folder / "cases" / ident / name).is_file():
                    self.problem("intent.reference", path, pointer, f"{pointer}: {kind} {ident} has no hw/{folder}/cases/{ident}/{name}")
                continue
            if kind == "proposed_part":
                if ident in self.defined.get("part", set()):
                    self.problem("intent.duplicate", path, pointer, f"{pointer}: proposed part {ident} is already declared under "
                                 f"hw/parts/{ident}; reference the declared part, or propose an unused PN")
                continue
            if ident not in self.defined.get(kind, set()):
                label = kind.split(":", 1)[0].replace("_", " ")
                self.problem("intent.reference", path, pointer, f"{pointer}: {label} {ident} is not declared")
        if self.hw is not None:
            for artifact, digest in sorted(self.artifacts.items()):
                target = self.hw / artifact
                if not target.is_file() or target.is_symlink():
                    self.problem("intent.source_artifact", f"hw/{artifact}", "", f"hw/{artifact} is named as a source but is missing")
                elif hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    self.problem("intent.source_artifact", f"hw/{artifact}", "",
                                 f"hw/{artifact} does not match the sha256 its source names")


# --- policy ----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class UseCaseAssessment:
    status: str | None
    service_class: str | None  # the known service class, or None
    final_ready: bool  # a final material/process recommendation may be made
    blocking: tuple[str, ...]  # why it may not
    unknown: tuple[str, ...]  # use-case fields that are unknown
    open_questions: tuple[str, ...]  # open questions that are material to the choice or safety-relevant


def use_case_fields(use_case: UseCase) -> dict[str, Fact]:
    """Every use-case fact by its field name below use_case: the USE_CASE_FACTS, the service class, and loads,
    capability.equipment and evidence_policy while each is a fact. A recorded loads list adds each load's facts as
    loads.<id>.<field> (magnitude, duration, frequency, cycles), so a listed load never hides an unknown one."""
    fields = dict(use_case.facts)
    fields["service_class"] = use_case.service_class
    for name, value in (("loads", use_case.loads), ("capability.equipment", use_case.equipment),
                        ("evidence_policy", use_case.evidence_policy)):
        if is_fact(value):
            fields[name] = value
    if not is_fact(use_case.loads):
        for index, load in enumerate(use_case.loads):
            for name, value in load.facts().items():
                fields[f"loads.{load.id or index}.{name}"] = value
    return fields


def assess_use_case(record: Record) -> UseCaseAssessment:
    """Whether the use case supports a final material/process recommendation. Conditional exploration
    is always allowed; a final choice needs a confirmed use case, a known service class, no open
    material-to-choice or safety question, and no unknown that no open question tracks. An unknown load fact (the load
    case, or a listed load's magnitude, duration, frequency or cycles) blocks whatever its question is classed as. A
    resolved question does not track an unknown: its answer belongs in the fact, so the pair is a contradiction."""
    use_case = record.use_case
    blocking: list[str] = []
    if record.version != RECORD_VERSION:
        blocking.append("the record is not record version 2")
    if use_case.status != "confirmed" or not use_case.decision:
        blocking.append("the use case is not confirmed by a decision")
    service = use_case.service_class.value if use_case.service_class.known else None
    if service in (None, "unknown"):
        blocking.append("the service class is unknown")
    fields = use_case_fields(use_case)
    unknown = tuple(sorted(name for name, value in fields.items() if value.unknown))
    for name in unknown:
        tracking = fields[name].question
        question = record.questions.get(tracking) if tracking else None
        if question is None:
            blocking.append(f"use_case.{name} is unknown and no open question tracks it")
        elif question.status != "open":
            blocking.append(f"use_case.{name} is unknown but its question {question.id} is {question.status}; "
                            "record the answer in the fact or reopen the question")
        elif name == "loads" or name.startswith("loads."):
            blocking.append(f"use_case.{name} is unknown: a final choice needs every load fact known, whatever its "
                            f"question ({question.id}) is classed as")
    open_questions = tuple(sorted(q.id for q in record.questions.values() if q.blocks_final_choice))
    if open_questions:
        blocking.append(f"open questions material to the choice or safety: {', '.join(open_questions)}")
    if service == "safety_critical":
        for name in ("consequences.standards", "consequences.approval_authority"):
            if not fields[name].known:
                blocking.append(f"a safety-critical use case needs use_case.{name}")
    return UseCaseAssessment(use_case.status, service, not blocking, tuple(blocking), unknown, open_questions)


@dataclass(frozen=True)
class PartRoute:
    pn: str
    sourcing: str  # make, made_to_drawing, catalogue or existing
    operations: tuple[Operation, ...]  # route order; empty while the route is unknown
    # An id is None only for an operation or component a schema-invalid record leaves unnamed.
    predecessors: Mapping[str | None, tuple[str | None, ...]]  # operation id -> the operations it depends on
    unknown: Fact | None  # the route fact while it is not recorded
    unsupported: tuple[str | None, ...]  # operation ids whose kind has no check contract
    components: tuple[str | None, ...]  # component ids; ("main",) for a sole solid


def part_route(record: Record, pn: str) -> PartRoute:
    """The ordered route of part `pn` with each operation's predecessors. IntentError for an unknown PN
    or a route whose dependencies are unknown or cyclic."""
    part = record.parts.get(pn)
    if part is None:
        raise IntentError(f"no part {pn}")
    components: tuple[str | None, ...] = tuple(c.id for c in part.intent.components) or ("main",)
    route = part.intent.route
    if is_fact(route):
        return PartRoute(pn, part.sourcing, (), types.MappingProxyType({}), route, (), components)
    ids = [op.id for op in route]
    predecessors: dict[str | None, tuple[str | None, ...]] = {}
    for index, op in enumerate(route):
        missing = [d for d in op.depends_on if d not in ids]
        if missing:
            raise IntentError(f"{pn} {op.id} depends on {', '.join(missing)}, not operations of its route")
        predecessors[op.id] = op.depends_on or ((ids[index - 1],) if index else ())
    _topological(pn, predecessors)
    return PartRoute(pn, part.sourcing, route, types.MappingProxyType(predecessors), None,
                     tuple(op.id for op in route if not op.known_kind), components)


def _topological(pn: str, predecessors: Mapping[str | None, tuple[str | None, ...]]) -> list[str | None]:
    order: list[str | None] = []
    state: dict[str | None, int] = {}

    def visit(node: str | None) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            raise IntentError(f"{pn}: the route's depends_on form a cycle through {node}")
        state[node] = 1
        for previous in predecessors.get(node, ()):
            visit(previous)
        state[node] = 2
        order.append(node)

    for node in predecessors:
        visit(node)
    return order


@dataclass(frozen=True)
class PropertyResolution:
    status: str  # resolved, unknown or refused
    profile: str
    name: str
    value: Quantity | None
    basis: str | None
    sources: tuple[Source, ...]
    conditions: Mapping[str, Any] | None
    reasons: tuple[str, ...]

    @property
    def resolved(self) -> bool:
        return self.status == "resolved"


def resolve_property(profile: MaterialProfile, name: str, *, form: str | None = None, orientation: str | None = None,
                     conditioning: str | None = None, temperature: Quantity | None = None,
                     allowable: bool = False) -> PropertyResolution:
    """Property `name` of `profile` under the requested conditions: resolved only when exactly one sourced
    value matches the form, orientation, conditioning, temperature (its tested range contains the
    requested one) and, with `allowable`, a justified allowable basis. Never another property, another
    profile or a default: unknown when no value exists, refused when values exist but none applies."""
    if name not in MATERIAL_PROPERTIES:
        raise IntentError(f"{name!r} is not a material property name")

    def answer(status: str, reasons: list[str], match: MaterialProperty | None = None) -> PropertyResolution:
        if match is None:
            return PropertyResolution(status, profile.id, name, None, None, (), None, tuple(reasons))
        conditions = {"test_standard": match.test_standard.value if match.test_standard.known else None,
                      "temperature": match.temperature.value if match.temperature.known else None,
                      "conditioning": match.conditioning,
                      "orientation": match.orientation.value if match.orientation is not None and match.orientation.known else None}
        return PropertyResolution(status, profile.id, name, match.value.value, match.value.basis, match.value.sources,
                                  types.MappingProxyType(conditions), tuple(reasons))

    reasons: list[str] = []
    if form is not None and profile.form != form:
        return answer("refused", [f"{profile.id} is {profile.form} material; {form} properties were requested"])
    if allowable and (profile.level == "family" or profile.typical_not_allowable):
        guidance = "family guidance" if profile.level == "family" else "typical_not_allowable"
        return answer("refused", [f"{profile.id} is {guidance}: it holds no design allowables"])
    candidates = [p for p in profile.properties if p.name == name and p.value.known]
    if not candidates:
        return answer("unknown", [f"{profile.id} records no known {name}"])
    matches = []
    for candidate in candidates:
        why: list[str] = []
        if conditioning is not None and candidate.conditioning != conditioning:
            why.append(f"tested {candidate.conditioning}, {conditioning} requested")
        if orientation is not None:
            stated = candidate.orientation.value if candidate.orientation is not None and candidate.orientation.known else None
            if not isinstance(stated, str) or stated.strip().lower() != orientation.strip().lower():
                why.append(f"orientation {stated or 'not stated'}, {orientation} requested")
        if temperature is not None:
            tested = candidate.temperature.value if candidate.temperature.known else None
            if not isinstance(tested, Quantity) or not tested.contains(temperature):
                why.append(f"tested at {tested or 'an unstated temperature'}, {temperature} requested")
        if allowable and candidate.value.basis != "allowable":
            why.append(f"{candidate.value.basis} value, an allowable requested")
        if why:
            reasons.append("; ".join(why))
        else:
            matches.append(candidate)
    if len(matches) > 1:
        values = {str(m.value.value) for m in matches}
        if len(values) > 1:
            return answer("refused", [f"{len(matches)} {name} values match with different values: {', '.join(sorted(values))}"])
    if matches:
        return answer("resolved", [], matches[0])
    return answer("refused", reasons)


@dataclass(frozen=True)
class CapabilityResolution:
    status: str  # calibrated, unknown or refused
    profile: str
    characteristic: str
    calibration: Calibration | None
    reasons: tuple[str, ...]


# The reason capability_limit gives for a calibration whose size scope is smaller than the request.
SIZE_SCOPE_REASON = "covers sizes up to"


def capability_limit(profile: CapabilityProfile, characteristic: str, *, material: str, on: date | None = None,
                     tool: str | None = None, orientation: str | None = None, feature: str | None = None,
                     size: float | None = None) -> CapabilityResolution:
    """The calibration that demonstrates `characteristic` for `material` (and the tool, orientation and
    feature when given), valid on `on`, whose size scope (when it states one) covers `size` mm. The first that fits
    wins, so a smaller coupon listed first never hides a larger one. Machine parameters and nominal resolution never
    answer it."""
    if profile.template:
        return CapabilityResolution("refused", profile.id, characteristic, None, (f"{profile.id} is a template, not a capability",))
    reasons = []
    wanted = {"tool": tool, "orientation": orientation, "feature": feature}
    for calibration in profile.calibrations:
        if calibration.characteristic != characteristic:
            continue
        why = _scope_mismatches(calibration, material, wanted, on, size)
        if why:
            reasons.extend(why)
            continue
        return CapabilityResolution("calibrated", profile.id, characteristic, calibration, ())
    if not reasons:
        reasons.append(f"{profile.id} has no calibration of {characteristic}")
        return CapabilityResolution("unknown", profile.id, characteristic, None, tuple(reasons))
    return CapabilityResolution("refused", profile.id, characteristic, None, tuple(reasons))


def _scope_mismatches(calibration: Calibration, material: str, wanted: Mapping[str, str | None], on: date | None,
                      size: float | None) -> list[str]:
    """Why `calibration` does not demonstrate the request: another material, tool, orientation or feature, an expiry
    before `on`, or a size scope smaller than `size` mm."""
    scope, why = calibration.scope, []
    if scope.get("material") != material:
        why.append(f"{calibration.id} is for {scope.get('material')}")
    for key, value in wanted.items():
        if value is not None and scope.get(key) != value:
            why.append(f"{calibration.id} {key} is {scope.get(key) or 'not stated'}, {value} requested")
    if on is not None and calibration.valid_until and calibration.valid_until < on.isoformat():
        why.append(f"{calibration.id} expired on {calibration.valid_until}")
    covered = scope.get("size")
    if size is not None and isinstance(covered, Mapping):
        try:
            largest = quantity(covered).to("mm").bounds[1]
        except IntentError as error:  # a record read without its checks: refuse the scope, never crash the check
            why.append(f"{calibration.id} size scope is not a length ({error})")
            return why
        if size > largest + 1e-9:
            why.append(f"{calibration.id} {SIZE_SCOPE_REASON} {largest:g} mm, {size:g} mm requested")
    return why


# --- required checks -------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Obligation:
    id: str  # family@anchor
    family: str
    subject: Mapping[str, Any]
    level: str
    stage: str
    basis: str
    requirement: str | None = None
    criterion: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """The schema form (manufacturing.schema.json#/$defs/obligation)."""
        data: dict[str, Any] = {"id": self.id, "family": self.family, "subject": thaw(self.subject), "level": self.level,
                                "stage": self.stage, "basis": self.basis}
        if self.requirement:
            data["requirement"] = self.requirement
        if self.criterion:
            data["criterion"] = self.criterion
        return data


# Per route kind: (check family, evidence level, stage due). An unknown kind gets process.<kind>.coverage only.
PROCESS_CHECKS: Mapping[str, tuple[tuple[str, str, str], ...]] = types.MappingProxyType({
    "fdm": (("orientation", "process_planned", "detail"), ("geometry", "geometrically_manufacturable", "parts"),
            ("support", "process_planned", "detail"), ("thermal_distortion", "geometrically_manufacturable", "detail"),
            ("trial", "physically_validated", "release")),
    "resin": (("orientation", "process_planned", "detail"), ("geometry", "geometrically_manufacturable", "parts"),
              ("support", "process_planned", "detail"), ("drainage", "geometrically_manufacturable", "detail"),
              ("post_cure", "process_planned", "detail"), ("trial", "physically_validated", "release")),
    "cnc": (("stock", "process_planned", "parts"), ("setup", "process_planned", "detail"),
            ("access", "geometrically_manufacturable", "detail"), ("features", "geometrically_manufacturable", "parts"),
            ("precision", "process_planned", "detail")),
    "laser": (("material", "process_planned", "parts"), ("kerf", "geometrically_manufacturable", "detail"),
              ("features", "geometrically_manufacturable", "parts"), ("thermal", "process_planned", "detail"),
              ("nesting", "process_planned", "detail"), ("fit_coupon", "physically_validated", "release")),
    "waterjet": (("material", "process_planned", "parts"), ("kerf", "geometrically_manufacturable", "detail"),
                 ("features", "geometrically_manufacturable", "parts"), ("edge", "process_planned", "detail")),
    "plasma": (("material", "process_planned", "parts"), ("kerf", "geometrically_manufacturable", "detail"),
               ("features", "geometrically_manufacturable", "parts"), ("thermal", "process_planned", "detail")),
    "sheet_forming": (("bend", "geometrically_manufacturable", "detail"), ("sequence", "process_planned", "detail"),
                      ("distortion", "process_planned", "detail")),
    "casting": (("draft", "geometrically_manufacturable", "detail"), ("walls", "geometrically_manufacturable", "parts"),
                ("tooling", "process_planned", "detail"), ("defects", "process_planned", "detail")),
    "moulding": (("draft", "geometrically_manufacturable", "detail"), ("walls", "geometrically_manufacturable", "parts"),
                 ("tooling", "process_planned", "detail"), ("defects", "process_planned", "detail")),
    "extrusion": (("profile", "geometrically_manufacturable", "parts"), ("secondary", "process_planned", "detail")),
    "drilling": (("access", "geometrically_manufacturable", "detail"), ("feature", "geometrically_manufacturable", "detail")),
    "reaming": (("access", "geometrically_manufacturable", "detail"), ("allowance", "process_planned", "detail")),
    "tapping": (("access", "geometrically_manufacturable", "detail"), ("engagement", "geometrically_manufacturable", "detail")),
    "finishing": (("allowance", "process_planned", "detail"), ("access", "geometrically_manufacturable", "detail")),
    "coating": (("allowance", "process_planned", "detail"), ("access", "process_planned", "detail")),
    "joining": (("access", "geometrically_manufacturable", "detail"), ("distortion", "process_planned", "detail")),
    "inspection": (("access", "geometrically_manufacturable", "detail"), ("capability", "process_planned", "detail")),
    "purchased": (("identity", "process_planned", "parts"), ("rating", "process_planned", "parts"),
                  ("dimensions", "geometrically_manufacturable", "parts"), ("mounting", "process_planned", "detail"),
                  ("availability", "process_planned", "parts")),
})
_LEVEL_STAGE = {"cad_valid": "layout", "geometrically_manufacturable": "parts", "process_planned": "detail",
                "simulation_supported": "analysis", "physically_validated": "release"}
# The assembly operation kinds that make a joint, each with its assembly.joint evidence obligation
# (cad-record assembly_operations.JOINING is the same set).
JOINING_KINDS = frozenset(("press_fit", "heat_set", "retaining_ring", "insert", "fasten", "join"))


def obligation_anchor(subject: Mapping[str, Any]) -> str:
    """The anchor of an obligation id for `subject`: product, PN[.component][/operation][#feature], IF[#feature],
    REQ or op:<assembly operation>."""
    if subject.get("PN"):
        anchor = f"{subject['PN']}"
        if subject.get("component"):
            anchor += f".{subject['component']}"
        if subject.get("operation"):
            anchor += f"/{subject['operation']}"
        if subject.get("feature"):
            anchor += f"#{subject['feature']}"
        return anchor
    if subject.get("interface"):
        return f"{subject['interface']}" + (f"#{subject['feature']}" if subject.get("feature") else "")
    if subject.get("assembly_operation"):
        return f"op:{subject['assembly_operation']}"
    if subject.get("requirements"):
        return f"{subject['requirements'][0]}"
    return "product"


def interfaces_of(record: Record, pn: str, part_number: Callable[[Any], Any] | None = None) -> tuple[str, ...]:
    """The ids of the interfaces that name part number `pn` as their from or to end, in record order. `part_number`
    maps an end to the part number it names (an @occurrence to its PN); without it an end is compared as written."""
    resolve = part_number or (lambda ref: ref)
    return tuple(ident for ident, item in record.interfaces.items()
                 if pn in (resolve(item.raw.get("from")), resolve(item.raw.get("to"))))


# The product-level check that the product exports (cad-model product.step and product.glb) hold exactly the delivered
# product; release requires it passed for a version that seals them.
PRODUCT_EXPORTS_FAMILY = "cad.product_exports"
PRODUCT_EXPORTS_OBLIGATION = f"{PRODUCT_EXPORTS_FAMILY}@product"


def required_checks(record: Record) -> tuple[Obligation, ...]:
    """Every check obligation the record implies, enumerated before any producer runs, sorted by id:
    each made component x route operation x its kind's check families; purchase checks for bought items;
    function-driven engineering checks; the product exports holding exactly the delivered product; critical interface
    fit, stack, acceptance, contact and play (play for every critical interface that declares it or rotates or slides);
    assembly order (per operation and for the
    whole product) and access, and joint evidence for every joining operation; requirement evidence levels;
    and an intent.* obligation for every unknown
    input that hides checks. A producer cannot shrink this list by omission; an unknown route kind gets
    process.<kind>.coverage, which no handler can pass. Distinct checks that share one anchor keep their own ids: the
    first keeps family@anchor, so references to it still resolve, and each later one adds ~<qualifier> (its critical
    surface id, or its policy requirement). Distinct checks no qualifier separates add an unknown
    intent.obligation_identity obligation that names them."""
    found: dict[str, Obligation] = {}
    # obligation id -> the distinct checks that share it, keyed by their identity (the first is the one found holds)
    collided: dict[str, dict[tuple[Any, ...], Obligation]] = {}

    def add(family: str, subject: Mapping[str, Any], level: str, stage: str, basis: str, *,
            requirement: str | None = None, criterion: str | None = None, qualifier: str | None = None) -> None:
        clean = {key: value for key, value in subject.items() if value is not None}
        ident = f"{family}@{obligation_anchor(clean)}"
        obligation = Obligation(ident, family, types.MappingProxyType(clean), level, stage, basis, requirement, criterion)
        identity = _identity(obligation, qualifier)
        if ident in collided and identity not in collided[ident] and qualifier is not None:
            ident = f"{ident}~{qualifier}"
            obligation = Obligation(ident, family, obligation.subject, level, stage, basis, requirement, criterion)
        found.setdefault(ident, obligation)
        collided.setdefault(ident, {}).setdefault(identity, obligation)

    use_case = record.use_case
    add("intent.use_case", {"product": True}, "process_planned", "intent",
        "a final material and process recommendation needs a confirmed use case")
    add(PRODUCT_EXPORTS_FAMILY, {"product": True}, "cad_valid", "layout",
        "product.step and product.glb hold exactly the delivered product (cad-record sourcing.delivered)")
    for pn, part in sorted(record.parts.items()):
        _part_checks(record, pn, part, add)
    for if_id, interface in sorted(record.interfaces.items()):
        _heat_set_checks(if_id, interface, add)
        _interface_checks(if_id, interface, add)
    operations = record.assembly_operations
    if is_fact(operations):
        if not operations.not_applicable:
            add("intent.assembly_operations", {"product": True}, "process_planned", "detail",
                f"the assembly operations are {operations.status}: installation order and access stay unchecked")
    else:
        for operation in operations:
            ident = operation.get("id")
            if isinstance(ident, str):
                add("assembly.order", {"assembly_operation": ident}, "process_planned", "detail",
                    f"assembly operation {ident}: installation order and closure")
                add("assembly.access", {"assembly_operation": ident}, "geometrically_manufacturable", "detail",
                    f"assembly operation {ident}: part path and tool reach")
                if operation.get("kind") in JOINING_KINDS:
                    add("assembly.joint", {"assembly_operation": ident}, "process_planned", "detail",
                        f"assembly operation {ident}: engagement, edge distance, retention, torque and host suitability")
        add("assembly.order", {"product": True}, "process_planned", "detail",
            "every assembly occurrence is installed by an operation, apart from the assembly's one base")
    for req_id, requirement in sorted(record.requirements.items()):
        for level in requirement.evidence_required:
            add(f"requirement.{level}", {"product": True, "requirements": (req_id,)}, level, _LEVEL_STAGE[level],
                f"{req_id} evidence_required {level}", requirement=req_id)
    if not is_fact(use_case.evidence_policy):
        for item in use_case.evidence_policy:
            policy_level = item.get("level")
            subject = item.get("subject") or (
                {"product": True, "requirements": (item["requirement"],)} if item.get("requirement") else None)
            if subject is not None and policy_level in _LEVEL_STAGE:
                add(f"policy.{policy_level}", subject, policy_level, _LEVEL_STAGE[policy_level], "the approved evidence policy",
                    requirement=item.get("requirement"), qualifier=item.get("requirement"))
    found.update(_identity_collisions(collided))
    return tuple(found[key] for key in sorted(found))


# The record-derived obligation that reports distinct checks sharing one obligation id; no producer can pass it.
OBLIGATION_IDENTITY_FAMILY = "intent.obligation_identity"


def obligation_base(obligation_id: str) -> str:
    """`obligation_id` without its ~qualifier: family@anchor, the id the first of the checks sharing an anchor holds."""
    return obligation_id.split("~", 1)[0]


def _identity(obligation: Obligation, qualifier: str | None) -> tuple[Any, ...]:
    """What makes two checks distinct: subject (occurrence, component, operation, feature and region), requirement,
    criterion and qualifier (the critical surface a check is about). The basis text is not identity: one check may be
    derived from two functions."""
    return (tuple(sorted((key, repr(value)) for key, value in obligation.subject.items())), obligation.requirement,
            obligation.criterion, qualifier)


def _identity_collisions(collided: Mapping[str, Mapping[tuple[Any, ...], Obligation]]) -> dict[str, Obligation]:
    """One intent.obligation_identity obligation per anchor where distinct checks still share an obligation id: no
    qualifier separates them (two policy obligations on two occurrences of a part with no requirement; the id grammar
    holds no occurrence). The first check keeps the id and the others would vanish, so this obligation names them all
    and stays unknown: the collision is never a pass. Its remedy keeps the recorded subjects and levels."""
    texts: dict[str, list[str]] = {}
    firsts: dict[str, Obligation] = {}
    stages: dict[str, str] = {}
    policy: dict[str, bool] = {}
    for ident, distinct in sorted(collided.items()):
        if len(distinct) < 2:
            continue
        members = list(distinct.values())
        anchor = obligation_base(ident).split("@", 1)[1]
        firsts.setdefault(anchor, members[0])
        texts.setdefault(anchor, []).append(f"{ident} stands for {len(members)} checks: "
                                            + "; ".join(_identity_text(member) for member in members))
        stages[anchor] = min((stages.get(anchor, members[0].stage), *(m.stage for m in members)), key=STAGES.index)
        policy[anchor] = policy.get(anchor, False) or members[0].family.startswith("policy.")
    found = {}
    for anchor, first in firsts.items():
        ident = f"{OBLIGATION_IDENTITY_FAMILY}@{anchor}"
        remedy = ("name the requirement each evidence_policy obligation serves (a requirement of its own for each), so "
                  "each gets an id of its own" if policy[anchor] else
                  "give each critical surface an id of its own, so each check gets an id of its own")
        found[ident] = Obligation(ident, OBLIGATION_IDENTITY_FAMILY, first.subject, "process_planned", stages[anchor],
                                  " | ".join(texts[anchor]) + ". One result cannot answer them all, so none of them is "
                                  f"checked: {remedy}; the recorded subjects and evidence levels stay as they are")
    return found


def _identity_text(obligation: Obligation) -> str:
    details = [f"{key} {obligation.subject[key]}" for key in ("occurrence", "component", "operation", "region")
               if obligation.subject.get(key)]
    if obligation.requirement:
        details.append(f"requirement {obligation.requirement}")
    return obligation.basis + (f" ({', '.join(details)})" if details else "")


def _part_checks(record: Record, pn: str, part: Part, add: Any) -> None:
    if part.existing:
        return  # outside the product: no route, purchase or process obligations, and no intent to complete
    intent = part.intent
    made = part.made
    base = {"PN": pn}
    if is_fact(intent.functions):
        add("intent.functions", base, "process_planned", "parts", f"{pn} functions are {intent.functions.status}")
    if made and is_fact(intent.material_profile) and not intent.material_profile.not_applicable:
        add("intent.material", base, "process_planned", "parts", f"{pn} material profile is {intent.material_profile.status}")
    if made and is_fact(intent.critical_surfaces) and intent.critical_surfaces.unknown:
        add("intent.critical_surfaces", base, "process_planned", "detail", f"{pn} critical surfaces are unknown")
    route = intent.route
    if is_fact(route):
        add("intent.route", base, "process_planned", "parts", f"{pn} route is {route.status}: its process checks are unknown")
    components = tuple(c.id for c in intent.components) or (None,)
    if made:
        for component in components:
            add("cad.readback", {**base, "component": component}, "cad_valid", "layout",
                f"{pn}: B-rep validity, readback, manufacturing state and component identity")
            add("route.consistency", {**base, "component": component}, "process_planned", "parts",
                f"{pn}: route, profile, material and capability consistency")
        add("cad.glb_step_agreement", base, "cad_valid", "layout",
            f"{pn}: each occurrence's assembly.glb node shows its assembly.step shape and placement")
        if part.process == "fdm":
            add("export.3mf", base, "cad_valid", "layout", f"{pn}: part.3mf, the FDM part's 3MF export of geometry, material and colour")
    if not is_fact(route):
        for op in route:
            targets = [c for c in components if c is None or not op.components or c in op.components]
            kind = op.kind
            checks = PROCESS_CHECKS.get(kind) if kind is not None and op.known_kind else None
            for component in targets if kind != "purchased" else (None,):
                subject = {**base, "component": component, "operation": op.id}
                if checks is None:
                    add(f"process.{kind}.coverage", subject, "process_planned", "parts",
                        f"{pn} {op.id}: route kind {kind} has no check contract")
                    continue
                family_prefix = "purchase" if kind == "purchased" else f"process.{kind}"
                for family, level, stage in checks:
                    add(f"{family_prefix}.{family}", subject, level, stage, f"{pn} {op.id}: {kind} operation")
                if op.kind != "purchased":
                    add("route.operation_plan", subject, "process_planned", "detail",
                        f"{pn} {op.id}: setup, orientation, supports, allowances and inspection")
    functions = () if is_fact(intent.functions) else tuple(f.function for f in intent.functions)
    functions += tuple(f.function for c in intent.components for f in c.functions)
    if "optical" in functions or part.transparency in ("translucent", "transparent"):
        add("optical.route", base, "process_planned", "parts",
            f"{pn} has an optical requirement: only a purchased transparent part or identified transparent stock on a suitable "
            "route qualifies; opaque processes are eliminated")
        add("optical.transmission", base, "physically_validated", "release", f"{pn}: sourced optical grade and transmission")
    if not made:
        return  # a catalogue item's function is its rating: the purchase checks cover it, never made-part checks
    loads = _part_loads(record, part)
    if "structural" in functions:
        add("engineering.strength", base, "simulation_supported", "analysis", f"{pn} is structural")
        add("engineering.stiffness", base, "simulation_supported", "analysis", f"{pn} is structural")
        if loads is None:
            add("engineering.load_cases", base, "simulation_supported", "analysis",
                f"{pn} is structural and its loads are unknown: creep, fatigue and impact applicability is unknown")
        else:
            kinds = {load.kind for load in loads}
            if "sustained" in kinds:
                add("engineering.creep", base, "physically_validated", "analysis", f"{pn} carries a sustained load")
            if kinds & {"cyclic", "vibration"}:
                add("engineering.fatigue", base, "physically_validated", "analysis", f"{pn} carries a cyclic load")
            if "impact" in kinds:
                add("engineering.impact", base, "physically_validated", "analysis", f"{pn} carries an impact load")
        if not is_fact(route) and any(op.kind in ADDITIVE_KINDS for op in route):
            add("engineering.anisotropy", base, "simulation_supported", "analysis",
                f"{pn} is structural and additive: layer direction against its loads")
    surfaces = () if is_fact(intent.critical_surfaces) else intent.critical_surfaces
    for surface in surfaces:
        subject = {**base, "component": surface.component, "feature": surface.feature, "region": surface.region}
        add("feature.fit", subject, "process_planned", "detail", f"{pn} {surface.id}: {surface.role} finished fit",
            qualifier=surface.id)
        add("feature.inspection", subject, "physically_validated", "release", f"{pn} {surface.id}: inspected finished dimensions",
            qualifier=surface.id)
    for function, family, level in _FUNCTION_CHECKS:
        if function in functions:
            add(family, base, level, "detail" if level == "process_planned" else "analysis", f"{pn} function {function}")


_FUNCTION_CHECKS = (
    ("rotating", "contact.wear", "physically_validated"), ("sliding", "contact.wear", "physically_validated"),
    ("rotating", "motion.play", "simulation_supported"), ("sliding", "motion.play", "simulation_supported"),
    ("locating", "feature.datum", "process_planned"), ("sealing", "function.sealing", "physically_validated"),
    ("spring", "function.spring", "physically_validated"), ("thermal", "function.thermal", "physically_validated"),
    ("electrical", "function.electrical", "physically_validated"), ("other", "function.other", "physically_validated"),
)


def _part_loads(record: Record, part: Part) -> tuple[Load, ...] | None:
    """The use-case loads `part` carries, or None when they are unknown."""
    service = part.intent.service
    loads = record.use_case.loads
    if is_fact(service) or is_fact(loads):
        return None
    wanted = service.get("loads")
    if not wanted:
        return None
    return tuple(load for load in loads if load.id in wanted)


def heat_set_feature(entry: int) -> str:
    """The feature that names the `entry`-th (0-based) `limits.heat_set` declaration of an interface in its
    cad.declared_dimensions obligation id: `heat_set_1` for the first. cad-model check names its rows the same way."""
    return f"heat_set_{entry + 1}"


def _heat_set_checks(if_id: str, interface: Interface, add: Any) -> None:
    """One cad.declared_dimensions obligation per heat-set declaration, whatever the interface's criticality: the
    record states a hole diameter, and the sealed model must have a bore of that size in the host."""
    limits = interface.raw.get("limits")
    entries = limits.get("heat_set") if isinstance(limits, Mapping) else None
    if not isinstance(entries, (list, tuple)):
        return
    for entry in range(len(entries)):
        add("cad.declared_dimensions", {"interface": if_id, "feature": heat_set_feature(entry)}, "cad_valid", "layout",
            f"{if_id} heat_set {entry + 1}: the declared hole_d is a bore of its host in the sealed model")


def _interface_checks(if_id: str, interface: Interface, add: Any) -> None:
    functional = interface.functional
    base = {"interface": if_id}
    critical = functional.critical
    if not critical.known:
        if not critical.not_applicable:
            add("intent.interface", base, "process_planned", "parts", f"{if_id} criticality is unknown")
        return
    if critical.value is not True:
        return
    raw = functional.raw
    add("interface.fit", base, "process_planned", "detail", f"{if_id} is critical: intended fit after tolerances")
    add("interface.stack", base, "process_planned", "detail",
        f"{if_id} is critical: worst-case stack with manufacturing, assembly, thermal, moisture, wear and deformation contributors")
    for index, acceptance in enumerate(raw.get("acceptance") or ()):
        criterion = acceptance.get("criterion") or {}
        name = criterion.get("id") or f"criterion_{index + 1}"
        for level in (acceptance.get("verification") or {}).get("evidence_required") or ():
            add(f"interface.acceptance.{level}", {**base, "feature": name}, level, _LEVEL_STAGE.get(level, "detail"),
                f"{if_id} acceptance {name}", criterion=name)
    if raw.get("contact"):
        add("contact.compatibility", base, "physically_validated", "analysis", f"{if_id}: contacting pair friction, wear and life")
    functions = set() if is_fact(functional.functions) else {entry.function for entry in functional.functions}
    contact_load = (raw.get("contact") or {}).get("load")
    if "structural" in functions and (raw.get("loads") or (isinstance(contact_load, Mapping) and contact_load.get("status") == "known")):
        add("engineering.stop_reaction", base, "simulation_supported", "layout",
            f"{if_id} is critical, structural and loaded: the reaction its contact carries, from the modelled contact")
    if raw.get("play") or functions & {"rotating", "sliding"}:
        add("motion.play", base, "simulation_supported", "analysis", f"{if_id}: function under play and tolerance extremes")


# --- source artifacts and blank records --------------------------------------------------------------------------

def source_artifacts(record: Record) -> Mapping[str, str]:
    """Every source artifact (relative to hw/) the record's facts name, with the sha256 they name."""
    return record.artifacts


def profile_document(data: Mapping[str, Any], kind: str, path: str) -> tuple[tuple[Any, ...], tuple[Problem, ...]]:
    """The profiles of a standalone capability (`kind` "capabilities") or material ("materials") document,
    such as the plugin's capability templates or starter material profiles, and their problems beside the
    schema: vocabularies, units, numbers, levels, forms and allowables. References to project records are
    not resolved; a project resolves them when it copies a profile in."""
    if kind not in ("capabilities", "materials"):
        raise IntentError(f"a profile document is capabilities or materials, not {kind!r}")
    reader = _Reader({}, None)
    reader.numbers(data, path, "")
    profiles = []
    for index, item in enumerate(data.get("profiles") or () if isinstance(data, Mapping) else ()):
        if not isinstance(item, Mapping):
            continue
        pointer = f"profiles/{index}"
        reader.define(kind, item.get("id"), path, pointer)
        build = reader.capability_profile if kind == "capabilities" else reader.material_profile
        profile = build(item, path, pointer, register=False)
        if profile is not None:
            profiles.append(profile)
    return tuple(profiles), tuple(reader.problems)


def blank_use_case(reason: str, questions: Mapping[str, str]) -> dict[str, Any]:
    """A use_case block whose every field is unknown for `reason`, each naming the question id
    `questions` maps its USE_CASE_QUESTIONS path to. Used by the template and fixtures."""
    def unknown(path: str) -> dict[str, Any]:
        item = {"status": "unknown", "reason": reason}
        if questions.get(path):
            item["question"] = questions[path]
        return item

    block: dict[str, Any] = {"status": "unknown", "service_class": unknown("service_class")}
    for dotted in ("purpose", "users"):
        block[dotted] = unknown(dotted)
    for dotted in USE_CASE_FACTS[2:]:
        group, name = dotted.split(".")
        block.setdefault(group, {})[name] = unknown(dotted)
        if dotted == "environment.corrosion":
            block["loads"] = unknown("loads")
    capability = block.pop("capability")
    block["capability"] = {"equipment": {"status": "not_applicable", "reason": EQUIPMENT_OPTIONAL}, **capability}
    release = block.pop("release_scope")
    block["release_scope"] = release
    block["evidence_policy"] = unknown("evidence_policy")
    order = ["status", "service_class", "purpose", "users", "environment", "loads", "motion", "accuracy", "consequences",
             "logistics", "capability", "release_scope", "evidence_policy"]
    return {key: block[key] for key in order}


def use_case_questions(first_number: int, owner: str = "lead") -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The USE_CASE_QUESTIONS as open_questions entries numbered from Q-<first_number>, and the map of each
    use-case path to its question id, for blank_use_case."""
    entries, paths = [], {}
    for offset, (text, covered, material, safety) in enumerate(USE_CASE_QUESTIONS):
        ident = f"Q-{first_number + offset:03d}"
        entries.append({"id": ident, "question": text, "subjects": [{"product": True}], "owner": owner,
                        "material_to_choice": material, "safety_relevant": safety, "status": "open"})
        for path in covered:
            paths[path] = ident
    return entries, paths
