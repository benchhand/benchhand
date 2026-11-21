# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["jsonschema>=4.23"]
# ///
"""The gate on product.yaml `flow.profile`, the user's answers to the project-start questions: what a
missing, unasked or malformed profile blocks, and where the profile contradicts the rest of the record.

Every function is pure in the parsed record: the product mapping, the part records by PN and, for the
decision that the profile cites, `approval`, a callable that returns why a decision does not approve
(None for an approving one). Nothing here reads the work tree or knows the decision record format, so the
caller supplies the approval from cad-record's decision reader (validate.py `approval_problem`).

cad-flow flow_profile.py owns the questions, the record command and the check command, and imports this
module; the dependency never runs the other way. Load this file by path and register it in `sys.modules`
as `cad_record_flow_profile_gate`.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _load_module(name: str, path: Path) -> Any:
    """A sibling module loaded once by path and registered in sys.modules."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"{path.name} not found at {path}")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


schema_registry = _load_module("cad_record_schema_registry", Path(__file__).resolve().parent / "schema_registry.py")
# The schemas beside the registry in use: a caller that loaded it from its own skill copy reads its own.
SCHEMAS: Path = schema_registry.SCHEMAS

Approval = Callable[[str], "str | None"]

PROFILE_REF = "https://srobroek.com/cad/schemas/product.schema.json#/$defs/profile"
STAGES: tuple[str, ...] = tuple(schema_registry.schema(SCHEMAS, "product.schema.json")["$defs"]["stage"]["enum"])
# The profile and its decision are due once the INTENT gate has passed. A build is L1 LAYOUT work or later.
PROFILE_DUE = "specify"
BUILD_STAGE = "layout"
FDM = "processes/fdm/"
PIP_CLEARANCE = "processes/fdm/pip_clearance_mm/"
# The ISO/ASTM 52900 category of each flow.profile process (product.schema.json $defs/iso_52900_category);
# None for a subtractive or forming process.
ISO_52900_CATEGORY: dict[str, str | None] = {"fdm": "mex"}
# flow.profile fea, the shape agreed with package A (product.schema.json $defs/fea): these names only.
FEA, FEA_ENABLED, FEA_MODE = "fea", "enabled", "mode"
# Who settled a start question, recorded per question in flow.profile `asked`.
USER, ACCEPTED_DEFAULT = "user", "asked-accepted-default"
PROVENANCE = (USER, ACCEPTED_DEFAULT)
# The default of every flow decision, shown to the user at GROUND. Conservative: no check is turned off, nothing is
# committed to git and the user keeps control. product.schema.json carries the same values as `default`
# (a test keeps the two equal). fast_band_pct has none: cad-analysis measures its own band.
DEFAULTS: dict[str, Any] = {
    "human_approval": "all", "fea": {"enabled": True, "mode": "accurate"}, "stack_up": "auto", "critique": "full",
    "motion": "auto", "storage_results_in_git": False, "processes/fdm/orientation": "user",
    "processes/fdm/supports": "allowed", "processes/fdm/overhang_deg": "calibrated", "processes/fdm/bridge_mm": "calibrated",
    "processes/fdm/min_wall_mm": "calibrated", "processes/fdm/min_feature_mm": "calibrated",
    "processes/fdm/warp": "calibrated", "processes/fdm/cosmetic_faces": False, "processes/fdm/print_in_place": False,
    "processes/fdm/pip_clearance_mm/xy": "calibrated", "processes/fdm/pip_clearance_mm/z": "calibrated"}
# The fields the user is asked, in question order: every default but motion, which is stated and never asked.
QUESTION_FIELDS: tuple[str, ...] = tuple(field for field in DEFAULTS if field != "motion")
# Fields whose values are words: YAML 1.1 reads a bare on, off, yes or no as a boolean, which these never take.
# A two-state field (fea.enabled, cosmetic_faces, print_in_place, storage_results_in_git) is a boolean instead.
WORD_FIELDS = ("human_approval", "stack_up", "critique", "motion", "processes/fdm/orientation", "processes/fdm/supports",
               "processes/fdm/overhang_deg", "processes/fdm/bridge_mm", "processes/fdm/min_wall_mm",
               "processes/fdm/min_feature_mm", "processes/fdm/warp", "processes/fdm/pip_clearance_mm/xy",
               "processes/fdm/pip_clearance_mm/z")
ASK = ("ask the user the project-start questions (flow_profile.py questions), each with its default, and record the "
       "answers with flow_profile.py record --decision DR-###")
# Settings whose off or light turns a check off; safety_critical service and regulated rigor refuse them, and
# fea disabled. cosmetic_faces false declares no cosmetic face and turns no check off.
CHECK_SWITCHES = ("stack_up", "critique", "processes/fdm/supports", "processes/fdm/overhang_deg",
                  "processes/fdm/bridge_mm", "processes/fdm/min_wall_mm", "processes/fdm/min_feature_mm",
                  "processes/fdm/warp")


@dataclass(frozen=True)
class Problem:
    """What refuses a build, a validation or a stage advance: its flow/profile path, the fix and its code."""

    path: str
    message: str
    action: str
    code: str = "flow.profile"


@dataclass(frozen=True)
class Conflict:
    """A profile setting the rest of the record contradicts. `refuse` marks the safety rule, which no
    gate decision resolves; the caller grades the others by stage."""

    rule: str
    path: str
    message: str
    refuse: bool = False


# --- reading a parsed record ----------------------------------------------------------------------------------------

def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mappings(value: Any) -> list[Mapping[str, Any]]:
    """The mappings in `value` when it is a list; anything else is an empty list."""
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def profile_of(product: Any) -> Any:
    """product.yaml flow.profile as recorded, or None."""
    return _mapping(_mapping(product).get("flow")).get("profile")


def value_at(profile: Mapping[str, Any], field: str) -> Any:
    """The profile's value of `field` (a / path below flow.profile), None when absent."""
    value: Any = profile
    for key in field.split("/"):
        if not isinstance(value, Mapping) or key not in value:
            return None
        value = value[key]
    return value


def fea_setting(profile: Any) -> tuple[bool | None, str | None]:
    """(enabled, mode) of flow.profile fea. (None, None) when it is not a fea mapping with a boolean enabled:
    not assessed, which is neither off nor fast."""
    fea = _mapping(profile).get(FEA)
    if not isinstance(fea, Mapping) or not isinstance(fea.get(FEA_ENABLED), bool):
        return None, None
    mode = fea.get(FEA_MODE) if fea[FEA_ENABLED] else None
    return fea[FEA_ENABLED], mode if isinstance(mode, str) else None


def stage_index(stage: Any) -> int:
    """The position of `stage` in the stage order; a missing or unknown stage counts as the last one."""
    return STAGES.index(stage) if stage in STAGES else len(STAGES) - 1


def _route(part: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return _mappings(_mapping(part.get("intent")).get("route"))


def _uses_fdm(part: Mapping[str, Any]) -> bool:
    return part.get("process") == "fdm" or any(op.get("kind") == "fdm" for op in _route(part))


def plans_fdm(product: Any, parts: Mapping[str, Any]) -> bool:
    """Whether the product plans FDM: at INTENT (manufacturing.processes), as a part's process, or as an
    operation of a part's route. `parts` are the parsed part records by PN."""
    processes = _mapping(_mapping(product).get("manufacturing")).get("processes")
    if isinstance(processes, list) and "fdm" in processes:
        return True
    return any(_uses_fdm(_mapping(part)) for part in parts.values())


# --- the gate -------------------------------------------------------------------------------------------------------

def problems(product: Any, parts: Mapping[str, Any], approval: Approval, stage: str | None = None) -> list[Problem]:
    """What blocks at `stage` (default: product.yaml flow.stage): from SPECIFY on, a missing profile, a
    profile whose cited decision does not approve, and every applicable start question with no `asked` answer.
    The FDM questions apply while the product plans FDM, the PIP clearance while print-in-place is not declined."""
    at = stage_index(stage if stage is not None else _mapping(_mapping(product).get("flow")).get("stage"))
    if at < STAGES.index(PROFILE_DUE):
        return []
    profile = profile_of(product)
    if not isinstance(profile, Mapping):
        return [Problem("flow/profile", "flow.profile is missing: the project-start questions are not recorded", ASK)]
    found = []
    decision = profile.get("decision")
    if not isinstance(decision, str):
        found.append(Problem("flow/profile/decision", "flow.profile cites no decision record",
                             "record the profile again with flow_profile.py record --decision DR-###"))
    elif (why := approval(decision)) is not None:
        found.append(Problem("flow/profile/decision", f"flow.profile cites {decision}, which {why}",
                             "only the user's explicit approval records the answers: record them again with a new DR-###"))
    return found + asked_problems(profile, fdm=plans_fdm(product, parts))


def asked_problems(profile: Mapping[str, Any], *, fdm: bool) -> list[Problem]:
    """Every applicable start question needs an `asked` answer; one marked as the accepted default must hold it."""
    asked = _mapping(profile.get("asked"))
    pip = value_at(profile, "processes/fdm/print_in_place") is not False
    found = []
    for field in QUESTION_FIELDS:
        if (field.startswith(FDM) and not fdm) or (field.startswith(PIP_CLEARANCE) and not pip):
            continue
        how = asked.get(field)
        path = f"flow/profile/asked/{field}"
        if how not in PROVENANCE:
            found.append(Problem(path, f"{field.replace('/', '.')} was not asked",
                                 "ask the user that project-start question, showing its default, and record the answers again"))
        elif how == ACCEPTED_DEFAULT and value_at(profile, field) != DEFAULTS[field]:
            found.append(Problem(path, f"{field.replace('/', '.')} is recorded as the accepted default but is "
                                 f"{value_at(profile, field)!r}, not {DEFAULTS[field]!r}",
                                 "record it as asked: user, or record the answers again"))
    return found


def unquoted(product: Any) -> list[Problem]:
    """flow.profile_unquoted: a word field holding a boolean, because YAML 1.1 read a bare on, off, yes or no.
    Reported at every stage; the word is never guessed back from the boolean."""
    profile = profile_of(product)
    if not isinstance(profile, Mapping):
        return []
    found = []
    for field in WORD_FIELDS:
        value = value_at(profile, field)
        if isinstance(value, bool):
            word = "on" if value else "off"
            found.append(Problem(f"flow/profile/{field}", f'write "{word}" in quotes; YAML reads bare {word} as '
                                 f"{str(value).lower()}", f'quote the answer: {field.split("/")[-1]}: "{word}"',
                                 code="flow.profile_unquoted"))
    return found


def shape_problems(profile: Any, skip: Iterable[str] = ()) -> list[Problem]:
    """The schema's findings on a recorded profile, at their flow/profile paths, except those at `skip`."""
    validator = schema_registry.ref_validator(SCHEMAS, PROFILE_REF)
    found = []
    for error in sorted(validator.iter_errors(profile), key=lambda item: list(item.absolute_path)):
        path = "/".join(["flow/profile", *(str(part) for part in error.absolute_path)])
        if path not in skip:
            found.append(Problem(path, error.message, "correct the value, or record the answers again"))
    return found


def gate(product: Any, parts: Mapping[str, Any], approval: Approval, stage: str | None = None, *,
         build: bool = False) -> tuple[str, list[Problem]]:
    """(the stage checked, what blocks at it): unquoted(), problems() and, once the profile is due, its
    shape. `stage` defaults to product.yaml flow.stage; a build checks at L1 LAYOUT at the earliest."""
    recorded = _mapping(_mapping(product).get("flow")).get("stage")
    checked = STAGES[stage_index(stage if stage is not None else recorded)]
    if build:
        checked = STAGES[max(stage_index(checked), STAGES.index(BUILD_STAGE))]
    words = unquoted(product)
    found = problems(product, parts, approval, checked)
    profile = profile_of(product)
    if stage_index(checked) >= STAGES.index(PROFILE_DUE) and isinstance(profile, Mapping):
        found = shape_problems(profile, {item.path for item in words}) + found
    return checked, words + found


def refusal(found: Iterable[Problem]) -> str:
    """One line per problem, for a refusing command's stderr."""
    return "\n".join(f"{item.path}: {item.message}; {item.action}" for item in found)


# --- conflicts ------------------------------------------------------------------------------------------------------

def _known_true(fact: Any) -> bool:
    return isinstance(fact, Mapping) and fact.get("status") == "known" and fact.get("value") is True


def _plans_supports(part: Mapping[str, Any]) -> bool:
    return any(op.get("kind") == "fdm" and _mapping(op.get("supports")).get("strategy") not in (None, "none")
               for op in _route(part))


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _pip_conflicts(profile: Mapping[str, Any], interfaces: list[Mapping[str, Any]]) -> list[Conflict]:
    joints = [item for item in interfaces if _mapping(item.get("functional")).get("print_in_place")]
    if not joints:
        return []
    ids = ", ".join(str(item.get("id")) for item in joints)
    found = []
    if value_at(profile, "processes/fdm/print_in_place") is False or value_at(profile, "processes/fdm/overhang_deg") == "off":
        found.append(Conflict("pip_not_offered", "flow/profile/processes/fdm",
                              f"{ids} print in place, but the profile declines print-in-place or turns the overhang check off"))
    # The record does not say which way each gap faces, so the larger axis clearance governs: the
    # conservative choice, since a gap at least that wide separates in either direction.
    known = [value for axis in ("xy", "z") if _number(value := value_at(profile, f"{PIP_CLEARANCE}{axis}"))]
    tight = [str(item.get("id")) for item in joints
             if known and _number(item.get("min_clearance_mm")) and item["min_clearance_mm"] < max(known)]
    if tight:
        found.append(Conflict("pip_play_too_tight", "flow/profile/processes/fdm/pip_clearance_mm",
                              f"{', '.join(tight)} require(s) a min_clearance_mm below the print-in-place clearance "
                              f"{max(known)} mm, so the printed gap would fuse"))
    return found


def conflicts(product: Any, requirements: Any, interfaces: Any, parts: Mapping[str, Any],
              fea_cases: Iterable[str]) -> list[Conflict]:
    """Where the profile contradicts the record: requirements.yaml, interfaces.yaml, the part records by PN and
    the authored FEA case ids. A list the record holds as anything else counts as empty; the schema reports it."""
    profile = profile_of(product)
    if not isinstance(profile, Mapping):
        return []
    items = _mappings(_mapping(interfaces).get("interfaces"))
    found = []
    enabled, _mode = fea_setting(profile)
    if enabled is False:
        needs = [str(r.get("id")) for r in _mappings(_mapping(requirements).get("requirements"))
                 if _mapping(r.get("verification")).get("step") == "fea"]
        needs += [f"analysis case {case}" for case in fea_cases]
        if needs:
            found.append(Conflict("fea_off", "flow/profile/fea/enabled", f"fea is off, but {', '.join(needs)} need(s) FEA"))
    critical = [str(item.get("id")) for item in items if _known_true(_mapping(item.get("functional")).get("critical"))]
    if value_at(profile, "stack_up") == "off" and critical:
        found.append(Conflict("stack_up_off", "flow/profile/stack_up",
                              f"stack_up is off, but {', '.join(critical)} is a critical interface"))
    if enabled is True and value_at(profile, "critique") in ("light", "off"):
        found.append(Conflict("fea_without_critique", "flow/profile/critique",
                              f"fea is on, but critique is {value_at(profile, 'critique')}: an analysed design gets the full critique"))
    supported = sorted(pn for pn, part in parts.items() if _plans_supports(_mapping(part)))
    if value_at(profile, "processes/fdm/supports") == "free" and supported:
        found.append(Conflict("supports_free", "flow/profile/processes/fdm/supports",
                              f"parts must print support-free, but {', '.join(supported)} plan(s) supports: "
                              "decide supports or a redesign at the PARTS gate"))
    found += _pip_conflicts(profile, items)
    service = _mapping(_mapping(_mapping(product).get("use_case")).get("service_class"))
    safety = service.get("status") == "known" and service.get("value") == "safety_critical"
    if safety or _mapping(product).get("rigor") == "regulated":
        reason = "safety_critical service" if safety else "regulated rigor"
        if enabled is False:
            found.append(Conflict("safety_off", "flow/profile/fea/enabled", f"{reason} refuses fea off", refuse=True))
        found += [Conflict("safety_off", f"flow/profile/{field}",
                           f"{reason} refuses {field.replace('/', '.')}: {value_at(profile, field)}", refuse=True)
                  for field in CHECK_SWITCHES if value_at(profile, field) in ("off", "light")]
    return found
