#!/usr/bin/env -S uv run --script

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["jsonschema>=4.23", "pyyaml>=6.0"]
# ///
"""The flow profile: the project-start questions, the record of their answers, and the check on it.

questions  prints the questions the lead relays to the user verbatim at GROUND, and the motion sweep,
           which is stated to the user and never asked.
record     writes the answers to hw/product.yaml flow.profile, citing --decision, and writes that decision
           as an accepted MADR record under hw/decisions/. The lead asks each question showing its default.
           Every question is an option the user answered (--fea off) or a default the user accepted
           (--accept-default fea); the profile's `asked` map records which. A question neither answered
           nor accepted refuses the record. product.yaml is edited as text, so its comments and layout
           survive: the edit must parse to exactly the intended data, and the file is replaced in one rename.
check      reports what blocks at --stage (default: product.yaml flow.stage): a missing profile or decision
           from SPECIFY on, a value the schema rejects, and a start question with no `asked` answer.

The gate is cad-record's flow_profile_gate.py, a pure function of the parsed record; this script reads the
work tree for it and judges the cited decision with cad-record's decision reader (validate.py approval_problem).
The dependency runs one way: cad-flow imports cad-record, never the reverse.

Exit status: 0 nothing blocks (questions, record: done), 1 check found a blocking problem, 2 usage
or a record that cannot be read or edited; nothing was written.
"""

# TODO(benchhand-d55): a plan command prints the stages, the L4 units and the decisions due at each gate.

from __future__ import annotations

import argparse
import copy
import functools
import importlib.util
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict
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
decision_records = _load_module("cad_record_decision_records", CAD_RECORD_SCRIPTS / "decision_records.py")
gate = _load_module("cad_record_flow_profile_gate", CAD_RECORD_SCRIPTS / "flow_profile_gate.py")


def load_validate() -> Any:
    """cad-record's validate.py, whose approval_problem judges a cited decision. Loaded when the check needs it."""
    return _load_module("cad_record_validate", CAD_RECORD_SCRIPTS / "validate.py")


DECISION = re.compile(r"DR-[0-9]{3}")
FEA_BAND_MAX = 50
FEA_ENABLED, FEA_MODE, FEA_FAST_BAND = gate.FEA_ENABLED, gate.FEA_MODE, "fast_band_pct"
USER, ACCEPTED_DEFAULT = gate.USER, gate.ACCEPTED_DEFAULT


def _words(*words: str, number: bool = False, booleans: dict[str, bool] | None = None) -> Any:
    """An answer parser: one of `words` (each mapped through `booleans` when given), or a positive number."""
    def parse(text: str) -> Any:
        if text in words:
            return booleans[text] if booleans else text
        try:
            value = float(text) if number else None
        except ValueError:
            value = None
        if value is None or not value > 0:
            raise argparse.ArgumentTypeError(f"expected {' or '.join([*words, *(['a positive number'] if number else [])])}, "
                                             f"got {text!r}")
        return int(value) if value.is_integer() else value
    return parse


def _fea_answer(text: str) -> dict[str, Any]:
    """--fea off | accurate | fast[:<band %>], as the fea shape."""
    if text == "off":
        return {FEA_ENABLED: False}
    if text == "accurate":
        return {FEA_ENABLED: True, FEA_MODE: "accurate"}
    mode, _colon, band = text.partition(":")
    if mode == "fast" and not band:
        return {FEA_ENABLED: True, FEA_MODE: "fast"}
    try:
        value = float(band) if mode == "fast" else None
    except ValueError:
        value = None
    if value is None or not 0 < value <= FEA_BAND_MAX:
        raise argparse.ArgumentTypeError(f"expected off, accurate, fast or fast:<band %> with 0 < band <= {FEA_BAND_MAX}, "
                                         f"got {text!r}")
    return {FEA_ENABLED: True, FEA_MODE: "fast", FEA_FAST_BAND: int(value) if value.is_integer() else value}


# (option, profile field, answer parser, metavar), in the order of gate.QUESTION_FIELDS
OPTIONS: tuple[tuple[str, str, Any, str], ...] = (
    ("--human-approval", "human_approval", _words("all", "key"), "all|key"),
    ("--fea", gate.FEA, _fea_answer, "off|accurate|fast[:PCT]"),
    ("--stack-up", "stack_up", _words("auto", "on", "off"), "auto|on|off"),
    ("--critique", "critique", _words("full", "light", "off"), "full|light|off"),
    ("--results-in-git", "storage_results_in_git", _words("yes", "no", booleans={"yes": True, "no": False}), "yes|no"),
    ("--orientation", "processes/fdm/orientation", _words("user", "propose"), "user|propose"),
    ("--supports", "processes/fdm/supports", _words("allowed", "free", "off"), "allowed|free|off"),
    ("--overhang-deg", "processes/fdm/overhang_deg", _words("calibrated", "off", number=True), "N|calibrated|off"),
    ("--bridge-mm", "processes/fdm/bridge_mm", _words("calibrated", "off", number=True), "N|calibrated|off"),
    ("--min-wall-mm", "processes/fdm/min_wall_mm", _words("calibrated", "off", number=True), "N|calibrated|off"),
    ("--min-feature-mm", "processes/fdm/min_feature_mm", _words("calibrated", "off", number=True), "N|calibrated|off"),
    ("--warp", "processes/fdm/warp", _words("calibrated", "off"), "calibrated|off"),
    ("--cosmetic-faces", "processes/fdm/cosmetic_faces", _words("on", "off", booleans={"on": True, "off": False}), "on|off"),
    ("--print-in-place", "processes/fdm/print_in_place", _words("offer", "no", booleans={"offer": True, "no": False}),
     "offer|no"),
    ("--pip-clearance-xy", "processes/fdm/pip_clearance_mm/xy", _words("calibrated", number=True), "N|calibrated"),
    ("--pip-clearance-z", "processes/fdm/pip_clearance_mm/z", _words("calibrated", number=True), "N|calibrated"),
)
# Design section 3, verbatim, each with the options its answer records.
QUESTIONS: tuple[tuple[str, str], ...] = (
    ("Must any part carry a load where breaking or bending would matter? Should I simulate it?",
     "--fea off|accurate|fast[:PCT] (fast escalates to accurate within PCT % of a limit)"),
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
RECORDED_OPTION = "Record the user's answers as flow.profile"


class ProfileError(Exception):
    """A record this script cannot read or edit; nothing was written."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


# --- reading ------------------------------------------------------------------------------------------------------

def read_product(hw: Path) -> Any:
    path = Path(hw) / "product.yaml"
    try:
        return yaml_loader.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ProfileError(f"cannot read {path}: {error}") from error


def read_parts(hw: Path) -> dict[str, Any]:
    """The part records under hw/parts/<PN>/part.yaml by PN. A part record that does not parse counts as no
    part here; validate.py reports it."""
    parts: dict[str, Any] = {}
    for path in sorted((Path(hw) / "parts").glob("*/part.yaml")):
        try:
            parts[path.parent.name] = yaml_loader.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
    return parts


def check(hw: Path, stage: str | None = None) -> tuple[str, list[Any]]:
    """(the stage checked, what blocks at it) for the work tree `hw`. The cited decision approves when
    cad-record's reader finds it accepted. Raises ProfileError when product.yaml cannot be read."""
    approval = functools.partial(load_validate().approval_problem, Path(hw))
    checked, found = gate.gate(read_product(hw), read_parts(hw), approval, stage)
    return checked, list(found)


# --- record -------------------------------------------------------------------------------------------------------

def defaulted_profile(decision: str) -> dict[str, Any]:
    """A profile citing `decision` with every decision at its default and no `asked` answer."""
    profile: dict[str, Any] = {"decision": decision}
    for field, value in gate.DEFAULTS.items():
        *parents, key = field.split("/")
        target = profile
        for parent in parents:
            target = target.setdefault(parent, {})
        target[key] = copy.deepcopy(value)
    return profile


def answered_profile(decision: str, answers: Mapping[str, Any], accepted: Iterable[str] = ()) -> dict[str, Any]:
    """defaulted_profile with each answer (profile field: value) set, and `asked` saying who settled each
    question: the user for an answer, the user accepting the default for a field in `accepted`."""
    profile = defaulted_profile(decision)
    for field, value in answers.items():
        *parents, key = field.split("/")
        target: dict[str, Any] = profile
        for parent in parents:
            target = target[parent]
        target[key] = value
    how = {**{field: ACCEPTED_DEFAULT for field in accepted}, **{field: USER for field in answers}}
    profile["asked"] = {field: how[field] for field in gate.QUESTION_FIELDS if field in how}
    return profile


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _content(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


class _Quoted(str):
    """A string value the profile dump writes in double quotes, so a hand edit beside it keeps quoting."""


class _Dumper(yaml.SafeDumper):  # type: ignore[misc]  # PyYAML ships no stubs here, so SafeDumper is Any
    pass


_Dumper.add_representer(_Quoted, lambda dumper, data: dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"'))


def _quoted(value: Any) -> Any:
    """`value` with every string value, not key, marked for double quotes."""
    if isinstance(value, Mapping):
        return {key: _quoted(item) for key, item in value.items()}
    return _Quoted(value) if isinstance(value, str) else value


def _dump(block: Mapping[str, Any], indent: int) -> str:
    text = yaml.dump(_quoted(dict(block)), Dumper=_Dumper, sort_keys=False, default_flow_style=False, allow_unicode=True,
                     width=4096)
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


def answers_table(profile: Mapping[str, Any]) -> str:
    """One table row per answer of a recorded profile, with who settled it: the user, the user accepting the
    default, or stated (the motion sweep, which is never asked)."""
    asked = _mapping(profile.get("asked"))

    def rows(value: Any, path: str) -> list[str]:
        if isinstance(value, Mapping):
            return [row for key, item in value.items() for row in rows(item, f"{path}/{key}" if path else key)]
        word = json.dumps(value) if isinstance(value, bool) else str(value)
        parts = path.split("/")
        how = next((asked[key] for key in ("/".join(parts[:n]) for n in range(len(parts), 0, -1)) if key in asked), "stated")
        return [f"| {path.replace('/', '.')} | {word} | {how} |"]

    answers = [row for key, value in profile.items() if key not in ("decision", "asked") for row in rows(value, key)]
    return "\n".join(["| Setting | Answer | Asked |", "|---|---|---|", *answers])


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


def write_profile_decision(decisions: Path, number: int, profile: Mapping[str, Any], today: date) -> Path:
    """The accepted MADR decision that holds the answers, through cad-record's decision writer."""
    context = ("The user answered the project-start questions (cad-flow `flow_profile.py questions`) at INTENT. "
               "`product.yaml` `flow.profile` records the answers. The lead showed the user each question with its "
               f"default: `{USER}` marks an answer the user gave, `{ACCEPTED_DEFAULT}` a default the user accepted, and "
               "`stated` the motion sweep, which runs once a joint moves and is never asked.")
    try:
        return Path(decision_records.write_decision(
            decisions, title="Flow profile", status="accepted", date=today, context=context, options=(RECORDED_OPTION,),
            chosen=RECORDED_OPTION, justification="the user answered each project-start question or accepted its default",
            more_information=answers_table(profile), number=number))
    except decision_records.DecisionRecordError as error:
        raise ProfileError(str(error)) from error


def record(hw: Path, decision: str, answers: Mapping[str, Any], accepted: Iterable[str] = (),
           today: date | None = None) -> dict[str, Any]:
    """Write `answers` (profile field: value) and the defaults of the `accepted` fields as flow.profile citing
    `decision`, then the decision record. Raises ProfileError, having written nothing, for an existing
    decision, a start question neither answered nor accepted, or a product.yaml it cannot edit."""
    hw = Path(hw)
    accepted = list(accepted)
    if DECISION.fullmatch(decision) is None:
        raise ProfileError(f"--decision {decision!r} is not DR-###")
    options = {field: option for option, field, _parse, _metavar in OPTIONS}
    if both := [options[field] for field in accepted if field in answers]:
        raise ProfileError(f"{', '.join(both)} answered and also accepted as the default; give one")
    if unasked := [option for field, option in options.items() if field not in answers and field not in accepted]:
        raise ProfileError(f"ask the user {', '.join(unasked)} and answer it, or accept its default with --accept-default; "
                           "nothing was recorded")
    decisions = hw / "decisions"
    number = int(decision.removeprefix("DR-"))
    try:
        taken = {item.number: item for item in decision_records.read_decisions(decisions)} if decisions.is_dir() else {}
    except decision_records.DecisionRecordError as error:
        raise ProfileError(str(error)) from error
    if number in taken:
        raise ProfileError(f"{decisions / taken[number].file_name} exists; decisions are append-only, so cite a new DR-###")
    path = hw / "product.yaml"
    try:
        raw = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ProfileError(f"cannot read {path}: {error}") from error
    newline = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")
    product = read_product(hw)
    if not isinstance(product, Mapping) or not isinstance(product.get("flow", {}), Mapping):
        raise ProfileError(f"{path} is not a mapping with a flow mapping")
    profile = answered_profile(decision, answers, accepted)
    if invalid := gate.shape_problems(profile):
        raise ProfileError(gate.refusal(invalid))
    after = with_profile(text, profile)
    expected = copy.deepcopy(dict(product))
    expected["flow"] = {**expected.get("flow", {}), "profile": profile}
    if yaml_loader.safe_load(after) != expected:
        raise ProfileError(f"{path}: the textual edit would not give the intended data; simplify its flow block")
    record_path = write_profile_decision(decisions, number, profile, today or date.today())
    _replace(path, after, newline)
    return {"product": str(path), "decision": str(record_path)}


# --- command line -------------------------------------------------------------------------------------------------

def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("questions", help="print the project-start questions to relay verbatim")
    rec = sub.add_parser("record", help="record the user's answers as flow.profile and its decision")
    rec.add_argument("--hw", type=Path, required=True, help="the design record directory (hw/)")
    rec.add_argument("--decision", required=True, help="the new DR-### that records the answers")
    for option, field, parse, metavar in OPTIONS:
        rec.add_argument(option, dest=field, type=parse, metavar=metavar,
                         help=f"flow.profile {field.replace('/', '.')}: the user's answer")
    rec.add_argument("--accept-default", dest="accept_default", nargs="+", default=[], metavar="OPTION",
                     choices=[option.removeprefix("--") for option, _field, _parse, _metavar in OPTIONS],
                     help="the options (without --) whose default the user accepted when asked")
    verify = sub.add_parser("check", help="report what blocks a build, a validation or a stage advance")
    verify.add_argument("--hw", type=Path, required=True, help="the design record directory (hw/)")
    verify.add_argument("--stage", choices=gate.STAGES, help="the stage to check at (default: product.yaml flow.stage)")
    return root


def print_questions() -> None:
    print("Ask the user these questions verbatim, then record the answers with flow_profile.py record.\n")
    for number, (text, _records) in enumerate(QUESTIONS, start=1):
        print(f"{number}. {text}")
    print(f"\n{MOTION_STATEMENT}\n\nEach answer records as:")
    for number, (_text, records) in enumerate(QUESTIONS, start=1):
        print(f"  {number}: {records}")
    print("\nDefaults, shown with each question:")
    for field, value in gate.DEFAULTS.items():
        print(f"  {field.replace('/', '.')}: {json.dumps(value)}")
    print("Show the user each question with its default. Record an answer as its option, or a")
    print("default the user accepts with --accept-default <options>. A question neither answered nor accepted refuses")
    print("the record.")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "questions":
            print_questions()
            return 0
        if args.command == "record":
            answers = {field: getattr(args, field) for _option, field, _parse, _metavar in OPTIONS
                       if getattr(args, field) is not None}
            by_name = {option.removeprefix("--"): field for option, field, _parse, _metavar in OPTIONS}
            accepted = [by_name[name] for name in args.accept_default]
            print(json.dumps(record(args.hw, args.decision, answers, accepted), indent=2))
            return 0
        stage, found = check(args.hw, args.stage)
    except ProfileError as error:
        print(f"flow_profile.py {args.command}: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"ok": not found, "stage": stage, "problems": [asdict(item) for item in found]}, indent=2))
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
