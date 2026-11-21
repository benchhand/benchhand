#!/usr/bin/env -S uv run --script

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["jsonschema>=4.23", "pyyaml>=6.0"]
# ///
"""Guided machine setup: check a capability profile built from published specs, and write it into the record.

    machine_profile.py check <hw>
    machine_profile.py write <hw> <answers.yaml>

`check` runs the record's schema validation and loader on hw/manufacturing/capabilities.yaml, then the rules the
schema cannot say:

- every known machine, envelope or tool fact cites a `datasheet` or `supplier` source with a `url` and an `accessed` date;
- a `kind: user` source is allowed only for installed configuration: nozzle diameter and material, the usable envelope
  and `max_speed` (taken from a frozen slicer file);
- every guideline pin names a set of the `guidelines.discover()` catalog, at that revision;
- a profile with no `machine` carries no pin.

An UNKNOWN fact with a reason is accepted. Each problem prints as `<profile id>: <field path>: <message>`; the exit
status is 1 when there is any. `write` takes one profile (the answers file), adds it to capabilities.yaml or replaces the
profile of the same id, and copies the starter material profile of each material it approves into materials.yaml when
that file lacks it. It writes nothing when `check` would fail. YAML comments in the rewritten files are not kept.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

SCRIPTS = Path(__file__).resolve().parent
CAPABILITIES = "manufacturing/capabilities.yaml"
MATERIALS = "manufacturing/materials.yaml"
STARTERS = SCRIPTS.parent / "assets" / "manufacturing" / "material-profiles"
SOURCED_KINDS = frozenset({"datasheet", "supplier"})
# Installed configuration may rest on the user's word: a statement about this machine as set up, not about the product.
USER_FIELDS = frozenset({"envelope/x", "envelope/y", "envelope/z", "parameters/nozzle_diameter", "parameters/nozzle_material",
                         "parameters/max_speed", "tools/parameters/nozzle_diameter", "tools/parameters/nozzle_material"})


def _load(name: str, filename: str) -> Any:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
        if spec is None or spec.loader is None:
            raise ImportError(f"{filename} not found beside {Path(__file__).name}")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


intent = _load("cad_record_manufacturing_intent", "manufacturing_intent.py")
guidelines = _load("cad_record_guidelines", "guidelines.py")
yaml_loader = _load("cad_record_yaml_loader", "yaml_loader.py")


def _is_fact(value: object) -> bool:
    return isinstance(value, Mapping) and "status" in value


def _facts(profile: Mapping[str, Any]) -> Iterator[tuple[str, str, Mapping[str, Any]]]:
    """(field path, rule key, fact) of every machine, envelope, parameter and tool-parameter fact of the profile."""
    machine = profile.get("machine")
    for name, value in (machine.items() if isinstance(machine, Mapping) else ()):
        if _is_fact(value):
            yield f"machine/{name}", f"machine/{name}", value
    for section in ("envelope", "parameters"):
        block = profile.get(section)
        for name, value in (block.items() if isinstance(block, Mapping) else ()):
            if _is_fact(value):
                yield f"{section}/{name}", f"{section}/{name}", value
    for index, tool in enumerate(profile.get("tools") or ()):
        block = tool.get("parameters") if isinstance(tool, Mapping) else None
        for name, value in (block.items() if isinstance(block, Mapping) else ()):
            if _is_fact(value):
                yield f"tools/{index}/parameters/{name}", f"tools/parameters/{name}", value


def _source_problems(rule_key: str, fact: Mapping[str, Any]) -> list[str]:
    sources = fact.get("sources")
    if not isinstance(sources, list) or not sources:
        return ["a known value needs a datasheet or supplier source"]
    found = []
    for source in sources:
        kind = source.get("kind") if isinstance(source, Mapping) else None
        if kind == "user":
            if rule_key not in USER_FIELDS:
                found.append("a user source is allowed only for nozzle diameter, nozzle material, the usable envelope and a "
                             "max_speed from a frozen slicer file; cite the datasheet or supplier")
        elif kind not in SOURCED_KINDS:
            found.append(f"a {kind} source is not enough; cite a datasheet or supplier")
        elif not source.get("url") or not source.get("accessed"):
            found.append(f"a {kind} source needs a url and the date it was accessed")
    return found


def profile_problems(profile: Mapping[str, Any], catalog: Any) -> list[str]:
    """The sourcing and pin problems of one raw profile, as `<id>: <path>: <message>`."""
    ident = profile.get("id")
    found = []
    for path, rule_key, fact in _facts(profile):
        if fact.get("status") == "known":
            found += [f"{ident}: {path}: {message}" for message in _source_problems(rule_key, fact)]
    pins = profile.get("guidelines")
    if pins:
        if not profile.get("machine"):
            found.append(f"{ident}: guidelines: a profile with no machine carries no guideline pin")
        context = guidelines.Context(profile.get("process"), pin=tuple(pins))
        found += [f"{ident}: guidelines: {message}" for message in guidelines.pin_warnings(catalog, context)]
    return found


def documents_problems(documents: Mapping[str, Any], hw: Path) -> list[str]:
    """Every problem of the capability (and material) documents: the record's own checks, then the profile rules."""
    own = {rel: documents[rel] for rel in (CAPABILITIES, MATERIALS) if rel in documents}
    record = intent.normalize_record(documents, hw=hw)
    wanted = {f"hw/{rel}" for rel in own}
    found = [problem for problem in intent.schema_problems(own) + list(record.problems)
             if problem.path in wanted and problem.severity == "error"]
    messages = [f"{problem.path}: {problem.pointer or '-'}: {problem.message}" for problem in found]
    catalog = guidelines.discover(hw)
    for profile in (documents.get(CAPABILITIES) or {}).get("profiles") or ():
        if isinstance(profile, Mapping):
            messages += profile_problems(profile, catalog)
    return messages


def _read(hw: Path) -> dict[str, Any]:
    documents, problems = intent.read_documents(hw)
    if problems:
        raise ValueError("; ".join(problem.message for problem in problems))
    return documents


def check(hw: Path) -> list[str]:
    """Every problem of the capability profiles at `hw`; empty when they pass."""
    return documents_problems(_read(Path(hw)), Path(hw))


def _starter(material: str) -> dict[str, Any] | None:
    path = STARTERS / f"{material}.yaml"
    if not path.is_file():
        return None
    for item in yaml_loader.safe_load(path.read_text(encoding="utf-8")).get("profiles") or ():
        if item.get("id") == material:
            return dict(item)
    return None


def write(hw: Path, answers: Path) -> list[str]:
    """Add or replace the profile in `answers`; the problems, with nothing written, when the result would fail `check`."""
    hw = Path(hw)
    data = yaml_loader.safe_load(Path(answers).read_text(encoding="utf-8"))
    profile = data.get("profile", data) if isinstance(data, Mapping) else None
    if not isinstance(profile, Mapping) or not isinstance(profile.get("id"), str):
        return [f"{answers}: -: the answers file must hold one profile with an id"]
    documents = copy.deepcopy(_read(hw))
    capabilities = documents.get(CAPABILITIES) or {}
    profiles = [item for item in capabilities.get("profiles") or ()]
    replaced = [dict(profile) if item.get("id") == profile["id"] else item for item in profiles]
    capabilities["profiles"] = replaced if any(item.get("id") == profile["id"] for item in profiles) else [*profiles, dict(profile)]
    documents[CAPABILITIES] = capabilities
    materials = documents.get(MATERIALS) or {}
    have = {item.get("id") for item in materials.get("profiles") or ()}
    copied = False
    for entry in profile.get("materials") or ():
        starter = _starter(entry.get("material")) if isinstance(entry, Mapping) else None
        if starter is not None and starter["id"] not in have:
            materials["profiles"] = [*(materials.get("profiles") or ()), starter]
            have.add(starter["id"])
            copied = True
            documents[MATERIALS] = materials
    problems = documents_problems(documents, hw)
    if problems:
        return problems
    for rel in (CAPABILITIES, MATERIALS) if copied else (CAPABILITIES,):
        target = hw / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(documents[rel], sort_keys=False, allow_unicode=True), encoding="utf-8")
    return []


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="check the capability profiles").add_argument("hw", type=Path)
    writer = commands.add_parser("write", help="add or replace one profile")
    writer.add_argument("hw", type=Path)
    writer.add_argument("answers", type=Path)
    args = parser.parse_args(argv)
    try:
        problems = check(args.hw) if args.command == "check" else write(args.hw, args.answers)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
