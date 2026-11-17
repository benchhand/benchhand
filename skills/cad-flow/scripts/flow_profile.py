#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["jsonschema>=4.23", "pyyaml>=6.0"]
# ///
"""The flow profile: the project-start questions, the record of their answers, and the gate on it.

questions  prints the questions the lead relays to the user verbatim at GROUND, and the motion sweep,
           which is stated to the user and never asked.
record     writes the answers to hw/product.yaml flow.profile, citing --decision, and writes that
           decision as an approved INTENT gate decision. Each answer is an option; an option left out
           records `not_asked`. product.yaml is edited as text, so its comments and layout survive: the
           edit must parse to exactly the intended data, and the file is replaced in one rename.
check      reports what refuses a build, a validation and a stage advance at --stage (default:
           product.yaml flow.stage): a missing profile or decision from SPECIFY on, a value the schema
           rejects, and a question still `not_asked` once its check falls due (DUE).

cad-model cad_model.py run, cad-record validate.py and cad-recheck stamp.py --verify load this file by
path and call gate() or problems(); validate.py also reports conflicts() as flow.conflict.

Exit status: 0 nothing blocks (questions, record: done), 1 check found a blocking problem, 2 usage
or a record that cannot be read or edited; nothing was written.
"""

# TODO(flow-profile PR 7): a plan command prints the stages, the L4 units and the decisions due at each gate.

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

SKILLS = Path(__file__).resolve().parents[2]
CAD_RECORD_SCRIPTS = SKILLS / "cad-record" / "scripts"


def _load_module(name: str, path: Path) -> Any:
    """A sibling skill's module loaded once by path and registered in sys.modules."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"{path.name} not found at {path}")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


yaml_loader = _load_module("cad_record_yaml_loader", CAD_RECORD_SCRIPTS / "yaml_loader.py")
schema_registry = _load_module("cad_record_schema_registry", CAD_RECORD_SCRIPTS / "schema_registry.py")
# The schemas beside the schema registry in use: a validate.py that loaded it from its own skill copy reads its own.
SCHEMAS: Path = schema_registry.SCHEMAS

PROFILE_REF = "https://srobroek.com/cad/schemas/product.schema.json#/$defs/profile"
_PRODUCT_DEFS = json.loads((SCHEMAS / "product.schema.json").read_text(encoding="utf-8"))["$defs"]
STAGES: tuple[str, ...] = tuple(_PRODUCT_DEFS["stage"]["enum"])
GATED_STAGES: tuple[str, ...] = tuple(_PRODUCT_DEFS["gated_stage"]["enum"])  # the values a decision's gate takes
NOT_ASKED = "not_asked"
DECISION = re.compile(r"DR-[0-9]{3}")
# The profile and its decision are due once the INTENT gate has passed. A build is L1 LAYOUT work or later.
PROFILE_DUE = "specify"
BUILD_STAGE = "layout"
# The stage at which the check that reads each field falls due; a field still not asked blocks from then on.
DUE: dict[str, str] = {
    "human_approval": "layout",
    "storage_results_in_git": "layout",
    "motion": "layout",
    "processes/fdm/print_in_place": "layout",
    "processes/fdm/orientation": "parts",
    "processes/fdm/supports": "parts",
    "processes/fdm/overhang_deg": "parts",
    "processes/fdm/bridge_mm": "parts",
    "processes/fdm/min_wall_mm": "parts",
    "processes/fdm/min_feature_mm": "parts",
    "processes/fdm/warp": "parts",
    "processes/fdm/cosmetic_faces": "parts",
    "stack_up": "detail",
    "processes/fdm/pip_clearance_mm/xy": "detail",
    "processes/fdm/pip_clearance_mm/z": "detail",
    "fea": "analysis",
    "critique": "critique",
}
FDM = "processes/fdm/"
PIP_CLEARANCE = "processes/fdm/pip_clearance_mm/"
# The ISO/ASTM 52900 category of each flow.profile process (product.schema.json $defs/iso_52900_category);
# None for a subtractive or forming process.
ISO_52900_CATEGORY: dict[str, str | None] = {"fdm": "mex"}

# (option, profile field, accepted words, whether a positive number is accepted too)
OPTIONS: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("--human-approval", "human_approval", ("all", "key"), False),
    ("--fea", "fea", ("on", "off"), False),
    ("--stack-up", "stack_up", ("auto", "on", "off"), False),
    ("--critique", "critique", ("full", "light", "off"), False),
    ("--results-in-git", "storage_results_in_git", ("yes", "no"), False),
    ("--orientation", "processes/fdm/orientation", ("user", "propose"), False),
    ("--supports", "processes/fdm/supports", ("allowed", "free", "off"), False),
    ("--overhang-deg", "processes/fdm/overhang_deg", ("calibrated", "off"), True),
    ("--bridge-mm", "processes/fdm/bridge_mm", ("calibrated", "off"), True),
    ("--min-wall-mm", "processes/fdm/min_wall_mm", ("calibrated", "off"), True),
    ("--min-feature-mm", "processes/fdm/min_feature_mm", ("calibrated", "off"), True),
    ("--warp", "processes/fdm/warp", ("calibrated", "off"), False),
    ("--cosmetic-faces", "processes/fdm/cosmetic_faces", ("on", "off"), False),
    ("--print-in-place", "processes/fdm/print_in_place", ("offer", "no"), False),
    ("--pip-clearance-xy", "processes/fdm/pip_clearance_mm/xy", ("calibrated",), True),
    ("--pip-clearance-z", "processes/fdm/pip_clearance_mm/z", ("calibrated",), True),
)
# Design section 3, verbatim, each with the options its answer records.
QUESTIONS: tuple[tuple[str, str], ...] = (
    ("Must any part carry a load where breaking or bending would matter? Should I simulate it?", "--fea"),
    ("Which fits must not jam or rattle with real, imperfect parts: only the critical ones, all of them, or none?",
     "--stack-up"),
    ("How much review before release: full, a quick re-check of retainers, mounts and walls, or none?", "--critique"),
    ("Which printer, nozzle and filament? What is the bed size?",
     "the capability profile in hw/manufacturing/capabilities.yaml, not flow.profile"),
    ("Are supports allowed, or must parts print support-free? Do you know your overhang, bridge, thinnest-wall and "
     "smallest-feature limits, or should I wait for a test print?",
     "--supports, --overhang-deg, --bridge-mm, --min-wall-mm, --min-feature-mm, --warp"),
    ("Will you pick the print orientation, or should I propose options? Which faces must stay clean?",
     "--orientation, --cosmetic-faces"),
    ("Would you like hinges or joints printed already assembled where that fits?",
     "--print-in-place, --pip-clearance-xy, --pip-clearance-z"),
    ("Should every stage gate wait for your approval, or only intent, variant, freeze and release?", "--human-approval"),
    ("Should generated results (STEP, GLB, renders) be committed to git?", "--results-in-git"),
)
MOTION_STATEMENT = ("Stated, not asked: once any joint moves, the motion sweep checks its full range for collisions "
                    "and the motion is rendered (motion: auto).")
ASK = ("ask the user the project-start questions (flow_profile.py questions) and record the answers with "
       "flow_profile.py record --decision DR-###")
# Settings whose off or light turns a check off; safety_critical service and regulated rigor refuse them.
# cosmetic_faces off declares no cosmetic face and turns no check off.
CHECK_SWITCHES = ("fea", "stack_up", "critique", "processes/fdm/supports", "processes/fdm/overhang_deg",
                  "processes/fdm/bridge_mm", "processes/fdm/min_wall_mm", "processes/fdm/min_feature_mm",
                  "processes/fdm/warp")


class ProfileError(Exception):
    """A record this script cannot read or edit; nothing was written."""


@dataclass(frozen=True)
class Problem:
    """What refuses a build, a validation or a stage advance: its flow/profile path and the fix."""

    path: str
    message: str
    action: str


@dataclass(frozen=True)
class Conflict:
    """A profile setting the rest of the record contradicts. `refuse` marks the safety rule, which no
    gate decision resolves; validate.py grades the others by stage."""

    rule: str
    path: str
    message: str
    refuse: bool = False


# --- reading ------------------------------------------------------------------------------------------------------

def read_product(hw: Path) -> Any:
    path = Path(hw) / "product.yaml"
    try:
        return yaml_loader.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ProfileError(f"cannot read {path}: {error}") from None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def profile_of(product: Any) -> Any:
    """product.yaml flow.profile as recorded, or None."""
    return _mapping(_mapping(product).get("flow")).get("profile")


def value_at(profile: Mapping[str, Any], field: str) -> Any:
    """The profile's value of `field` (a / path below flow.profile); an absent value counts as not asked."""
    value: Any = profile
    for key in field.split("/"):
        if not isinstance(value, Mapping) or key not in value:
            return NOT_ASKED
        value = value[key]
    return value


def stage_index(stage: Any) -> int:
    """The position of `stage` in the stage order; a missing or unknown stage counts as the last one."""
    return STAGES.index(stage) if stage in STAGES else len(STAGES) - 1


def plans_fdm(hw: Path, product: Any) -> bool:
    """Whether the product plans FDM: at INTENT (manufacturing.processes), as a part's process, or as an
    operation of a part's route. A part record that does not parse counts as no plan; validate.py reports it."""
    if "fdm" in (_mapping(_mapping(product).get("manufacturing")).get("processes") or ()):
        return True
    for path in sorted((Path(hw) / "parts").glob("*/part.yaml")):
        try:
            part = yaml_loader.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if _uses_fdm(_mapping(part)):
            return True
    return False


def _route(part: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    route = _mapping(part.get("intent")).get("route")
    return [op for op in route if isinstance(op, Mapping)] if isinstance(route, list) else []


def _uses_fdm(part: Mapping[str, Any]) -> bool:
    return part.get("process") == "fdm" or any(op.get("kind") == "fdm" for op in _route(part))


def fea_cases(hw: Path) -> list[str]:
    """The authored FEA cases: hw/analysis/cases/<ID>/case.yaml with fixed faces (cad-analysis fea_static.py)."""
    found = []
    for path in sorted((Path(hw) / "analysis" / "cases").glob("*/case.yaml")):
        try:
            case = yaml_loader.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if isinstance(case, Mapping) and "fixed_faces" in case:
            found.append(path.parent.name)
    return found


# --- the gate -----------------------------------------------------------------------------------------------------

def problems(hw: Path, product: Any, stage: str | None = None) -> list[Problem]:
    """What blocks at `stage` (default: product.yaml flow.stage): from SPECIFY on, a missing profile, a
    missing decision record, and every applicable field still not asked once DUE says its check is due.
    The FDM questions apply while the product plans FDM, the PIP clearance while print-in-place is not declined."""
    at = stage_index(stage if stage is not None else _mapping(_mapping(product).get("flow")).get("stage"))
    if at < STAGES.index(PROFILE_DUE):
        return []
    profile = profile_of(product)
    if not isinstance(profile, Mapping):
        return [Problem("flow/profile", "flow.profile is missing: the project-start questions are not recorded", ASK)]
    found = []
    decision = profile.get("decision")
    if not isinstance(decision, str) or not (Path(hw) / "decisions" / f"{decision}.md").is_file():
        found.append(Problem("flow/profile/decision", f"flow.profile cites no decision record ({decision or 'none'})",
                             "record the profile again with flow_profile.py record --decision DR-###"))
    elif not decision_approves(hw, decision):
        found.append(Problem("flow/profile/decision", f"flow.profile cites {decision}, which is not an approval",
                             "only the user's explicit approval records the answers: record them again with a new DR-###"))
    fdm = plans_fdm(hw, product)
    pip = value_at(profile, "processes/fdm/print_in_place") != "no"
    for field, due in DUE.items():
        applies = (fdm or not field.startswith(FDM)) and (pip or not field.startswith(PIP_CLEARANCE))
        if applies and STAGES.index(due) <= at and value_at(profile, field) == NOT_ASKED:
            found.append(Problem(f"flow/profile/{field}", f"{field.replace('/', '.')} was not asked, and its check is "
                                 f"due from {due}: not assessed (not asked)",
                                 "ask the user that project-start question and record the answers again"))
    return found


def shape_problems(profile: Any) -> list[Problem]:
    """The schema's findings on a recorded profile, at their flow/profile paths."""
    validator = schema_registry.ref_validator(SCHEMAS, PROFILE_REF)
    found = []
    for error in sorted(validator.iter_errors(profile), key=lambda item: list(item.absolute_path)):
        path = "/".join(["flow/profile", *(str(part) for part in error.absolute_path)])
        hint = ("; YAML reads a bare on, off, yes or no as a boolean, so quote the word" if isinstance(error.instance, bool)
                and not path.endswith("storage_results_in_git") else "")
        found.append(Problem(path, f"{error.message}{hint}", "correct the value, or record the answers again"))
    return found


def gate(hw: Path, stage: str | None = None, *, build: bool = False, product: Any = None) -> tuple[str, list[Problem]]:
    """(the stage checked, what blocks at it): problems() and, once the profile is due, its shape. `stage`
    defaults to product.yaml flow.stage; a build checks at L1 LAYOUT at the earliest. `product` defaults
    to hw/product.yaml. Raises ProfileError when that cannot be read."""
    product = read_product(hw) if product is None else product
    recorded = _mapping(_mapping(product).get("flow")).get("stage")
    checked = STAGES[stage_index(stage if stage is not None else recorded)]
    if build:
        checked = STAGES[max(stage_index(checked), STAGES.index(BUILD_STAGE))]
    found = problems(hw, product, checked)
    profile = profile_of(product)
    if stage_index(checked) >= STAGES.index(PROFILE_DUE) and isinstance(profile, Mapping):
        found = shape_problems(profile) + found
    return checked, found


def refusal(found: Iterable[Problem]) -> str:
    """One line per problem, for a refusing command's stderr."""
    return "\n".join(f"{item.path}: {item.message}; {item.action}" for item in found)


# --- conflicts ----------------------------------------------------------------------------------------------------

def _known_true(fact: Any) -> bool:
    return isinstance(fact, Mapping) and fact.get("status") == "known" and fact.get("value") is True


def _plans_supports(part: Mapping[str, Any]) -> bool:
    return any(op.get("kind") == "fdm" and _mapping(op.get("supports")).get("strategy") not in (None, "none")
               for op in _route(part))


def _pip_conflicts(profile: Mapping[str, Any], interfaces: list[Mapping[str, Any]]) -> list[Conflict]:
    joints = [item for item in interfaces if _mapping(item.get("functional")).get("print_in_place")]
    if not joints:
        return []
    ids = ", ".join(str(item.get("id")) for item in joints)
    found = []
    if value_at(profile, "processes/fdm/print_in_place") == "no" or value_at(profile, "processes/fdm/overhang_deg") == "off":
        found.append(Conflict("pip_not_offered", "flow/profile/processes/fdm",
                              f"{ids} print in place, but the profile declines print-in-place or turns the overhang check off"))
    # The record does not say which way each gap faces, so the larger axis clearance governs: the
    # conservative choice, since a gap at least that wide separates in either direction.
    axes = [value_at(profile, f"{PIP_CLEARANCE}{axis}") for axis in ("xy", "z")]
    known = [value for value in axes if isinstance(value, (int, float)) and not isinstance(value, bool)]
    tight = [str(item.get("id")) for item in joints
             if known and isinstance(item.get("min_clearance_mm"), (int, float)) and item["min_clearance_mm"] < max(known)]
    if tight:
        found.append(Conflict("pip_play_too_tight", "flow/profile/processes/fdm/pip_clearance_mm",
                              f"{', '.join(tight)} require(s) a min_clearance_mm below the print-in-place clearance "
                              f"{max(known)} mm, so the printed gap would fuse"))
    return found


def conflicts(product: Any, requirements: Any, interfaces: Any, parts: Mapping[str, Any],
              fea_cases: Iterable[str]) -> list[Conflict]:
    """Where the profile contradicts the record (design section 4): requirements.yaml, interfaces.yaml,
    the part records by PN and the authored FEA case ids."""
    profile = profile_of(product)
    if not isinstance(profile, Mapping):
        return []
    items = [item for item in _mapping(interfaces).get("interfaces") or [] if isinstance(item, Mapping)]
    found = []
    fea = value_at(profile, "fea")
    if fea == "off":
        needs = [str(r.get("id")) for r in _mapping(requirements).get("requirements") or []
                 if isinstance(r, Mapping) and _mapping(r.get("verification")).get("step") == "fea"]
        needs += [f"analysis case {case}" for case in fea_cases]
        if needs:
            found.append(Conflict("fea_off", "flow/profile/fea", f"fea is off, but {', '.join(needs)} need(s) FEA"))
    critical = [str(item.get("id")) for item in items if _known_true(_mapping(item.get("functional")).get("critical"))]
    if value_at(profile, "stack_up") == "off" and critical:
        found.append(Conflict("stack_up_off", "flow/profile/stack_up",
                              f"stack_up is off, but {', '.join(critical)} is a critical interface"))
    if fea == "on" and value_at(profile, "critique") in ("light", "off"):
        found.append(Conflict("fea_without_critique", "flow/profile/critique",
                              f"fea is on, but critique is {value_at(profile, 'critique')}: an analysed design gets the full critique"))
    supported = sorted(pn for pn, part in parts.items() if _plans_supports(_mapping(part)))
    if value_at(profile, "processes/fdm/supports") == "free" and supported:
        found.append(Conflict("supports_free", "flow/profile/processes/fdm/supports",
                              f"parts must print support-free, but {', '.join(supported)} plan(s) supports: "
                              "decide supports or a redesign at the PARTS gate"))
    found += _pip_conflicts(profile, items)
    use_case = _mapping(_mapping(product).get("use_case"))
    service = _mapping(use_case.get("service_class"))
    safety = service.get("status") == "known" and service.get("value") == "safety_critical"
    if safety or _mapping(product).get("rigor") == "regulated":
        reason = "safety_critical service" if safety else "regulated rigor"
        found += [Conflict("safety_off", f"flow/profile/{field}",
                           f"{reason} refuses {field.replace('/', '.')}: {value_at(profile, field)}", refuse=True)
                  for field in CHECK_SWITCHES if value_at(profile, field) in ("off", "light")]
    return found


# --- record -------------------------------------------------------------------------------------------------------

def blank_profile(decision: str) -> dict[str, Any]:
    """A profile citing `decision` with every question not asked, and the motion sweep stated."""
    clearance = {"xy": NOT_ASKED, "z": NOT_ASKED}
    fdm: dict[str, Any] = {field.removeprefix(FDM): NOT_ASKED for _option, field, _words, _number in OPTIONS
                           if field.startswith(FDM) and not field.startswith(PIP_CLEARANCE)}
    fdm["pip_clearance_mm"] = clearance
    return {"decision": decision, "human_approval": NOT_ASKED, "fea": NOT_ASKED, "stack_up": NOT_ASKED,
            "critique": NOT_ASKED, "motion": "auto", "storage_results_in_git": NOT_ASKED, "processes": {"fdm": fdm}}


def answered_profile(decision: str, answers: Mapping[str, Any]) -> dict[str, Any]:
    """blank_profile with each answer, keyed by profile field, set."""
    profile = blank_profile(decision)
    for field, value in answers.items():
        *parents, key = field.split("/")
        target: dict[str, Any] = profile
        for parent in parents:
            target = target[parent]
        target[key] = value
    return profile


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _content(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _dump(block: Mapping[str, Any], indent: int) -> str:
    text = yaml.safe_dump(dict(block), sort_keys=False, default_flow_style=False, allow_unicode=True, width=4096)
    return "".join(" " * indent + line if line.strip() else line for line in text.splitlines(keepends=True))


def with_profile(text: str, profile: Mapping[str, Any]) -> str:
    """`text` (product.yaml, \\n newlines) with flow.profile set to `profile`: an earlier profile block
    removed and the new one placed last in the block-style flow mapping, which is added when absent.
    Raises ProfileError for a flow this textual edit cannot place."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if re.match(r"^flow:\s*(#.*)?$", line)), None)
    if start is None:
        if any(re.match(r"^flow\s*:", line) for line in lines):
            raise ProfileError("product.yaml flow is not a block-style mapping; write it as one, then record again")
        prefix = text if not text or text.endswith("\n") else text + "\n"
        return prefix + "flow:\n" + _dump({"profile": profile}, 2)
    end = next((i for i in range(start + 1, len(lines)) if _content(lines[i]) and _indent_of(lines[i]) == 0), len(lines))
    children = [line for line in lines[start + 1:end] if _content(line)]
    column = _indent_of(children[0]) if children else 2
    body, skipping = [], False
    for line in lines[start + 1:end]:
        if _content(line) and _indent_of(line) == column:
            skipping = re.match(rf"^ {{{column}}}profile\s*:", line) is not None
        if not skipping:
            body.append(line)
    last = max((i for i, line in enumerate(body) if _content(line)), default=-1)
    if last >= 0 and not body[last].endswith("\n"):
        body[last] += "\n"
    body.insert(last + 1, _dump({"profile": profile}, column))
    return "".join(lines[:start + 1] + body + lines[end:])


def answers_body(profile: Mapping[str, Any], today: date) -> str:
    """The decision body of a recorded profile: its date, its source and one table row per answer."""
    def rows(value: Any, prefix: str) -> list[str]:
        if isinstance(value, Mapping):
            return [row for key, item in value.items() for row in rows(item, f"{prefix}{key}.")]
        word = json.dumps(value) if isinstance(value, bool) else str(value)
        return [f"| {prefix[:-1]} | {word} |"]

    answers = [row for key, value in profile.items() if key != "decision" for row in rows(value, f"{key}.")]
    return "\n".join([
        f"- Date: {today.isoformat()}",
        "- Source: the user's answers to the project-start questions (cad-flow `flow_profile.py questions`)", "",
        "`product.yaml` `flow.profile` records these answers. `not_asked` marks a question nobody asked: its check",
        "reports it not assessed, and blocks once the check falls due. The motion sweep was stated, not asked.", "",
        "| Setting | Answer |", "|---|---|", *answers, ""])


def write_decision(hw: Path, number: str, *, status: str, gate: str, version: str, title: str, body: str) -> Path:
    """Write hw/decisions/<number>.md in the decision record format, the one place this script knows
    that format. `gate` is a gated stage (product.schema.json $defs/gated_stage). Raises FileExistsError
    for an existing record, since decisions are append-only, and ValueError for another gate."""
    if gate not in GATED_STAGES:
        raise ValueError(f"gate {gate!r} is not one of {', '.join(GATED_STAGES)}")
    path = Path(hw) / "decisions" / f"{number}.md"
    if path.exists():
        raise FileExistsError(path)
    _replace(path, "\n".join([f"Status: {status}", f"Gate: {gate}", f"Version: {version}", "",
                              f"# {number}: {title}", "", body]))
    return path


def decision_approves(hw: Path, number: str) -> bool:
    """Whether hw/decisions/<number>.md exists and records the user's explicit approval, the one place this
    script reads the decision record format: its first line is `Status: APPROVED`."""
    path = Path(hw) / "decisions" / f"{number}.md"
    if not path.is_file():
        return False
    return path.read_text(encoding="utf-8", errors="replace").partition("\n")[0].strip() == "Status: APPROVED"


def _replace(path: Path, data: str, newline: str = "\n") -> None:
    """Write `data` beside `path` and rename it onto `path` in one step, keeping an existing file's mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, staged = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data.replace("\n", newline).encode("utf-8"))
        if path.exists():
            os.chmod(staged, path.stat().st_mode & 0o7777)
        os.replace(staged, path)
    except BaseException:
        Path(staged).unlink(missing_ok=True)
        raise


def record(hw: Path, decision: str, answers: Mapping[str, Any], today: date | None = None) -> dict[str, Any]:
    """Write `answers` (profile field: value) as flow.profile citing `decision`, then the decision record.
    Raises ProfileError, having written nothing, for an existing decision or a product.yaml it cannot edit."""
    hw = Path(hw)
    if DECISION.fullmatch(decision) is None:
        raise ProfileError(f"--decision {decision!r} is not DR-###")
    record_path = hw / "decisions" / f"{decision}.md"
    if record_path.exists():
        raise ProfileError(f"{record_path} exists; decisions are append-only, so cite a new DR-###")
    path = hw / "product.yaml"
    try:
        raw = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ProfileError(f"cannot read {path}: {error}") from None
    newline = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")
    product = read_product(hw)
    if not isinstance(product, Mapping) or not isinstance(product.get("flow", {}), Mapping):
        raise ProfileError(f"{path} is not a mapping with a flow mapping")
    profile = answered_profile(decision, answers)
    if invalid := shape_problems(profile):
        raise ProfileError(refusal(invalid))
    after = with_profile(text, profile)
    expected = copy.deepcopy(dict(product))
    expected["flow"] = {**expected.get("flow", {}), "profile": profile}
    if yaml_loader.safe_load(after) != expected:
        raise ProfileError(f"{path}: the textual edit would not give the intended data; simplify its flow block")
    _replace(path, after, newline)
    record_path = write_decision(hw, decision, status="APPROVED", gate="intent", version="N/A", title="Flow profile",
                                 body=answers_body(profile, today or date.today()))
    asked = [f for f in DUE if value_at(profile, f) == NOT_ASKED]
    return {"product": str(path), "decision": str(record_path), "not_asked": [f.replace("/", ".") for f in asked]}


# --- command line -------------------------------------------------------------------------------------------------

def _answer(words: tuple[str, ...], number: bool) -> Any:
    def parse(text: str) -> Any:
        if text in words:
            return {"yes": True, "no": False}.get(text, text) if words == ("yes", "no") else text
        try:
            value = float(text) if number else None
        except ValueError:
            value = None
        if value is None or not value > 0:
            accepted = " or ".join([*words, *(["a positive number"] if number else [])])
            raise argparse.ArgumentTypeError(f"expected {accepted}, got {text!r}")
        return int(value) if value.is_integer() else value
    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("questions", help="print the project-start questions to relay verbatim")
    rec = sub.add_parser("record", help="record the user's answers as flow.profile and its decision")
    rec.add_argument("--hw", type=Path, required=True, help="the design record directory (hw/)")
    rec.add_argument("--decision", required=True, help="the new DR-### that records the answers")
    for option, field, words, number in OPTIONS:
        accepted = "|".join([*words, *(["N"] if number else [])])
        rec.add_argument(option, dest=field, type=_answer(words, number), metavar=accepted,
                         help=f"flow.profile {field.replace('/', '.')}; left out: not_asked")
    check = sub.add_parser("check", help="report what refuses a build, a validation or a stage advance")
    check.add_argument("--hw", type=Path, required=True, help="the design record directory (hw/)")
    check.add_argument("--stage", choices=STAGES, help="the stage to check at (default: product.yaml flow.stage)")
    return root


def print_questions() -> None:
    print("Ask the user these questions verbatim, then record the answers with flow_profile.py record.\n")
    for number, (text, _records) in enumerate(QUESTIONS, start=1):
        print(f"{number}. {text}")
    print(f"\n{MOTION_STATEMENT}\n\nEach answer records as:")
    for number, (_text, records) in enumerate(QUESTIONS, start=1):
        print(f"  {number}: {records}")
    print("A question left unasked records not_asked, and blocks once its check falls due.")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "questions":
            print_questions()
            return 0
        if args.command == "record":
            answers = {field: getattr(args, field) for _option, field, _words, _number in OPTIONS
                       if getattr(args, field) is not None}
            print(json.dumps(record(args.hw, args.decision, answers), indent=2))
            return 0
        stage, found = gate(args.hw, args.stage)
    except ProfileError as error:
        print(f"flow_profile.py {args.command}: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"ok": not found, "stage": stage, "problems": [asdict(item) for item in found]}, indent=2))
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
