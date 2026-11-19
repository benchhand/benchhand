# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Material conditions and evidence of a static FEA case: where E, nu, density and yield come from, and
what a completed solve can support.

A versioned case of a record version 2 project takes its material only from the analysed part's material
profile (cad-record `manufacturing_intent.resolve_property`), under the conditions the case states: the
conditioning, the temperature and, for orientation-dependent material, the orientation. Before any solve it
refuses numeric case values (copied, unsourced properties), a part whose material is only a name, a family
or density-only profile, a profile whose form or process contradicts the part's route, a property tested
under other conditions, and orientation-dependent material without an approved effective isotropic model:
the solver is static, linear and isotropic. Nothing is derived from density or inherited from another
case, part or profile.

Explicit numbers stay readable in a standalone case: they are labelled
unsourced and the result is exploratory, never service evidence. A completed sourced solve is
simulation-supported evidence at most, never physical validation; `engineering.*` outcomes pass only with a
sourced acceptance criterion, service-grade properties, service loads covered as vectors and a mesh convergence
study that is approved, bound to this geometry, material, supports, forces and mesh, and converged. A stress case's
convergence is its own two-mesh study instead, and only that study's settled stress away from supports and loads can
pass strength. Anything unproven is unknown, never pass, and never service evidence.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import yaml

import fea_modes
import fea_stress

INTENT_PATH = Path(__file__).resolve().parents[2] / "cad-record" / "scripts" / "manufacturing_intent.py"
DECISIONS_PATH = Path(__file__).resolve().parents[2] / "cad-record" / "scripts" / "decision_records.py"
ASSEMBLY_DATA_PATH = INTENT_PATH.with_name("assembly_data.py")
POSE_PATH = Path(__file__).resolve().parents[2] / "cad-motion" / "scripts" / "pose.py"
# The code behind a load direction's frame: pose.py and what it runs at import and for a rotation (coupling.py,
# layout.py), and assembly_data.py. fea_static seals their bytes into the recipe.
FRAME_CODE = (ASSEMBLY_DATA_PATH, POSE_PATH, POSE_PATH.with_name("coupling.py"), ASSEMBLY_DATA_PATH.with_name("layout.py"))
# The code behind a record gap: cad-record's schema registry and its bundled schemas, which refer to each other.
# fea_static seals their bytes into the recipe.
SCHEMA_CODE = (INTENT_PATH.with_name("schema_registry.py"), *sorted((INTENT_PATH.parents[1] / "schemas").glob("*.json")))
# The record documents the assessment reads besides the analysed part's part.yaml, checked by cad-record before use.
RECORD_READS = ("product.yaml", "requirements.yaml", "assembly.yaml", "interfaces.yaml", "manufacturing/materials.yaml")
MATERIALS_DOCUMENT = "hw/manufacturing/materials.yaml"
PRODUCER = "cad-analysis fea_static.py"
EXPLICIT_KEYS = ("E", "nu", "density", "yield")
PROFILE_KEYS = ("profile", "conditions", "effective_isotropic")
CONDITIONINGS = ("dry", "conditioned", "as_received")
# solver input -> (material profile property, the units the solver takes)
PROPERTIES = {"E": ("youngs_modulus", "MPa"), "nu": ("poisson_ratio", "dimensionless"),
              "density": ("density", "kg_m3"), "yield": ("yield_strength", "MPa")}
# A yield allowable (not a typical value) is required for these service classes.
ALLOWABLE_SERVICE = frozenset({"production", "safety_critical"})
# Bases that may support service evidence; estimates, assumptions and bare user statements may not.
SERVICE_BASES = frozenset({"measured", "supplier_typical", "allowable", "standard"})
# The required checks a static isotropic solve can answer, all at simulation_supported.
ENGINEERING_FAMILIES = ("engineering.strength", "engineering.stiffness", "engineering.anisotropy")
# Route kinds that make the material state itself, and the profile form their properties have. A subtractive or
# cutting route keeps its stock's state, so the stock profile's own process lies upstream of the part's route.
FORMING = {"fdm": "printed", "resin": "printed", "casting": "cast", "moulding": "moulded"}
LEVEL = "simulation_supported"
DECISION = re.compile(r"DR-[0-9]{3}")
REQUIREMENT = re.compile(r"REQ-[0-9]{3}")
# A decision (mesh convergence, effective isotropic model) counts only when its MADR status is this. It is
# the old gate status APPROVED; PROVISIONAL is `proposed` and VOID is `rejected`, neither of them evidence.
APPROVED_STATUS = "accepted"


STUDY_BLOCK = re.compile(r"```mesh-convergence[ \t]*\r?\n(.*?)```", re.S)
MONITORED = ("max_von_mises_mpa", "max_displacement_mm")  # what a study must show settled
MIN_REFINEMENTS = 3
CONVERGENCE_LIMIT = 0.1  # the largest relative_change_max a study may claim for "settled"
# Tolerances for vector comparisons: a study's force components against the case's, per application region
# (relative to the region's force), and how far across a service load's direction the applied force may point
# (as a share of the service magnitude). Over-applying along the direction is conservative and allowed.
LOAD_BINDING_TOLERANCE = 1e-6
LOAD_CROSS_TOLERANCE = 0.01
METHOD = "static linear elastic isotropic FEA (FreeCAD FEM, Gmsh second-order tetrahedra, CalculiX)"
LIMITS = (
    "static, linear elastic, small displacement and isotropic: no contact, plasticity, buckling, dynamics or thermal load",
    "face loads only: no self-weight or inertia load",
    "peak von Mises stress at load faces, constraints and sharp re-entrant corners depends on the mesh",
    f"a stress case reads no stress within {fea_stress.K_EXCLUSION} x h_coarse of a fixed or load face: a peak on "
    "that cutoff gives no strength value, and the route to one at a support is linearisation (#209)",
    "a yield safety factor is not a fatigue, creep, wear, impact, buckling or service-life margin",
    "simulation-supported evidence at most, never physical validation",
)
# A stress case's mesh convergence is its own two-mesh study (fea_stress), never a decision record.
STUDY_METHOD = "two_mesh_stress_study"
STUDY_PENDING = "the case's own two-mesh stress study decides it after the solve"
# Why a strength obligation stays unknown in the modes whose solve gives no strength value.
FAST_STRENGTH = ("fast mode reports no stress figure: a strength outcome comes only from the separate two-mesh stress "
                 "study, a case with mode: stress")
FULL_STRENGTH = ("a full-mode yield safety factor rests on one mesh's peak von Mises stress, which does not settle at "
                 "supports, loads and edges: a strength pass comes only from the two-mesh stress study, a case with "
                 "mode: stress")
STUDY_STRENGTH = "the yield safety factor at the two-mesh stress study's settled stress away from supports and loads"


def decision_records() -> Any:
    """cad-record decision_records.py under the name every CAD skill shares."""
    return _shared("cad_record_decision_records", DECISIONS_PATH)


class Refused(ValueError):
    """The case cannot be solved as stated: every problem is a {code, message} row."""

    def __init__(self, problems: list[dict[str, str]]) -> None:
        super().__init__("; ".join(problem["message"] for problem in problems))
        self.problems = problems


def intent() -> Any:
    """cad-record manufacturing_intent.py under the name every CAD skill shares."""
    return _shared("cad_record_manufacturing_intent", INTENT_PATH)


def _shared(name: str, path: Path) -> Any:
    """The sibling-skill module at `path`, loaded once under the name every CAD skill shares."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"{path} not found")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[name]   # a half-run module must not stand for the real one
            raise
    return sys.modules[name]


def material_form(material: Mapping[str, Any]) -> str:
    """`explicit` for numbers given in the case, `profile` for the conditions of the part's material profile."""
    return "explicit" if any(key in material for key in EXPLICIT_KEYS) else "profile"


@dataclass
class Assessment:
    """What a case's material and evidence rest on, settled before the solve."""

    material: dict[str, Any]  # the report block: source, profile, conditions, properties
    evidence_gaps: list[str]  # why a completed solve cannot be service evidence; empty when it can
    version: str | None = None
    subject: dict[str, Any] | None = None
    binding: dict[str, Any] | None = None
    expected: list[dict[str, Any]] = field(default_factory=list)  # required_checks rows this case answers
    criteria: dict[str, dict[str, Any]] = field(default_factory=dict)  # strength/stiffness -> criterion row
    load_gaps: list[str] = field(default_factory=list)  # why the case does not cover the part's service loads
    convergence: dict[str, Any] = field(default_factory=dict)
    effective_isotropic: dict[str, Any] | None = None
    dependencies: dict[str, str] = field(default_factory=dict)  # semantic name -> sha256
    record_gaps: list[str] = field(default_factory=list)  # the problems cad-record finds in the documents read

    @property
    def sourced(self) -> bool:
        return self.material.get("source") == "profile"

    def all_gaps(self) -> list[str]:
        """Every reason this solve is not service evidence: the record's problems, the basis gaps, the uncovered
        service loads and an unproven mesh convergence. A sourced result carries all four; an exploratory one only
        its basis note."""
        gaps = list(self.record_gaps) + list(self.evidence_gaps)
        if self.sourced:
            gaps += self.load_gaps
            gaps += convergence_gaps(self.convergence)
        return gaps

    def recipe(self) -> dict[str, Any]:
        """The recipe fields that bind a published bundle to this material, criterion and configuration."""
        return {"material": self.material, "binding": self.binding, "acceptance": self.criteria,
                "mesh_convergence": self.convergence, "dependencies": self.dependencies}


def explicit(case: Mapping[str, Any], why: str) -> Assessment:
    """Explicit case numbers: readable, unsourced, exploratory, never service evidence."""
    material = case["material"]
    report = {"source": "unsourced_explicit", "name": material.get("name"),
              "values": {key: material[key] for key in EXPLICIT_KEYS},
              "note": f"{why}: explicit case numbers are not a material source; the result is exploratory and never "
                      "service evidence"}
    return Assessment(report, [report["note"]])


def read_record(record_dir: Path) -> tuple[Any, dict[str, Any]]:
    """The normalized record of a version's record snapshot and its parsed documents."""
    mi = intent()
    documents, problems = mi.read_documents(record_dir)
    if problems:
        raise Refused([{"code": "record.unreadable", "message": problem.message} for problem in problems])
    return mi.normalize_record(documents), documents


def assess_version(case: dict[str, Any], *, record_dir: Path, hw: Path, version: str) -> Assessment:
    """The material and evidence basis of a versioned case; Refused before the solve when it has none."""
    record, documents = read_record(record_dir)
    form = case["_material_form"]
    if record.version != 2:
        raise Refused([{"code": "record.version_unsupported",
                        "message": f"version {version}: the record snapshot's record_version is not supported"}])
    if form == "explicit":
        raise Refused([{"code": "material.inherited",
                        "message": "a record version 2 case takes E, nu, density and yield only from the part's material "
                                   "profile: numeric case values are copied, unsourced properties; replace them with "
                                   "material.conditions"}])
    return _Assessor(record, documents, case, record_dir, Path(hw), version).run()


class _Assessor:
    """One versioned record version 2 case: every refusal is collected, then raised together."""

    def __init__(self, record: Any, documents: Mapping[str, Any], case: dict[str, Any], record_dir: Path, hw: Path,
                 version: str) -> None:
        self.mi = intent()
        self.record, self.documents, self.case = record, documents, case
        self.record_dir, self.hw, self.version = record_dir, hw, version
        self.problems: list[dict[str, str]] = []
        self.gaps: list[str] = []
        self.dependencies: dict[str, str] = {}
        self.analysed: Mapping[str, Any] = {}
        self.checked: list[Any] = []  # cad-record's error problems in the documents read, materials.yaml aside
        self.materials_gaps: list[str] = []

    def refuse(self, code: str, message: str) -> None:
        self.problems.append({"code": code, "message": message})

    def check(self) -> None:
        if self.problems:
            raise Refused(self.problems)

    def check_record(self, pn: str) -> None:
        """cad-record's checks of the documents this case reads, and of no other: a problem in materials.yaml
        refuses the profile before the solve, and any other leaves every outcome unknown."""
        paths = [f"hw/{rel}" for rel in (*RECORD_READS, f"parts/{pn}/part.yaml")]
        found = [p for p in self.mi.check_documents(self.documents, paths) if p.severity == "error"]
        self.materials_gaps = [_record_gap(p) for p in found if p.path == MATERIALS_DOCUMENT]
        self.checked = [p for p in found if p.path != MATERIALS_DOCUMENT]

    def run(self) -> Assessment:
        subject = self.subject()
        self.check()
        self.check_record(subject["PN"])
        self.analysed = subject
        pn = subject["PN"]
        part = self.record.parts.get(pn)
        if part is None:
            self.refuse("material.subject", f"version {self.version}: the record snapshot has no part {pn}")
            self.check()
        self.depend("part_record", self.record_dir / "parts" / pn / "part.yaml")
        for name, rel in (("product_record", "product.yaml"), ("requirements_record", "requirements.yaml"),
                          ("assembly_record", "assembly.yaml"), ("materials_record", "manufacturing/materials.yaml")):
            self.depend(name, self.record_dir / rel)
        profile = self.profile(part)
        self.check()
        route = self.route(part, profile)
        conditions = self.conditions(profile)
        self.check()
        anisotropy = self.anisotropy(profile, conditions)
        allowable = self.allowable_required()
        properties, values = self.properties(profile, conditions, allowable)
        self.check()
        self.service_conditions(conditions)
        criteria = self.criteria()
        load_gaps = self.load_coverage(part)
        profile_sha = _digest(self.mi.thaw(profile.raw))
        convergence = self.convergence(profile, profile_sha)
        self.check()
        self.dependencies["material_profile"] = profile_sha
        material = {
            "source": "profile", "name": profile.raw.get("title") or profile.id,
            "profile": {"id": profile.id, "revision": profile.revision, "sha256": profile_sha, "level": profile.level,
                        "family": profile.family, "grade": profile.grade.value if profile.grade.known else None,
                        "form": profile.form, "process": route["profile_process"],
                        "typical_not_allowable": profile.typical_not_allowable},
            "conditions": {"conditioning": conditions["conditioning"], "temperature": conditions["temperature"].as_dict(),
                           "orientation": conditions["orientation"]},
            "allowable_required": allowable,
            "isotropy": anisotropy,
            "properties": properties,
        }
        self.case["material"] = {"name": material["name"], **values}
        binding = {"version": self.version, "record_version": 2, **subject, "part_revision": part.raw.get("rev"),
                   "material_profile": {"id": profile.id, "revision": profile.revision, "sha256": profile_sha},
                   "route": route["kinds"], "conditions": material["conditions"]}
        expected = [ob.as_dict() for ob in self.mi.required_checks(self.record)
                    if ob.family in ENGINEERING_FAMILIES and ob.subject.get("PN") == pn]
        return Assessment(material, self.gaps, self.version, subject, binding, expected, criteria, load_gaps, convergence,
                          anisotropy if anisotropy["model"] != "isotropic" else None, self.dependencies,
                          [_record_gap(p) for p in self.checked])

    # -- inputs ----------------------------------------------------------------------------------------------

    def depend(self, name: str, path: Path) -> str | None:
        if not path.is_file():
            return None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.dependencies[name] = digest
        return digest

    def decision(self, ref: str, name: str, code: str, what: str) -> Any:
        """The decision record `ref` under hw/decisions/, bound by its sha256; None (and a refusal) when the
        reader refuses it."""
        records = decision_records()
        try:
            record = records.find_decision(self.hw / "decisions", ref)
        except records.DecisionRecordError as error:
            self.refuse(code, f"{what} names {ref}, but {error}")
            return None
        self.depend(name, self.hw / "decisions" / record.file_name)
        return record

    def subject(self) -> dict[str, Any]:
        """The analysed PN, from `part` or from `occurrence` through the version's assembly.yaml."""
        if self.case.get("part"):
            return {"PN": self.case["part"]}
        occurrence = self.case.get("occurrence")
        if occurrence:
            assembly = self.documents.get("assembly.yaml")
            occurrences = assembly.get("occurrences") if isinstance(assembly, Mapping) else None
            placed = {item.get("id"): item.get("PN") for item in occurrences or () if isinstance(item, Mapping)}
            pn = placed.get(occurrence[1:])
            if isinstance(pn, str):
                return {"PN": pn, "occurrence": occurrence}
            self.refuse("material.subject", f"occurrence {occurrence} is not an occurrence of version {self.version}'s "
                                            "assembly.yaml, so its part and material profile are unknown")
            return {}
        self.refuse("material.subject", "a record version 2 case names its `part` or `occurrence`: the material profile "
                                        "is the analysed part's, never chosen by solid index or STEP content")
        return {}

    def profile(self, part: Any) -> Any:
        """The part's material profile: never its material name, another part's or a copied case's profile."""
        pn, reference = part.pn, part.intent.material_profile
        asserted = self.case["material"].get("profile")
        if self.mi.is_fact(reference):
            why = reference.reason or reference.status
            self.refuse("material.name_only",
                        f"{pn}'s material profile is {reference.status} ({why}); its material name {part.material!r} is "
                        "never a property source")
            return None
        if asserted is not None and asserted != reference:
            self.refuse("material.inherited",
                        f"the case names profile {asserted}, but {pn}'s intent names {reference}: a case copied from "
                        "another part or profile never supplies this part's properties")
        others = sorted({c.material_profile for c in part.intent.components
                         if isinstance(c.material_profile, str) and c.material_profile != reference})
        if others:
            self.refuse("material.component",
                        f"{pn}'s components use profiles {', '.join(others)} besides {reference}: one isotropic material "
                        "per solve cannot represent them")
        profile = self.record.materials.get(reference)
        if profile is None:
            self.refuse("material.name_only", f"{pn}'s profile {reference} is not in the version's "
                                              "manufacturing/materials.yaml")
            return None
        if profile.level != "grade":
            self.refuse("material.grade", f"{reference} is {profile.level} guidance, never a grade specification: "
                                          "name the grade profile the part is made of")
        for gap in self.materials_gaps:
            self.refuse("material.profile", gap)
        return profile

    def route(self, part: Any, profile: Any) -> dict[str, Any]:
        """The profile's form and process against the part's route: printed, cast or moulded properties only for
        a part its route prints, casts or moulds by that process, and never bulk values for such a part."""
        state = profile.raw.get("process_state") or {}
        made_by = state.get("process") if isinstance(state.get("process"), str) else None
        if profile.form == "printed" and made_by is None:
            self.refuse("material.form_process", f"{profile.id} is printed material whose process is unknown: printed "
                                                 "properties depend on the process that made them")
        route = part.intent.route
        if self.mi.is_fact(route):
            self.gaps.append(f"{part.pn}'s route is {route.status}: the material state the profile describes is unverified")
            return {"kinds": None, "profile_process": made_by}
        kinds = [op.kind for op in route]
        forming = [kind for kind in kinds if kind in FORMING]
        for kind in forming:
            if profile.form != FORMING[kind]:
                self.refuse("material.form_process",
                            f"{part.pn} is made by {kind}, but {profile.id} holds {profile.form} properties: only "
                            f"{FORMING[kind]} values describe that material")
            elif made_by is not None and made_by != kind:
                self.refuse("material.form_process", f"{profile.id} was tested as {made_by} material, but {part.pn} is made "
                                                     f"by {kind}")
        if profile.form in FORMING.values() and not any(FORMING[kind] == profile.form for kind in forming):
            self.refuse("material.form_process", f"{profile.id} holds {profile.form} properties, but {part.pn}'s route "
                                                 f"({', '.join(kinds) or 'empty'}) makes no {profile.form} material")
        return {"kinds": kinds, "profile_process": made_by}

    def conditions(self, profile: Any) -> dict[str, Any]:
        stated = self.case["material"]["conditions"]
        try:
            temperature = self.mi.quantity(stated["temperature"])
        except (self.mi.IntentError, TypeError, AttributeError) as exc:
            self.refuse("material.condition", f"material.conditions.temperature: {exc}")
            return {}
        if temperature.dimension != "temperature":
            self.refuse("material.condition", f"material.conditions.temperature is {temperature}, not a temperature")
        orientation = stated.get("orientation")
        oriented = profile.form == "printed" or any(p.orientation is not None and p.orientation.known
                                                    for p in profile.properties if p.name in _PROFILE_NAMES)
        if oriented and orientation is None:
            self.refuse("material.condition", f"{profile.id} has orientation-dependent properties: state "
                                              "material.conditions.orientation, the specimen direction the analysis takes")
        return {"conditioning": stated["conditioning"], "temperature": temperature, "orientation": orientation}

    def anisotropy(self, profile: Any, conditions: Mapping[str, Any]) -> dict[str, Any]:
        """Isotropic, or an approved effective isotropic model of orientation-dependent material at one orientation."""
        orientations = {str(p.orientation.value).strip().lower() for p in profile.properties
                        if p.name in _PROFILE_NAMES and p.orientation is not None and p.orientation.known}
        layup = (profile.raw.get("process_state") or {}).get("layup")
        reasons = ([f"{profile.id} is printed"] if profile.form == "printed" else []) + \
                  ([f"{profile.id} has a layup ({layup})"] if layup else []) + \
                  ([f"{profile.id} states properties at {len(orientations)} orientations"] if len(orientations) > 1 else [])
        if not reasons:
            return {"model": "isotropic", "reasons": []}
        model = self.case["material"].get("effective_isotropic")
        if model is None:
            self.refuse("material.anisotropy",
                        f"{'; '.join(reasons)}: the static solver is isotropic, so orientation-dependent material needs "
                        "material.effective_isotropic with the approved decision justifying it")
            return {"model": "unsupported", "reasons": reasons}
        decided = self.decision(model["decision"], f"decision_{model['decision'].lower().replace('-', '_')}",
                                "material.anisotropy", "material.effective_isotropic")
        if decided is not None and decided.status != APPROVED_STATUS:
            self.refuse("material.anisotropy", f"{model['decision']} is not approved: its status is {decided.status}, "
                                               f"not {APPROVED_STATUS}, and a decision that is not accepted is not evidence")
        return {"model": "effective_isotropic", "reasons": reasons, "orientation": conditions["orientation"],
                "decision": model["decision"], "justification": model["justification"]}

    def allowable_required(self) -> bool:
        service = self.record.use_case.service_class
        if not service.known or service.value == "unknown":
            self.gaps.append("the use case's service class is unknown, so whether a yield allowable is required is unknown")
            return False
        return service.value in ALLOWABLE_SERVICE

    def properties(self, profile: Any, conditions: Mapping[str, Any], allowable: bool) -> tuple[list[dict[str, Any]], dict[str, float]]:
        rows, values = [], {}
        for key, (name, units) in PROPERTIES.items():
            needs_allowable = allowable and key == "yield"
            found = self.mi.resolve_property(profile, name, orientation=conditions["orientation"],
                                             conditioning=conditions["conditioning"], temperature=conditions["temperature"],
                                             allowable=needs_allowable)
            row: dict[str, Any] = {"input": key, "property": name, "status": found.status, "reasons": list(found.reasons),
                                   "allowable_requested": needs_allowable}
            rows.append(row)
            if not found.resolved:
                code = ("material.allowable" if needs_allowable and found.status == "refused"
                        else "material.condition" if found.status == "refused" else "material.property")
                self.refuse(code, f"{profile.id} {name} is {found.status}: {'; '.join(found.reasons) or 'no value'}")
                continue
            lower, upper = found.value.to(units).bounds
            if lower != upper and key != "yield":
                self.refuse("material.property", f"{profile.id} {name} is the interval {found.value}: the solver takes one "
                                                 "value, never a default inside it")
                continue
            used = lower  # an interval yield is taken at its lower bound, the conservative end for a yield margin
            if lower != upper:
                row["bound"] = "lower"
            sources = [_plain(source) for source in found.sources]
            row.update(value=found.value.as_dict(), used=used, units=units, basis=found.basis, sources=sources,
                       conditions={k: v.as_dict() if isinstance(v, self.mi.Quantity) else v
                                   for k, v in (found.conditions or {}).items()})
            values[key] = used
            if key != "density" and found.basis not in SERVICE_BASES:
                self.gaps.append(f"{profile.id} {name} is a {found.basis} value, not service evidence")
            for source in found.sources:
                self.artifact(source, f"{profile.id} {name}")
        names = [row["property"] for row in rows if row["status"] == "resolved"]
        if names == ["density"]:
            self.refuse("material.density_only", f"{profile.id} resolves only a density: density is never stiffness or "
                                                 "strength, and no property is derived from it")
        valid = {"E": lambda v: v > 0, "nu": lambda v: 0 < v < 0.5, "density": lambda v: v > 0, "yield": lambda v: v > 0}
        for key, value in values.items():
            if not valid[key](value):
                self.refuse("material.property", f"{profile.id} {PROPERTIES[key][0]} {value:g} is outside the range a "
                                                 "linear elastic material card accepts")
        return rows, values

    def artifact(self, source: Any, what: str) -> None:
        if not source.artifact:
            return
        path = self.record_dir / source.artifact
        if not path.is_file():
            self.gaps.append(f"{what}: source artifact {source.artifact} is not in the version's record snapshot")
            return
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if source.sha256 and digest != source.sha256:
            self.refuse("material.source_artifact", f"{what}: {source.artifact} does not match its recorded sha256")
        self.dependencies[f"source_{digest[:16]}"] = digest

    def service_conditions(self, conditions: Mapping[str, Any]) -> None:
        service = self.record.use_case.facts.get("environment.temperature")
        analysed = conditions["temperature"]
        if service is None or not service.known or not isinstance(service.value, self.mi.Quantity):
            self.gaps.append("the use case's service temperature is unknown")
        elif not analysed.contains(service.value):
            self.gaps.append(f"the analysed temperature {analysed} does not cover the service temperature {service.value}")

    def criteria(self) -> dict[str, dict[str, Any]]:
        """The sourced acceptance criteria the case names, from requirements.yaml; never a bare number."""
        named = self.case.get("acceptance") or {}
        found = {}
        for check, prop, operator, units in (("strength", "yield_safety_factor", ">=", "dimensionless"),
                                             ("stiffness", "max_displacement", "<=", "mm")):
            ref = named.get(check)
            if ref is None:
                continue
            requirement = self.record.requirements.get(ref)
            if requirement is None:
                self.refuse("analysis.criterion", f"acceptance.{check} names {ref}, which is not in requirements.yaml")
                continue
            matches = [c for c in requirement.acceptance if c.property == prop]
            if len(matches) != 1 or matches[0].operator != operator or matches[0].units is None:
                self.refuse("analysis.criterion", f"acceptance.{check} names {ref}, which needs exactly one sourced "
                                                  f"acceptance criterion {prop} {operator} <value> with units")
                continue
            criterion = matches[0]
            try:
                value = self.mi.convert(float(criterion.value), criterion.units, units)
            except (self.mi.IntentError, TypeError, ValueError) as exc:
                self.refuse("analysis.criterion", f"{ref} {prop}: {exc}")
                continue
            found[check] = {"requirement": ref, "property": prop, "operator": operator, "value": value, "units": units,
                            "source": _plain(criterion.source)}
        if "strength" in found:
            required = self.case.get("required_safety_factor")
            if required is not None and abs(required - found["strength"]["value"]) > 1e-12:
                self.refuse("analysis.criterion", f"required_safety_factor {required:g} contradicts "
                                                  f"{found['strength']['requirement']}'s {found['strength']['value']:g}")
            self.case["required_safety_factor"] = found["strength"]["value"]
        return found

    def load_coverage(self, part: Any) -> list[str]:
        """Why the case's loads do not cover the part's service loads (empty when they do).

        The case's forces for a service load are added as vectors and compared with the service load's magnitude
        along its stated direction, so a reversed, rotated or cancelling force never counts as covered."""
        named = [load.get("load") for load in self.case["loads"] if load.get("load")]
        loads = self.record.use_case.loads
        known = {} if self.mi.is_fact(loads) else {load.id: load for load in loads}
        for ref in named:
            if ref not in known:
                self.refuse("analysis.load", f"a case load names {ref!r}, which is not a load of the use case")
        service = part.intent.service
        if self.mi.is_fact(service):
            return [f"{part.pn}'s service is {service.status}: the loads it must carry are unknown"]
        wanted = list(service.get("loads") or ())
        if not wanted or self.mi.is_fact(loads):
            return [f"{part.pn}'s service loads are unknown"]
        return [gap for ref in wanted for gap in self.load_gaps_of(ref, known.get(ref))]

    def load_gaps_of(self, ref: str, load: Any) -> list[str]:
        """Why the case's forces for one service load fall short of it (empty when they reach it)."""
        magnitude = load.magnitude if load is not None else None
        if magnitude is None or not magnitude.known or not isinstance(magnitude.value, self.mi.Quantity) \
                or magnitude.value.dimension != "force":
            return [f"service load {ref}'s force magnitude is unknown"]
        peak = magnitude.value.to("N").bounds[1]
        applied = _sum_vectors(item["force"] for item in self.case["loads"] if item.get("load") == ref)
        direction = _unit(load.direction)
        if direction is None:
            gaps = [f"service load {ref}'s direction is not a valid unit vector, so the direction of the case's "
                    "forces cannot be checked against it"]
            if _norm(applied) < peak * (1 - 1e-9):
                gaps.append(f"the case's forces for service load {ref} add up to {_norm(applied):g} N, which reaches "
                            f"{peak:g} N")
            return gaps
        broken = self.load_record_gaps(ref)
        if broken:
            return [f"service load {ref}'s direction frame cannot be confirmed: its record entry fails cad-record's "
                    f"checks ({'; '.join(broken)}), so the case's forces cannot be checked against it"]
        direction, unconfirmed = _Frames(self.record, self.documents).into(
            direction, load.application, self.analysed, posed=bool(self.case.get("pose")))
        if direction is None:
            return [f"service load {ref}'s direction frame cannot be confirmed: {unconfirmed}, so the case's forces "
                    "cannot be checked against it"]
        along = _dot(applied, direction)
        across = _norm(tuple(a - along * d for a, d in zip(applied, direction)))
        if along < peak * (1 - 1e-9):
            return [f"the case applies {max(along, 0.0):g} N along service load {ref}'s direction "
                    f"{_text(direction)}, which reaches {peak:g} N (its forces add up to {_text(applied)} N)"]
        if across > LOAD_CROSS_TOLERANCE * peak:
            return [f"the case's forces for service load {ref} point {across:g} N across its direction "
                    f"{_text(direction)}; more than {LOAD_CROSS_TOLERANCE:.0%} of the {peak:g} N it reaches"]
        return []

    def load_record_gaps(self, ref: str) -> list[str]:
        """The record gaps on service load `ref`'s entries in product.yaml: those under an entry with its id, and
        those on a node that holds them (a schema failure inside the loads array is reported at use_case/loads)."""
        product = self.documents.get("product.yaml")
        use_case = product.get("use_case") if isinstance(product, Mapping) else None
        loads = use_case.get("loads") if isinstance(use_case, Mapping) else None
        entries = [f"use_case/loads/{i}" for i, item in enumerate(loads if isinstance(loads, list) else ())
                   if isinstance(item, Mapping) and item.get("id") == ref]
        return [_record_gap(p) for p in self.checked if p.path == "hw/product.yaml"
                and any(_on_path(p.pointer, entry) for entry in entries)]

    def convergence(self, profile: Any, profile_sha: str) -> dict[str, Any]:
        stated = self.case.get("mesh_convergence")
        if self.case.get("_mode") == "stress":  # fea_static refuses mesh_convergence on a stress case
            return {"status": "not_demonstrated", "method": STUDY_METHOD, "reason": STUDY_PENDING,
                    "problems": [STUDY_PENDING]}
        if stated is None:
            reason = ("one mesh: refine mesh_size until peak stress and displacement settle, then record the study "
                      "in an approved decision named by mesh_convergence.decision")
            return {"status": "not_demonstrated", "reason": reason, "problems": [reason]}
        ref = stated["decision"]
        record = self.decision(ref, f"decision_{ref.lower().replace('-', '_')}", "analysis.convergence",
                               "mesh_convergence")
        if record is None:
            return {"status": "not_demonstrated", "decision": ref, "problems": [f"{ref} is missing or not a valid record"]}
        path = self.hw / "decisions" / record.file_name
        step = self.case.get("_step")
        geometry = hashlib.sha256(Path(step).read_bytes()).hexdigest() if step and Path(step).is_file() else None
        study, problems = read_study(path.read_text(encoding="utf-8"), ref, record.status)
        if study is None:
            return {"status": "not_demonstrated", "decision": ref, "problems": problems, "reason": "; ".join(problems)}
        problems += study_problems(study, geometry=geometry, profile_id=profile.id, profile_sha=profile_sha,
                                   supports=self.case["fixed_faces"], loads=self.case["loads"],
                                   mesh_size=self.case["mesh_size"])
        if problems:
            return {"status": "not_demonstrated", "decision": ref, "problems": problems, "reason": "; ".join(problems)}
        return {"status": "demonstrated", "decision": ref,
                "study": {"sizes_mm": [row["size_mm"] for row in study["refinements"]],
                          "relative_change_max": study["criterion"]["relative_change_max"]}}


# --- loads as vectors ------------------------------------------------------------------------------------------


def _sum_vectors(vectors: Any) -> tuple[float, float, float]:
    total = [0.0, 0.0, 0.0]
    for vector in vectors:
        for axis in range(3):
            total[axis] += float(vector[axis])
    return (total[0], total[1], total[2])


def _norm(vector: tuple[float, ...]) -> float:
    return math.sqrt(sum(component * component for component in vector))


def _dot(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _text(vector: tuple[float, ...]) -> str:
    return "[" + ", ".join(f"{component:.6g}" for component in vector) + "]"


def _unit(direction: Any) -> tuple[float, float, float] | None:
    """A working copy of the load's direction, scaled to length 1, or None when it is not three finite numbers of
    norm 1 within the record's 1e-6. The record's own direction is left as stated and reports an invalid one; this
    refuses it again. The scaling matters because the record accepts a norm of 1 ± 1e-6 and the projections assume 1."""
    if not isinstance(direction, (tuple, list)) or len(direction) != 3:
        return None
    if any(isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) for c in direction):
        return None
    length = _norm(tuple(direction))
    if abs(length - 1.0) > 1e-6:
        return None
    return (direction[0] / length, direction[1] / length, direction[2] / length)


def _record_gap(problem: Any) -> str:
    """A cad-record problem as `hw/<rel>#<pointer>: <message>`, or `hw/<rel>: <message>` for the whole document."""
    if not problem.pointer:
        return f"{problem.path}: {problem.message}"
    return f"{problem.path}#{problem.pointer}: {problem.message.removeprefix(problem.pointer + ': ')}"


def _on_path(pointer: str, node: str) -> bool:
    """Whether a problem at JSON pointer `pointer` lies at `node`, under it or on a node that holds it."""
    return pointer in ("", node) or node.startswith(pointer + "/") or pointer.startswith(node + "/")


class _Frames:
    """Model frames of the record's parts, to state a load's direction in the analysed part's model frame.

    `direction` is in the model frame of the part its `application` names, or in the assembly frame when that names
    no part or is omitted (manufacturing.schema.json). A `part_local` part is placed by its occurrence's rest
    placement; an `assembly_zero` part's model frame is the assembly frame at the zero pose. Rotations are cad-motion's
    `Rigid.from_placement` over cad-record's `assembly_data` inventory, with the translation dropped for a vector."""

    def __init__(self, record: Any, documents: Mapping[str, Any]) -> None:
        self.record = record
        self.assembly: Any = None
        self.pose: Any = None
        self.inventory: Any = None
        self.broken = ""
        self.unavailable = ""
        try:
            self.assembly = _shared("cad_record_assembly_data", ASSEMBLY_DATA_PATH)
            self.pose = _shared("cad_motion_pose", POSE_PATH)
        except Exception as error:   # any failure to load sibling code leaves the frames unknown, never the assessment aborted
            self.unavailable = f"a frame dependency is unavailable ({type(error).__name__}: {error})"
            return
        assembly = documents.get("assembly.yaml")
        frames = {pn: part.raw.get("model_frame", self.assembly.DEFAULT_FRAME) for pn, part in record.parts.items()}
        try:
            self.inventory = self.assembly.normalize(assembly if isinstance(assembly, Mapping) else {}, frames)
        except self.assembly.InventoryError as error:
            self.broken = str(error)

    def into(self, direction: tuple[float, float, float], application: Mapping[str, Any] | None,
             analysed: Mapping[str, Any], *, posed: bool) -> tuple[tuple[float, float, float] | None, str]:
        """`direction` in the analysed part's model frame, or None and why the frames are not confirmed.

        An omitted `application` is the assembly frame. A present one has passed cad-record's subject schema and
        reference checks: a load whose entry fails them never reaches here. A direction stated against the analysed
        part is already in its frame. Any other frame is reached through the assembly at the rest placements, which a
        posed case has left."""
        if application is not None and self.same_part(application, analysed):
            return direction, ""
        if self.unavailable:
            return None, self.unavailable
        if posed:
            return None, "the case's pose moves the parts from their rest placements"
        source, why = self.to_assembly(application or {})
        target, why_target = self.to_assembly(analysed)
        if source is None or target is None:
            return None, why or why_target
        moved = target.inverse().apply(source.apply(direction))
        return (moved[0], moved[1], moved[2]), ""

    def same_part(self, application: Mapping[str, Any], analysed: Mapping[str, Any]) -> bool:
        """Whether `application` names the analysed part's own model frame: its part, with no occurrence named, the
        analysed occurrence, or (for a case that names the part) the one occurrence the part has."""
        pn = application.get("PN")
        if pn is None or pn != analysed.get("PN"):
            return False
        named = application.get("occurrence")
        if named is None or named == analysed.get("occurrence"):
            return True
        if analysed.get("occurrence") is not None or self.inventory is None:
            return False
        try:
            return bool(self.inventory.resolve(pn).ref == named)
        except self.assembly.UnresolvedReference:
            return False

    def to_assembly(self, subject: Mapping[str, Any]) -> tuple[Any, str]:
        """The rotation from the model frame `subject` names into the assembly frame, or None and why not."""
        pn = subject.get("PN")
        identity = self.pose.Rigid.identity()
        if pn is None:
            return identity, ""
        part = self.record.parts.get(pn)
        if part is None:
            return None, f"{pn} is not a part of the record"
        zero = part.raw.get("model_frame", self.assembly.DEFAULT_FRAME) == self.assembly.DEFAULT_FRAME
        if zero and subject.get("occurrence") is None:
            return identity, ""
        if self.inventory is None:
            return None, f"assembly.yaml does not normalize ({self.broken})"
        try:
            occurrence = self.inventory.endpoint(pn, subject.get("occurrence"))
        except self.assembly.UnresolvedReference as error:
            return None, str(error)
        if zero:
            return identity, ""
        placed = self.pose.Rigid.from_placement(occurrence.placement)
        return self.pose.Rigid(placed.rot, identity.trans), ""


def _selector_key(selector: Any) -> str:
    """A selector in a form two equal selectors share: sorted indices, or a plane whose numbers are compared by
    value (0 and 0.0, 1e-06 and 0.000001 select the same plane)."""
    if isinstance(selector, Mapping) and isinstance(selector.get("indices"), list):
        try:
            return json.dumps({"indices": sorted(selector["indices"])}, sort_keys=True)
        except TypeError:
            pass
    plane = selector.get("plane") if isinstance(selector, Mapping) else None
    if isinstance(plane, Mapping):
        numbers = {key: (float(value) + 0.0 if isinstance(value, (int, float)) and not isinstance(value, bool) else value)
                   for key, value in plane.items()}   # + 0.0 turns -0.0 into 0.0
        return json.dumps({"plane": numbers}, sort_keys=True, default=str)
    return json.dumps(selector, sort_keys=True, default=str)


def _regions(loads: Any) -> dict[str, tuple[float, float, float]] | None:
    """The force on each application region (faces or plane), the case's loads on one region added up; None
    when a load has no usable selector or force."""
    sums: dict[str, list[tuple[float, ...]]] = {}
    for load in loads if isinstance(loads, list) else ():
        force = load.get("force") if isinstance(load, Mapping) else None
        if not isinstance(force, list) or len(force) != 3 or any(
                not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in force):
            return None
        sums.setdefault(_selector_key(load.get("selector")), []).append(tuple(float(v) for v in force))
    return {key: _sum_vectors(vectors) for key, vectors in sums.items()} or None


# --- the mesh convergence study --------------------------------------------------------------------------------


def read_study(text: str, ref: str, status: str) -> tuple[dict[str, Any] | None, list[str]]:
    """The study block of a convergence decision, or why the decision proves nothing: its MADR `status` is not
    `accepted` or it holds no readable study."""
    problems = []
    if status != APPROVED_STATUS:
        problems.append(f"{ref} is not approved: its status is {status}, not {APPROVED_STATUS}")
    block = STUDY_BLOCK.search(text)
    if block is None:
        problems.append(f"{ref} holds no ```mesh-convergence block with the refinement study")
        return None, problems
    try:
        study = yaml.safe_load(block.group(1))
    except yaml.YAMLError as exc:
        problems.append(f"{ref}'s mesh-convergence block is not valid YAML: {exc}")
        return None, problems
    if not isinstance(study, dict) or not study:
        problems.append(f"{ref}'s mesh-convergence block is empty")
        return None, problems
    return (study if not problems else None), problems


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def study_problems(study: Mapping[str, Any], *, geometry: str | None, profile_id: str, profile_sha: str, supports: Any,
                   loads: Any, mesh_size: float) -> list[str]:
    """Why a study does not demonstrate convergence of this case (empty when it does): it must be bound to this
    geometry, material, supports, forces per region and mesh, and its refinements must meet its own criterion."""
    problems: list[str] = []
    if geometry is None:
        problems.append("the case's geometry file cannot be read to bind the study to it")
    elif study.get("geometry_sha256") != geometry:
        problems.append("the study is not bound to this geometry: geometry_sha256 is missing or differs")
    material = study.get("material")
    if not isinstance(material, Mapping) or material.get("profile") != profile_id or material.get("sha256") != profile_sha:
        problems.append(f"the study is not bound to this material: material needs profile {profile_id} and its sha256")
    if "supports" not in study or _selector_key(study["supports"]) != _selector_key(supports):
        problems.append("the study is not bound to these supports: supports is missing or selects other faces")
    problems += _load_problems(study.get("loads"), loads)
    stated = study.get("mesh")
    mesh: Mapping[str, Any] = stated if isinstance(stated, Mapping) else {}
    size = _number(mesh.get("size_mm"))
    if size is None or abs(size - mesh_size) > 1e-9 * max(abs(mesh_size), 1.0) or mesh.get("element_order") != "second":
        problems.append(f"the study is not bound to this mesh: mesh needs size_mm {mesh_size:g} and element_order second")
    criterion = study.get("criterion")
    tolerance = _number(criterion.get("relative_change_max")) if isinstance(criterion, Mapping) else None
    if tolerance is None or not 0 < tolerance <= CONVERGENCE_LIMIT:
        problems.append(f"criterion.relative_change_max must be a number above 0 and at most {CONVERGENCE_LIMIT:g}")
        tolerance = None
    problems += _refinement_problems(study.get("refinements"), mesh_size, tolerance)
    return problems


def _load_problems(study_loads: Any, case_loads: Any) -> list[str]:
    wanted, stated = _regions(case_loads), _regions(study_loads)
    if stated is None:
        return ["the study is not bound to these loads: loads needs a selector and a force vector for each application region"]
    if wanted is None or stated.keys() != wanted.keys():
        return ["the study's loads act on other application regions than the case's"]
    for key, force in wanted.items():
        difference = _norm(tuple(a - b for a, b in zip(stated[key], force)))
        if difference > LOAD_BINDING_TOLERANCE * max(_norm(force), 1e-9):
            return [f"the study's force on {key} is {_text(stated[key])} N, but the case applies {_text(force)} N "
                    f"(tolerance {LOAD_BINDING_TOLERANCE:g} of the force)"]
    return []


def _refinement_series(rows: list[Any]) -> tuple[list[float], dict[str, list[float]]] | str:
    """The sizes and the monitored series of the refinement rows, or why a row is unusable."""
    sizes: list[float] = []
    series: dict[str, list[float]] = {name: [] for name in MONITORED}
    for index, row in enumerate(rows):
        message = f"refinement {index + 1} needs a positive size_mm and positive {' and '.join(MONITORED)}"
        size = _number(row.get("size_mm")) if isinstance(row, Mapping) else None
        values = [_number(row.get(name)) if isinstance(row, Mapping) else None for name in MONITORED]
        if size is None or size <= 0 or any(value is None or value <= 0 for value in values):
            return message
        sizes.append(size)
        for name, value in zip(MONITORED, values):
            series[name].append(value)   # type: ignore[arg-type]
    return sizes, series


def _refinement_problems(rows: Any, mesh_size: float, tolerance: float | None) -> list[str]:
    if not isinstance(rows, list) or len(rows) < MIN_REFINEMENTS:
        return [f"the study needs at least {MIN_REFINEMENTS} refinement results, coarse to fine"]
    parsed = _refinement_series(rows)
    if isinstance(parsed, str):
        return [parsed]
    sizes, series = parsed
    if any(coarse <= fine for coarse, fine in zip(sizes, sizes[1:])):
        return ["the refinement sizes must strictly decrease from coarse to fine"]
    here = [i for i, size in enumerate(sizes) if abs(size - mesh_size) <= 1e-9 * max(mesh_size, 1.0)]
    if not here:
        return [f"the case's mesh size {mesh_size:g} mm is not one of the study's refinement sizes"]
    if tolerance is None:
        return []
    problems: list[str] = []
    for name, values in series.items():
        settled = abs(values[-1] - values[-2]) / values[-1]
        used = abs(values[-1] - values[here[0]]) / values[-1]
        if settled > tolerance:
            problems.append(f"{name} still moves {settled:.1%} between the two finest meshes (limit {tolerance:.1%})")
        elif used > tolerance:
            problems.append(f"{name} on the case's mesh differs {used:.1%} from the finest mesh (limit {tolerance:.1%})")
    return problems


def convergence_gaps(convergence: Mapping[str, Any]) -> list[str]:
    """Why a result is not service evidence on account of its mesh: empty only for a demonstrated study."""
    if convergence.get("status") == "demonstrated":
        return []
    problems = convergence.get("problems") or [convergence.get("reason") or "no convergence study"]
    return ["mesh convergence is not demonstrated: " + "; ".join(str(problem) for problem in problems)]


def study_convergence(found: Any) -> dict[str, Any]:
    """A stress result's mesh convergence, from its own two-mesh study (`stress_study`).

    Both settled outcomes meet every convergence criterion of fea_stress, the displacement's included, so either
    demonstrates it. An `unknown` study, or a result without a study mapping, does not."""
    if isinstance(found, Mapping) and found.get("outcome") in (fea_stress.SETTLED, fea_stress.SETTLED_AWAY_FROM_SUPPORT):
        return {"status": "demonstrated", "method": STUDY_METHOD,
                "study": {key: found.get(key) for key in ("outcome", "mesh_sizes_mm", "step_ratio", "delta_sigma",
                                                          "delta_displacement")},
                "relative_change_max": fea_stress.SETTLE_TOLERANCE}
    reason = (f"the two-mesh stress study is {found.get('outcome')}: {found.get('message')}" if isinstance(found, Mapping)
              else "the stress result carries no two-mesh stress study")
    return {"status": "not_demonstrated", "method": STUDY_METHOD, "reason": reason, "problems": [reason]}


_PROFILE_NAMES = frozenset(name for name, _units in PROPERTIES.values())


def _plain(source: Any) -> dict[str, Any]:
    return {key: value for key, value in vars(source).items() if value is not None}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --- after the solve -------------------------------------------------------------------------------------------

def report(result: dict[str, Any], assessment: Assessment, case: Mapping[str, Any]) -> dict[str, Any]:
    """Add the material, evidence, mesh and (record version 2) engineering packet to a completed result.

    A stress result's mesh convergence is its own two-mesh study, which only the solve can show."""
    mode = case.get("_mode", fea_modes.DEFAULT)
    if mode == "stress":
        assessment = replace(assessment, convergence=study_convergence(result.get("stress_study")))
    convergence = assessment.convergence or {"status": "not_demonstrated", "reason": "one mesh; no convergence study"}
    result["material"] = assessment.material
    result["mesh"] = {"size_mm": case["mesh_size"], "element_order": "second", "nodes": result.get("node_count"),
                      "elements": result.get("element_count"), "convergence": convergence}
    gaps = assessment.all_gaps()
    result["evidence"] = {
        "level": LEVEL if assessment.sourced else None,
        "exploratory": not assessment.sourced,
        "service_evidence": assessment.sourced and not gaps,
        "physical_validation": False,
        "gaps": gaps,
        "limits": list(LIMITS),
    }
    if assessment.binding is not None:
        result["binding"] = assessment.binding
    if assessment.sourced:
        result["engineering"] = packet(result, assessment, mode=mode)
    return result


def packet(result: Mapping[str, Any], a: Assessment, *, mode: str) -> dict[str, Any]:
    """The engineering.* check-result packet (manufacturing-check-result.schema.json) of one solve in `mode`."""
    if a.subject is None:
        raise ValueError("only a record version 2 case, which names its analysed part, has an engineering packet")
    subject = a.subject
    outcomes, findings = [], []
    for obligation in a.expected:
        outcome, finding = _outcome(obligation, result, a, subject, mode)
        outcomes.append(outcome)
        if finding is not None:
            findings.append(finding)
    states = {row["outcome"] for row in outcomes}
    verdict = ("fail" if "fail" in states else "unknown" if "unknown" in states else
               "pass" if "pass" in states else "not_applicable")
    return {"schema_version": 1, "record_version": 2, "producer": PRODUCER, "scope": [subject],
            "expected": a.expected, "outcomes": outcomes, "findings": findings, "dependencies": dict(a.dependencies),
            "complete": len(outcomes) == len(a.expected), "result": verdict}


_CHECKS = {
    "engineering.strength": ("strength", "safety_factor_yield", "dimensionless",
                             "the yield safety factor at the peak von Mises stress"),
    "engineering.stiffness": ("stiffness", "max_displacement_mm", "mm", "the peak displacement"),
}


def _outcome(obligation: Mapping[str, Any], result: Mapping[str, Any], a: Assessment,
             subject: Mapping[str, Any], mode: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    family = obligation["family"]
    gaps = list(a.evidence_gaps) + convergence_gaps(a.convergence)
    if a.record_gaps:
        return _unchecked_record(obligation, a, subject, mode, gaps)
    if family == "engineering.anisotropy":
        model = a.effective_isotropic
        if model is None:
            # Additive routes need an effective model or were refused, so this is an obligation without one.
            return _row(obligation, "unknown", a, subject,
                        ["the case records no effective isotropic model for the orientation-dependent material"],
                        None, None, "the orientation-dependent material model")
        reasons = list(a.evidence_gaps) + a.load_gaps
        if reasons:
            return _row(obligation, "unknown", a, subject, reasons, None, None,
                        f"effective isotropic model {model['decision']}")
        return _row(obligation, "pass", a, subject, [], None, None,
                    f"orientation-specific properties at {model['orientation']} under the effective isotropic model "
                    f"{model['decision']}; interlayer failure is outside a von Mises check")
    check, key, units, what = _CHECKS[family]
    criterion = a.criteria.get(check)
    gaps += a.load_gaps
    if check == "strength" and mode == "stress":
        return _stress_strength(obligation, result, a, subject, criterion, gaps)
    measured = result.get(key)
    if criterion is None:
        why = (FAST_STRENGTH if check == "strength" and mode == "fast" else
               f"no sourced acceptance criterion: name the requirement under acceptance.{check}")
        return _row(obligation, "unknown", a, subject, [why], measured, units, what)
    if not isinstance(measured, (int, float)) or isinstance(measured, bool) or not math.isfinite(measured):
        return _row(obligation, "unknown", a, subject, [f"the solve reported no {what}"], None, units, what, criterion)
    meets = measured >= criterion["value"] if criterion["operator"] == ">=" else measured <= criterion["value"]
    if not meets:
        # A coarser mesh under-reports peak stress, so a miss stands without a convergence study; its basis gaps stay visible.
        return _row(obligation, "fail", a, subject, gaps, measured, units, what, criterion)
    if check == "strength":
        gaps.append(FULL_STRENGTH)
    return _row(obligation, "pass" if not gaps else "unknown", a, subject, gaps, measured, units, what, criterion)


def _unchecked_record(obligation: Mapping[str, Any], a: Assessment, subject: Mapping[str, Any], mode: str,
                      gaps: list[str]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Unknown whatever the solve shows, before any pass or fail: a document the case reads fails cad-record's
    checks, so the inputs the solve stands on are not the record's."""
    reasons = [*a.record_gaps, *gaps, *a.load_gaps]
    if obligation["family"] not in _CHECKS:
        return _row(obligation, "unknown", a, subject, reasons, None, None, "the orientation-dependent material model")
    check, _key, units, what = _CHECKS[obligation["family"]]
    if check == "strength" and mode == "stress":
        what = STUDY_STRENGTH
    return _row(obligation, "unknown", a, subject, reasons, None, units, what, a.criteria.get(check))


def _stress_strength(obligation: Mapping[str, Any], result: Mapping[str, Any], a: Assessment,
                     subject: Mapping[str, Any], criterion: Mapping[str, Any] | None,
                     gaps: list[str]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """A stress case's strength: yield used / the study's settled stress, against the sourced criterion.

    Only a settled, strength-eligible study gives a figure. Any other study, or a yield the case does not resolve,
    leaves strength unknown and never fails it."""
    units = "dimensionless"
    if criterion is None:
        return _row(obligation, "unknown", a, subject, ["no sourced acceptance criterion: name the requirement under "
                                                        "acceptance.strength"], None, units, STUDY_STRENGTH)
    sigma, why = _study_sigma(result.get("stress_study"))
    used = _yield_used(a)
    factor = used / sigma if sigma is not None and used is not None else None
    if factor is None or not math.isfinite(factor):
        reason = why or ("the case's material resolves no positive finite yield strength" if used is None else
                         f"the yield safety factor {used:g} / {sigma:g} MPa is not a finite number")
        return _row(obligation, "unknown", a, subject, [reason, *gaps], None, units, STUDY_STRENGTH, criterion)
    if factor < criterion["value"]:
        return _row(obligation, "fail", a, subject, gaps, factor, units, STUDY_STRENGTH, criterion)
    return _row(obligation, "pass" if not gaps else "unknown", a, subject, gaps, factor, units, STUDY_STRENGTH, criterion)


def _study_sigma(found: Any) -> tuple[float | None, str | None]:
    """The stress (MPa) a two-mesh study stands behind as a strength value, or None and why it stands behind none.

    That is a `settled`, strength-eligible study's `sigma_mpa`, the larger kept peak of its two meshes. Its
    information-only figure never counts, and neither does anything outside the study."""
    if not isinstance(found, Mapping):
        return None, "the stress result carries no two-mesh stress study"
    outcome = found.get("outcome")
    if outcome == fea_stress.SETTLED_AWAY_FROM_SUPPORT:
        return None, ("the two-mesh stress study settled only on the exclusion boundary, where the stress rises toward "
                      "the support: its figure is information only, and linearisation at the support (#209) is the "
                      "route to a strength value")
    if outcome != fea_stress.SETTLED:
        return None, f"the two-mesh stress study is {outcome}: {found.get('message')}"
    sigma: Any = found.get("sigma_mpa")
    if found.get("strength_eligible") is not True or found.get("sigma_info_mpa") is not None or not _positive(sigma):
        return None, ("the two-mesh stress study states settled without a strength-eligible positive finite sigma_mpa "
                      "and no information-only figure")
    return float(sigma), None


def _yield_used(a: Assessment) -> float | None:
    """The yield strength (MPa) the solve used: an interval's lower bound. None when the case resolved none."""
    for row in a.material.get("properties") or ():
        if row.get("input") == "yield" and row.get("status") == "resolved":
            used = row.get("used")
            return float(used) if _positive(used) else None
    return None


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _row(obligation: Mapping[str, Any], outcome: str, a: Assessment, subject: Mapping[str, Any], reasons: list[str],
         measured: Any, units: str | None, what: str,
         criterion: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    family = obligation["family"]
    uncertainty = "; ".join(reasons) if reasons else (
        "linear static idealisation; peak values depend on the mesh within the demonstrated convergence")
    row: dict[str, Any] = {"obligation": obligation["id"], "outcome": outcome, "level": LEVEL, "method": METHOD,
                           "uncertainty": uncertainty,
                           "coverage": f"{what} of one static load case on {subject['PN']}; never fatigue, creep, wear, "
                                       "impact, buckling or life"}
    if criterion is not None:
        row["source"] = criterion["source"]
    if outcome == "pass":
        return row, None
    ident = family.replace(".", "_")
    row["findings"] = [ident]
    finding = {
        "id": ident, "code": f"fea.{family.split('.', 1)[1]}.{'margin' if outcome == 'fail' else 'unverified'}",
        "obligation": obligation["id"], "subject": subject, "severity": "critical" if outcome == "fail" else "major",
        "outcome": outcome,
        "measured": ({"value": measured, "units": units} if isinstance(measured, (int, float)) and units else
                     {"status": "unknown", "reason": uncertainty}),
        "criterion": (_criterion(criterion) if criterion is not None else
                      {"status": "unknown", "reason": "the case names no sourced acceptance criterion"}),
        "method": {"name": METHOD}, "uncertainty": uncertainty,
        "consequence": (f"{subject['PN']} does not meet {criterion['requirement']} under this load case"
                        if outcome == "fail" and criterion else
                        f"{subject['PN']}'s {family.split('.', 1)[1]} is not established by this analysis"),
        "remedy": ("change the geometry, the material grade or the load path, then rerun the analysis"
                   if outcome == "fail" else
                   "close the listed gaps (sourced criterion, service-grade profile values, covered service loads, "
                   "demonstrated mesh convergence), then rerun the analysis"),
        "recheck": {"families": [family], "earliest_stage": "analysis"},
    }
    if criterion is not None:
        finding["requirements"] = [criterion["requirement"]]
    return row, finding


def _criterion(criterion: Mapping[str, Any]) -> dict[str, Any]:
    return {key: criterion[key] for key in ("property", "operator", "value", "units", "source")}
