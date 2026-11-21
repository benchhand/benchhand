#!/usr/bin/env -S uv run --script

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["jsonschema>=4.23", "pyyaml>=6.0", "referencing>=0.28.4"]
# ///
"""Guided machine setup: check a capability profile built from published specs, and write it into the record.

    machine_profile.py check <hw>
    machine_profile.py write <hw> <answers.yaml>

`check` runs the record's schema validation and loader on hw/manufacturing/capabilities.yaml, then the rules the
schema cannot say:

- every known machine, envelope or tool fact cites a `datasheet` or `supplier` source with a `url` and an `accessed` date;
- a `kind: user` source is allowed only for installed configuration: nozzle diameter and material, the usable envelope
  and `max_speed`, which also names the frozen slicer file (`artifact` and `sha256`, checked against the file);
- every guideline pin names a set of the `guidelines.discover()` catalog, at that revision;
- a profile with no `machine` carries no pin.
- the record has a supported `record_version` in product.yaml, because the loader stops before it hashes a source
  artifact otherwise; a missing or unsupported version is a finding, never a silent skip.

An UNKNOWN fact with a reason is accepted. Each problem prints as `<profile id>: <field path>: <message>`; the exit
status is 1 when there is any, and 2 when a file or answer cannot be used (an `error:` line on stderr). `write` takes one bare profile mapping (the answers file), adds it to capabilities.yaml or replaces the
profile of the same id, and copies the starter material profile of each material it does not forbid into materials.yaml when
that file lacks it (a starter is found by its exact id). It writes nothing when `check` would fail, and replaces the
files one after the other (each staged beside its target, then `os.replace`d; materials.yaml first). A failure while
staging changes nothing. A failure after the first replace leaves the new materials.yaml with the old capabilities.yaml:
an unused starter profile, never a profile without its material. YAML comments in the rewritten files are not kept.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import os
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
FROZEN_FIELDS = frozenset({"parameters/max_speed", "tools/parameters/max_speed"})  # a user value needs the frozen slicer file
USER_FIELDS = FROZEN_FIELDS | {"envelope/x", "envelope/y", "envelope/z", "parameters/nozzle_diameter", "parameters/nozzle_material",
                               "tools/parameters/nozzle_diameter", "tools/parameters/nozzle_material"}


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
            elif rule_key in FROZEN_FIELDS and not (source.get("artifact") and source.get("sha256")):
                found.append("a user max_speed must come from a frozen slicer file: name its artifact and sha256")
        elif kind not in SOURCED_KINDS:
            found.append(f"a {kind} source is not enough; cite a datasheet or supplier")
        elif not source.get("url") or not source.get("accessed"):
            found.append(f"a {kind} source needs a url and the date it was accessed")
    return found


def profile_problems(profile: Mapping[str, Any], catalog: guidelines.Catalog) -> list[str]:
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


def _artifacts(node: Any) -> Iterator[str]:
    """The `artifact` paths that sources below `node` name."""
    if isinstance(node, Mapping):
        if isinstance(node.get("artifact"), str):
            yield node["artifact"]
        for value in node.values():
            yield from _artifacts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _artifacts(value)


def documents_problems(documents: Mapping[str, Any], hw: Path) -> list[str]:
    """Every problem of the capability (and material) documents: the record's own checks, then the profile rules.

    `intent.check_documents` does not fit: it normalizes without `hw`, so it never checks a named source artifact against
    its sha256, which a user `max_speed` rests on. Problems about the artifacts these documents name are kept, and so is a
    missing or unsupported record version: the loader stops there before it hashes any artifact, so the check reports it."""
    own = {rel: documents[rel] for rel in (CAPABILITIES, MATERIALS) if rel in documents}
    record = intent.normalize_record(documents, hw=hw)
    wanted = {f"hw/{rel}" for rel in own} | {f"hw/{artifact}" for artifact in _artifacts(list(own.values()))}
    found = [problem for problem in intent.schema_problems(own) + list(record.problems)
             if (problem.path in wanted or problem.code == intent.VERSION_UNSUPPORTED) and problem.severity == "error"]
    messages = [f"{problem.path}: {problem.pointer or '-'}: {problem.message}" for problem in found]
    catalog = guidelines.discover(hw)
    for profile in (documents.get(CAPABILITIES) or {}).get("profiles") or ():
        if isinstance(profile, Mapping):
            messages += profile_problems(profile, catalog)
    return messages


def _read(hw: Path) -> dict[str, Any]:
    """The record documents at `hw`; ValueError when one cannot be read or capabilities.yaml has the wrong shape."""
    documents, problems = intent.read_documents(hw)
    if problems:
        raise ValueError("; ".join(problem.message for problem in problems))
    capabilities = documents.get(CAPABILITIES)
    if capabilities is not None and not (isinstance(capabilities, Mapping) and isinstance(capabilities.get("profiles", []), list)):
        raise ValueError(f"hw/{CAPABILITIES} must be a mapping with a `profiles` list")
    if MATERIALS in documents and not isinstance(documents[MATERIALS], Mapping):
        raise ValueError(f"hw/{MATERIALS} must be a mapping with a `profiles` list")
    return documents


def check(hw: Path) -> list[str]:
    """Every problem of the capability profiles at `hw`; empty when they pass. ValueError when the files cannot be read."""
    return documents_problems(_read(Path(hw)), Path(hw))


def _starter(material: str) -> dict[str, Any] | None:
    path = STARTERS / f"{material}.yaml"
    if not path.is_file():
        return None
    for item in yaml_loader.safe_load(path.read_text(encoding="utf-8")).get("profiles") or ():
        if item.get("id") == material:
            return dict(item)
    return None


def _replace_all(files: Mapping[Path, str]) -> None:
    """Replace each file whole, as prune.py does: stage every one beside its target, then `os.replace` each. A failure
    before the first replace leaves every file as it was and removes the staged copies."""
    staged = {target: target.with_name(f".{target.name}.{os.getpid()}.tmp") for target in files}
    try:
        for target, path in staged.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(files[target], encoding="utf-8")
        for target, path in staged.items():
            os.replace(path, target)
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)


def _dump(document: Any) -> str:
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def write(hw: Path, answers: Path) -> list[str]:
    """Add or replace the profile in `answers` (one bare profile mapping with an id). Returns the problems, with nothing
    written, when the result would fail `check`; ValueError when the answers or the record files cannot be used."""
    hw = Path(hw)
    profile = yaml_loader.safe_load(Path(answers).read_text(encoding="utf-8"))
    if not isinstance(profile, Mapping) or not isinstance(profile.get("id"), str):
        raise ValueError(f"{answers} must hold one profile mapping with an id")
    documents = copy.deepcopy(_read(hw))
    capabilities = documents.get(CAPABILITIES) or {}
    profiles = list(capabilities.get("profiles") or ())
    if not all(isinstance(item, Mapping) for item in profiles):
        raise ValueError(f"hw/{CAPABILITIES} holds a profile that is not a mapping")
    if any(item.get("id") == profile["id"] for item in profiles):
        profiles = [dict(profile) if item.get("id") == profile["id"] else item for item in profiles]
    else:
        profiles.append(dict(profile))
    documents[CAPABILITIES] = {**capabilities, "profiles": profiles}
    materials = documents.get(MATERIALS) or {}
    have = {item.get("id") for item in materials.get("profiles") or () if isinstance(item, Mapping)}
    copied = False
    for entry in profile.get("materials") or ():
        starter = _starter(entry["material"]) if isinstance(entry, Mapping) and entry.get("status") != "forbidden" \
            and isinstance(entry.get("material"), str) else None
        if starter is not None and starter["id"] not in have:
            materials = {**materials, "profiles": [*(materials.get("profiles") or ()), starter]}
            have.add(starter["id"])
            copied = True
    if copied:
        documents[MATERIALS] = materials
    problems = documents_problems(documents, hw)
    if problems:
        return problems
    _replace_all({hw / rel: _dump(documents[rel]) for rel in ((MATERIALS, CAPABILITIES) if copied else (CAPABILITIES,))})
    return []


def main(argv: Sequence[str] | None = None) -> int:
    """0 when the profiles pass (or were written), 1 for rule findings, 2 for a file or answer that cannot be used."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="check the capability profiles").add_argument("hw", type=Path)
    writer = commands.add_parser("write", help="add or replace one profile")
    writer.add_argument("hw", type=Path)
    writer.add_argument("answers", type=Path)
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return int(stop.code or 0)
    try:
        problems = check(args.hw) if args.command == "check" else write(args.hw, args.answers)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
