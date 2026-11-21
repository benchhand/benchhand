#!/usr/bin/env -S uv run --script

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["click==8.5.1", "jsonschema>=4.23", "pyyaml>=6.0", "referencing>=0.28.4"]
# ///
# click: --prune's store gc runs cad_model.py in this interpreter (prune.py), and cad_model.py's CLI needs it.
"""Seal a release package of the frozen version: byte copies and a version 3 manifest under hw/releases/<REV>/.

    uv run --script release.py HW --rev A --version v3 --decision DR-007
    uv run --script release.py HW --rev A --version v3 --decision DR-007 --prune
    uv run --script release.py HW --rev A --version v3 --decision DR-007 --prune --yes DIGEST
The frozen version (product.yaml flow.frozen) defines what a release holds; release.py copies and
never builds. It refuses the release when any of these fails, and then writes nothing outside
.cad-work/:

- the frozen anchor, the approving decision, and a revision that is unused, already sealed from this
  same frozen manifest, or stale but sealed from this version, approval and frozen manifest;
- validate.py on the current record (a record of an unsupported version refuses), and the
  current record against the frozen snapshot: the snapshot set the version sealed, its structured
  documents by content and its frozen sources by their exact bytes;
- a build_guide.py okh.toml in hw/docs/ that describes another release;
- the manufacturing outputs and the process route of every custom-made part;
- the drawings, the analysis cases and the verification evidence, each a sealed bundle of that version;
- bom.csv quantities against the frozen assembly;
- the acceptance: the freeze's evidence_sha256 is the acceptance digest (evidence.py) as it stands,
  and the evidence meets the release policy of the use case's service class: every obligation in
  its scope resolved, no unresolved critical finding, an unvalidated trial only where the class
  permits one and only by approved waivers, no waiver at all for safety-critical service.

The revision's lock (layout.target_lock) is held for the whole run, so a second run of the same
revision waits and then inspects what the first left. Under it, the run inspects an existing
hw/releases/<REV>/, stages the copies, including the acceptance inventory under acceptance/, in
.cad-work/release-<REV>/, writes and checks the manifest, inspects the revision once more, claims
hw/releases/<REV>/ and renames the stage onto it. A sealed package of
another manifest version is refused. A stale package (one that fails its seal) of this version,
approval and frozen manifest is repaired: assembled again at its own release time, moved to
.cad-work/replaced/ as the new one is claimed, and put back when the claim fails.

--prune, under the same lock, never repairs: a stale package refuses and nothing is deleted.
--prune, once the package is sealed, prints the plan of superseded versions to remove (prune.py) with
its digest and changes nothing; when hw/releases/<REV>/ already verifies and holds this version and
decision, it skips sealing. --prune --yes DIGEST applies that plan when a fresh plan has the same
digest, or finishes an interrupted prune that DIGEST started.

Prints JSON, its `status` last: exit 0 released (a plan printed or applied), 1 refused, prune_refused
or prune_incomplete, 2 usage or infrastructure error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import importlib.util
import os
import posixpath
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def _load_module(name: str, path: Path) -> Any:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        sys.modules[name] = importlib.util.module_from_spec(spec)  # dataclasses look the module up here
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


validate = _load_module("cad_record_validate", Path(__file__).resolve().parent / "validate.py")
layout = validate.layout
prune = _load_module("cad_record_prune", Path(__file__).resolve().parent / "prune.py")
generated_docs = _load_module("cad_record_generated_docs", Path(__file__).resolve().parent / "generated_docs.py")
yaml_loader = validate.yaml_loader

MANIFEST = layout.MANIFEST
SCHEMA_VERSION = 3
NOT_ASSESSED = "not_assessed"  # a bundle status: the producer assessed nothing (calibration off or no capability profile)
WORKFLOW_KEYS = {"product.yaml": ("flow",)}  # workflow state; advancing it is not a design change
SHIPPED_COPIES = "manufacturing/guidelines/shipped--"  # guidelines.SHIPPED_COPY under PROJECT_RELATIVE
EVIDENCE_PATH = re.compile(r"hw/[^\s,;()\"'<>]+")  # a project-relative path inside a verification evidence cell
evidence = validate.evidence
intent = validate.intent


@dataclass(frozen=True)
class Copy:
    package: str  # path inside hw/releases/<REV>/
    original: str  # project-relative hw/ path it copies
    sha256: str  # what the original holds: its seal's hash, or its bytes when checked


@dataclass
class Release:
    """The package release.py assembles, and every reason it refuses it."""

    hw: Path
    rev: str
    version: str
    decision: str
    target_rel: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    copies: dict[str, Copy] = field(default_factory=dict)
    manufacturing: dict[str, dict[str, Any]] = field(default_factory=dict)
    bundles: list[dict[str, str]] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)
    considered: set[str] = field(default_factory=set)  # bundle paths already selected or refused
    acceptance: dict[str, Any] | None = None  # the manifest's acceptance block, once the evidence is accepted
    # the release time, fixed once: the manifest records it and a generated okh.toml must name its date
    released_at: str = ""

    def refuse(self, path: str, code: str, message: str) -> None:
        self.findings.append(validate.finding(path, message, code=f"release.{code}"))

    def copy(self, package: str, original: str, sha256: str) -> None:
        self.copies[package] = Copy(package, original, sha256)


@dataclass(frozen=True)
class Existing:
    """What hw/releases/<REV>/ holds, as `sealed` reports it: exit 0 for a sound package of this release,
    else a refusal. `repairable_from` is the release time of a stale package of this version, approval
    and frozen manifest, which a run seals again."""

    code: int
    report: dict[str, Any]
    repairable_from: datetime | None = None


# validate.py's findings on a sealed directory's own files: a listed file missing, an unlisted file, an empty
# directory. A repair replaces the whole stale package, so it leaves out these findings inside that package.
SEAL_FINDINGS = frozenset({"layout.missing", "layout.unlisted", "layout.empty_directory"})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _yaml(path: Path) -> Any:
    try:
        return yaml_loader.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _dict(value: object) -> dict[str, Any]:
    """`value` when it is a JSON/YAML object, else an empty one."""
    return value if isinstance(value, dict) else {}


def snapshot_inventory(record: Path, manifest: dict[str, Any]) -> dict[str, str]:
    """The record snapshot a version sealed: each file below its record/ by name, with the sha256 its
    manifest inventory seals (the snapshot's own bytes for a manifest without an inventory)."""
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else None
    if files is not None:
        return {name[len("record/"):]: sha for name, sha in files.items() if name.startswith("record/")}
    return {path.relative_to(record).as_posix(): sha256(path) for path in sorted(record.rglob("*")) if path.is_file()}


def _domain(name: str, data: Any) -> Any:
    """A record document without its workflow state (WORKFLOW_KEYS)."""
    if isinstance(data, dict):
        return {key: value for key, value in data.items() if key not in WORKFLOW_KEYS.get(name, ())}
    return data


def _structured(name: str) -> bool:
    """A record document compared by content; frozen sources (manufacturing/sources/**) by exact bytes."""
    return name.endswith((".yaml", ".yml")) and not name.startswith(f"{evidence.SOURCES_DIR}/")


def check_record(release: Release, record: Path, manifest: dict[str, Any]) -> None:
    """The current record against the frozen snapshot, by the snapshot set the version itself sealed:
    the current record files (layout.snapshot_names for the current part numbers) are exactly the
    files it snapshotted; a structured document has the same content, workflow state aside; every
    other file, such as a frozen source, has the exact bytes the version sealed; and each part model
    is the one it built."""
    hw, version = release.hw, release.version
    pns = sorted(path.parent.name for path in (hw / "parts").glob("*/part.yaml") if path.is_file())
    snapshot = snapshot_inventory(record, manifest)
    try:
        current = set(layout.snapshot_names(hw, pns))
    except layout.LayoutError as error:
        release.refuse("hw/parts", "changed", str(error))
        return
    _check_names(release, record, current, snapshot)
    for name in sorted(current & set(snapshot)):
        if _structured(name):
            mine = _yaml(hw / name)
            same = mine is not None and _domain(name, mine) == _domain(name, _yaml(record / name))
        else:
            same = sha256(hw / name) == snapshot[name]
        if not same:
            release.refuse(f"hw/{name}", "changed", f"hw/{name} differs from {version}/record/{name}, the frozen snapshot")
    _check_models(release, manifest)
    release.checks["record"] = "pass"


def _check_names(release: Release, record: Path, current: set[str], snapshot: dict[str, str]) -> None:
    """The record file names of hw/ against those the version sealed: none new, none removed, none reserved. A shipped
    guideline copy (generated by the build, absent from hw/) must still match the sha256 the version sealed for it."""
    version = release.version
    for name in sorted(current):
        if name.startswith(SHIPPED_COPIES):  # shipped-- names belong to the copies a build writes; a project file never carries one
            release.refuse(f"hw/{name}", "reserved", f"hw/{name} uses a name reserved for the shipped guideline sets a build copies")
    for name in sorted(current - set(snapshot)):
        release.refuse(f"hw/{name}", "changed", f"hw/{name} is new since {version} was frozen")
    for name in sorted(set(snapshot) - current):
        if name.startswith(SHIPPED_COPIES):  # a shipped guideline set copied in when the version was built; hw/ holds no such file
            if not (record / name).is_file() or sha256(record / name) != snapshot[name]:
                release.refuse(f"hw/{name}", "stale", f"{version}/record/{name} differs from the sha256 the version sealed for it; produce it again")
            continue
        release.refuse(f"hw/{name}", "changed", f"hw/{name} was removed after {version} was frozen")


def _check_models(release: Release, manifest: dict[str, Any]) -> None:
    """Each part model of hw/ is the one the version built."""
    hw, version = release.hw, release.version
    built = parts if isinstance(parts := manifest.get("parts"), dict) else {}
    models = {path.parent.name for path in (hw / "parts").glob("*/model.py") if path.is_file()}
    for pn in sorted(models | set(built)):
        model = hw / "parts" / pn / "model.py"
        source = (built.get(pn) or {}).get("source_sha256")
        if not model.is_file() or source is None or sha256(model) != source:
            release.refuse(f"hw/parts/{pn}/model.py", "changed", f"hw/parts/{pn}/model.py is not the model {version} built")


def snapshot_parts(record: Path) -> dict[str, dict[str, Any]]:
    parts = {}
    for path in sorted((record / "parts").glob("*/part.yaml")):
        data = _yaml(path)
        if isinstance(data, dict):
            parts[path.parent.name] = data
    return parts


def route_kinds(part: dict[str, Any]) -> list[str] | None:
    """The route kinds of a part record's `intent.route` in order, or None while the route is unknown."""
    route = (part.get("intent") or {}).get("route") if isinstance(part.get("intent"), dict) else None
    if not isinstance(route, list) or not route or not all(isinstance(op, dict) and op.get("kind") for op in route):
        return None
    return [op["kind"] for op in route]


def check_manufacturing(release: Release, folder: Path, manifest: dict[str, Any], parts: dict[str, dict[str, Any]],
                        quantities: dict[str, int]) -> None:
    """Every custom-made part of the frozen snapshot has its complete process route and its process
    outputs, sealed in the version (with the per-component outputs of a multi-solid part) and passing
    every blocking manufacturing check; a check its producer marks `blocking: false` is a diagnostic
    (a legacy heuristic such as a build-volume fit), never a pass or a failure. A catalogue buy has none."""
    version = release.version
    made = _dict(manifest.get("manufacturing"))
    sealed = _dict(manifest.get("files"))
    for pn, part in sorted(parts.items()):
        if not layout.custom_made(part):
            continue
        directory = layout.manufacturing_dir(release.hw, version, pn)
        rel = layout.relative(release.hw, directory)
        inside = directory.relative_to(folder).as_posix()
        try:
            policy = layout.manufacture_policy(part)
        except layout.LayoutError as error:
            release.refuse(rel, "failed", f"{pn}: {error}")
            continue
        entry = made.get(pn)
        if not isinstance(entry, dict):
            release.refuse(rel, "missing", f"{version} has no manufacturing output for custom-made {pn}; "
                                           "build a new version with cad-model run")
            continue
        missing = [name for name in policy.outputs if f"{inside}/{name}" not in sealed]
        for name in missing:
            release.refuse(f"{rel}/{name}", "missing", f"{rel}/{name} is missing; a {part.get('process')} part needs "
                                                       f"{', '.join(policy.outputs)}")
        sidecar = _json(directory / "manufacturing.json")
        rows = [entry] + ([sidecar] if isinstance(sidecar, dict) else [])
        failed = sorted({name for row in rows for name, check in (row.get("checks") or {}).items()
                         if isinstance(check, dict) and check.get("status") != "pass" and check.get("blocking") is not False}
                        | {name for row in rows for name in row.get("blocking_failures") or []})
        if failed or any(row.get("status") != "pass" or row.get("complete") is not True for row in rows):
            release.refuse(f"{rel}/manufacturing.json", "failed",
                           f"{pn} manufacturing is not a complete pass" + (f": {', '.join(failed)} failed" if failed else ""))
        elif isinstance(sidecar, dict) and list(sidecar.get("outputs") or []) != list(policy.outputs):
            release.refuse(f"{rel}/manufacturing.json", "stale", f"{pn} manufacturing.json lists outputs "
                                                                 f"{sidecar.get('outputs')}, not {list(policy.outputs)}")
        quantity = quantities.get(pn, 0)
        if quantity < 1:
            release.refuse(f"hw/versions/{version}/record/assembly.yaml", "missing",
                           f"custom-made {pn} has no occurrence in the frozen assembly")
        route = route_kinds(part)
        if route is None:
            release.refuse(f"hw/versions/{version}/record/parts/{pn}/part.yaml", "route",
                           f"{pn} has no complete recorded route (intent.route): a release names every process of a "
                           "custom-made part")
        if missing or route is None:
            continue
        files = []
        for name in policy.outputs:
            files.append(f"manufacturing/{pn}/{name}")
            release.copy(files[-1], f"{rel}/{name}", sealed[f"{inside}/{name}"])
        components: dict[str, list[str]] = {}
        prefix = f"{inside}/components/"
        for name, sha in sorted(sealed.items()):
            if name.startswith(prefix) and name.count("/") == prefix.count("/") + 1:
                component = name[len(prefix):].split("/", 1)[0]
                package = f"manufacturing/{pn}/components/{name[len(prefix):]}"
                components.setdefault(component, []).append(package)
                release.copy(package, f"hw/versions/{version}/{name}", sha)
        release.manufacturing[pn] = {"process": part["process"], "quantity": quantity, "files": files, "route": route,
                                     **({"components": components} if components else {})}
        release.checks[f"manufacturing/{pn}"] = "pass"


def select_bundle(release: Release, kind: str, subject: str, version_sha: str, why: str) -> None:
    """A required bundle of the frozen version: sealed, unchanged, bound to that version and passing."""
    hw = release.hw
    directory = layout.bundle_dir(hw, kind, release.version, subject)
    rel = layout.relative(hw, directory)
    if rel in release.considered:
        return
    release.considered.add(rel)
    if not (directory / MANIFEST).is_file():
        release.refuse(rel, "missing", f"{rel} is missing: {why}")
        return
    if reason := layout.stale(directory):
        release.refuse(rel, "stale", reason)
        return
    manifest = _json(directory / MANIFEST)
    if manifest.get("layout") != f"bundle.{kind}" or manifest.get("path") != rel:
        release.refuse(rel, "stale", f"stale: manifest.json names {manifest.get('layout')} at {manifest.get('path')}")
        return
    if manifest.get("version_manifest_sha256") != version_sha:
        release.refuse(rel, "stale", f"{rel} is not bound to the frozen {release.version} manifest; produce it again")
        return
    status = manifest.get("status")
    if status not in ("pass", NOT_ASSESSED):  # a producer that assessed nothing blocks nothing, and is never recorded as a pass
        release.refuse(rel, "failed", f"{rel} records status {status!r}, not pass")
        return
    manifest_sha = sha256(directory / MANIFEST)
    release.bundles.append({"layout": f"bundle.{kind}", "path": rel, "manifest_sha256": manifest_sha})
    release.checks[f"{kind}/{subject}"] = status
    package = f"{kind}/{subject}"
    found = layout.classify(f"{release.target_rel}/{package}/{MANIFEST}")
    if found is None or found.entry.id != "release.package":
        return  # a review bundle (renders, captures): selected by its manifest sha256, not copied
    for name, sha in sorted(manifest["files"].items()):
        release.copy(f"{package}/{name}", f"{rel}/{name}", sha)
    release.copy(f"{package}/{MANIFEST}", f"{rel}/{MANIFEST}", manifest_sha)


def check_evidence(release: Release, rows: list[dict[str, str]], version_sha: str) -> None:
    """Every hw/ path a verification row cites exists; a cited bundle or version is the frozen version's."""
    for index, row in enumerate(rows, start=2):
        where = f"hw/verification.csv:{index}"
        for token in EVIDENCE_PATH.findall(row.get("evidence") or ""):
            rel = token.rstrip(".").rstrip("/")
            sealing = validate.sealed_owner(rel)
            if sealing is not None and "V" in sealing[1]:
                entry, params = sealing
                if params["V"] != release.version:
                    release.refuse(where, "stale", f"{row.get('requirement_id')} evidence {rel} belongs to "
                                                   f"{params['V']}, not the frozen {release.version}")
                    continue
                if entry.manifest == "bundle":
                    select_bundle(release, entry.id.split(".", 1)[1], params["S"], version_sha,
                                  f"{row.get('requirement_id')} cites it as evidence")
            try:
                present = layout.locate(release.hw, rel).exists()
            except layout.LayoutError:
                present = False
            if not present:
                release.refuse(where, "missing", f"{row.get('requirement_id')} evidence {rel} does not exist")
        release.checks[f"verification/{row.get('requirement_id')}"] = row.get("result") or ""


def check_bom(release: Release, rows: list[dict[str, str]], quantities: dict[str, int],
              parts: dict[str, dict[str, Any]]) -> None:
    """bom.csv rolls up to the frozen assembly: each part's total is its occurrence count."""
    levels: list[int | None] = []
    for row in rows:
        try:
            levels.append(int(row["level"]))
        except (TypeError, ValueError, KeyError):
            levels.append(None)
    totals = validate.bom_totals(rows, levels)
    for pn in sorted(set(quantities) | (set(totals) & set(parts))):
        if totals.get(pn, 0) != quantities.get(pn, 0):
            release.refuse("hw/bom.csv", "stale", f"bom.csv gives {pn} a quantity of {totals.get(pn, 0)}; the frozen "
                                                  f"{release.version} assembly places it {quantities.get(pn, 0)} times")
    release.checks["bom"] = "pass"


def check_docs(release: Release) -> None:
    """A build_guide.py okh.toml in hw/docs/ describes this release, since the package copies it: the
    frozen version, this revision and the release date. A hand-written okh.toml is the author's."""
    rel = "hw/docs/okh.toml"
    try:
        fields = generated_docs.okh_release(release.hw / "docs" / "okh.toml")
    except ValueError as error:
        release.refuse(rel, "docs", f"{rel} cannot be read: {error}")
        return
    expected = {"version": release.version, "release": release.rev, "release_date": release.released_at[:10]}
    if fields is not None and fields != expected:
        release.refuse(rel, "docs", f"{rel} describes release {fields['release']!r} of {fields['version']!r} dated "
                                    f"{fields['release_date']!r}, not release {release.rev} of {release.version} "
                                    f"dated {expected['release_date']}; regenerate it with build_guide.py hw --version "
                                    f"{release.version} --rev {release.rev} --date {expected['release_date']} "
                                    "--out hw/docs")
    release.checks["docs"] = "pass"


def check_acceptance(release: Release, frozen: Any) -> None:
    """The intent acceptance of a record version 2 release, a domain apart from output integrity: the
    freeze's evidence_sha256 is the acceptance digest of the current inventory, and the evidence
    assessment of the frozen version passes the release gate of the use case's service class. The
    inventory is copied under acceptance/ and the scope, claims, waivers and open obligations become the
    manifest's acceptance block. Unknown never passes; a waiver stays waived."""
    hw = release.hw
    if not isinstance(frozen, dict):
        return  # assemble already refused the missing anchor
    if "evidence_sha256" not in frozen:
        release.refuse("hw/product.yaml", "frozen", "flow.frozen anchors no evidence_sha256 (a freeze from before record "
                       "version 2): freeze again, recording `evidence.py digest` with a decision the user approves")
        return
    try:
        assessment, packets = evidence.build_assessment(hw, release.version,
                                                        today=datetime.fromisoformat(release.released_at[:10]).date())
        inventory, digest = evidence.version_acceptance(hw, release.version, assessment.record, packets)
    except (evidence.EvidenceError, layout.LayoutError, OSError, ValueError) as error:
        release.refuse("hw/product.yaml", "acceptance", f"the acceptance inventory cannot be formed: {error}")
        return
    if assessment.record.version != intent.RECORD_VERSION:
        return  # validate.py reports record.version_unsupported, which refuses the release
    if digest != frozen["evidence_sha256"]:
        release.refuse("hw/product.yaml", "acceptance", f"flow.frozen.evidence_sha256 is not the acceptance digest as it "
                       f"stands ({digest}): the evidence changed after the freeze; freeze again or open a change")
    blocking = [item for item in assessment.gate("release") if item.severity == "error"]
    for item in blocking:
        release.findings.append(validate.finding(item.path, f"{item.code}: {item.message}", code="release.acceptance"))
    if blocking:
        return
    policy = assessment.release()
    release.acceptance = {"evidence_sha256": digest, "inventory": inventory, "scope": policy.scope,
                          "claims": {req: list(levels) for req, levels in policy.claims.items()},
                          "waivers": [dict(waiver) for waiver in policy.waivers],
                          "open_obligations": list(policy.open_obligations)}
    for path, sha in inventory.items():
        release.copy(f"acceptance/{path.removeprefix('hw/')}", path, sha)
    release.checks["acceptance"] = "waived" if policy.trial else "pass"


def project_findings(hw: Path, target_rel: str, repaired_on: date | None) -> list[dict[str, Any]]:
    """validate.py's blocking findings on the project. `repaired_on` is the release date of the stale
    package at `target_rel` when the run repairs it (inspection found it stale and of this release), else
    None. A repair judges waivers on that date, and leaves out the SEAL_FINDINGS inside that package;
    every other finding, including the same kinds anywhere else, stays."""
    findings = list(validate.blocking(validate.validate_project(hw, today=repaired_on)))
    if repaired_on is None:
        return findings
    package = layout.locate(hw, target_rel)
    return [item for item in findings
            if item.get("code") not in SEAL_FINDINGS or not Path(item["path"]).is_relative_to(package)]


def assemble(hw: Path, rev: str, version: str, decision: str,
             now: datetime | None = None, repairing: bool = False) -> tuple[Release, dict[str, Any] | None]:
    """Every check and the copy list, without writing anything, for a release at `now` (the current UTC
    time when None). `repairing` when the run replaces the stale package at the revision (see
    project_findings). LayoutError for a malformed argument."""
    target_rel = layout.load().path("release.package", {"REV": rev})
    layout.version_dir(hw, version)
    moment = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    release = Release(hw, rev, version, decision, target_rel, released_at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"))
    decision_path = layout.decision_file(hw, decision)
    decision_rel = layout.relative(hw, decision_path or layout.locate(hw, "hw/decisions"))
    record, missing = validate.load_decision(hw, decision)
    if missing is not None:
        release.refuse(decision_rel, "decision", f"{decision} {missing}; record the release approval")
    elif (problem := validate.not_an_approval(record)) is not None:
        release.refuse(decision_rel, "decision",
                       f"{decision} {problem}; record the user's release approval as a decision with "
                       "`status: accepted`")
    product = _yaml(hw / "product.yaml")
    flow = value if isinstance(product, dict) and isinstance(value := product.get("flow"), dict) else {}
    frozen = flow.get("frozen")
    if not isinstance(frozen, dict):
        release.refuse("hw/product.yaml", "frozen", "product.yaml has no flow.frozen; freeze a version first")
    elif frozen.get("version") != version:
        release.refuse("hw/product.yaml", "frozen", f"flow.frozen names {frozen.get('version')}, not {version}")
    release.findings.extend(project_findings(hw, target_rel, moment.date() if repairing else None))
    check_docs(release)
    try:
        _, folder, record = layout.built_version(hw, version)
    except layout.LayoutError as error:
        release.refuse(f"hw/versions/{version}", "version", str(error))
        return release, None
    manifest = _json(folder / MANIFEST)
    version_sha = sha256(folder / MANIFEST)  # validate.py's check_flow holds flow.frozen to it
    check_record(release, record, manifest)

    parts = snapshot_parts(record)
    assembly = _yaml(record / "assembly.yaml")
    snapshot_findings: list[dict[str, Any]] = []
    records = {pn: (part, f"hw/versions/{version}/record/parts/{pn}/part.yaml") for pn, part in parts.items()}
    inventory = validate.assembly_inventory(assembly, f"hw/versions/{version}/record/assembly.yaml", records,
                                            snapshot_findings)
    if inventory is not None:
        # a version's attach entries are cad-model's `--attach REF=PARENT_REF` options; a PN endpoint
        # with no occurrence is its identity singleton, an `@id` endpoint an occurrence already listed
        attached = [ref for spec in manifest.get("attach") or [] for ref in spec.split("=", 1) if not ref.startswith("@")]
        inventory = inventory.extended(attached)
    quantities = inventory.quantities() if inventory is not None else {}
    release.findings.extend(snapshot_findings)
    check_manufacturing(release, folder, manifest, parts, quantities)

    for pn, part in sorted(parts.items()):
        if part.get("drawing") is not None:
            select_bundle(release, "drawings", pn, version_sha, f"{pn} declares drawing intent")
    if isinstance(assembly, dict) and assembly.get("drawing") is not None:
        select_bundle(release, "drawings", "assembly", version_sha, "assembly.yaml declares an assembly drawing")
    for case in sorted((hw / "analysis" / "cases").glob("*/case.yaml")):
        case_id = layout.analysis_case(hw, case)
        if case_id is None:
            continue  # validate.py reports a case folder outside the layout
        select_bundle(release, "analysis", case_id, version_sha, f"analysis case {case_id} has no result for {version}")

    verification = validate.read_csv(hw / "verification.csv", validate.CSV_COLUMNS["verification.csv"], release.findings)
    validate.check_release_readiness(hw, verification, records, "hw/verification.csv", release.findings)
    check_evidence(release, verification, version_sha)
    bom = validate.read_csv(hw / "bom.csv", validate.CSV_COLUMNS["bom.csv"], release.findings)
    check_bom(release, bom, quantities, parts)
    check_acceptance(release, frozen)

    sealed = manifest.get("files") or {}
    if "assembly.step" in sealed:
        release.copy("definition/assembly.step", f"hw/versions/{version}/assembly.step", sealed["assembly.step"])
    else:
        release.refuse(f"hw/versions/{version}/assembly.step", "missing", f"{version} has no assembly.step")
    for name, sha in sorted(sealed.items()):
        if name.startswith("record/"):
            release.copy(f"definition/{name}", f"hw/versions/{version}/{name}", sha)
    for pn, part in sorted(parts.items()):
        specification = (part.get("procurement") or {}).get("specification") or {}
        if isinstance(specification.get("artifact"), str):
            artifact = posixpath.normpath(specification["artifact"])
            release.copy(f"definition/{artifact}", f"hw/{artifact}", specification.get("sha256", ""))
    for name, path in (("bom.csv", hw / "bom.csv"), ("verification.csv", hw / "verification.csv")):
        if path.is_file():
            release.copy(name, f"hw/{name}", sha256(path))
    docs = hw / "docs"
    for path in sorted(docs.rglob("*")) if docs.is_dir() else []:
        if path.is_file() and not path.is_symlink():
            release.copy(f"docs/{path.relative_to(docs).as_posix()}", layout.relative(hw, path), sha256(path))
    # validate.py and the release rules can report one fact twice; keep the first
    release.findings = list({(item["path"], item["message"]): item for item in release.findings}.values())
    return release, {"product_id": product.get("id") if isinstance(product, dict) else None, "frozen": frozen}


def stage(release: Release, facts: dict[str, Any]) -> Path:
    """Byte copies plus manifest.json in a new folder under .cad-work/release-<REV>/; findings when a
    copy does not hold what its original was checked to hold."""
    hw = release.hw
    root = hw.parent / layout.SCRATCH / f"release-{release.rev}"
    root.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix="stage-", dir=root))
    files = {}
    for package, item in sorted(release.copies.items()):
        found = layout.classify(f"{release.target_rel}/{package}")
        if found is None or found.entry.id != "release.package":
            release.refuse(item.original, "layout", f"{package} is not a path release.package holds")
            continue
        target = staged / package
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(layout.locate(hw, item.original), target)
        except OSError as error:
            release.refuse(item.original, "missing", f"cannot copy {item.original}: {error.strerror}")
            continue
        copied = sha256(target)
        if copied != item.sha256:
            release.refuse(item.original, "stale", f"stale: {item.original} changed since it was checked")
        files[package] = {"sha256": copied, "original": item.original}
    if release.acceptance is None:
        release.refuse("hw/product.yaml", "acceptance", "the release has no accepted evidence to seal")
    manifest = {
        "schema_version": SCHEMA_VERSION, "record_version": intent.RECORD_VERSION, "product_id": facts["product_id"],
        "rev": release.rev, "released_at": release.released_at,
        "frozen": facts["frozen"], "approval": release.decision, "files": files,
        "manufacturing": release.manufacturing, "bundles": release.bundles, "checks": release.checks,
        "acceptance": release.acceptance,
    }
    path = staged / MANIFEST
    validate.validate_document(manifest, "release-manifest.schema.json", layout.relative(hw, path), release.findings)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for problem in layout.verify(staged):
        release.refuse(layout.relative(hw, staged), "stale", f"stale: {problem}")
    return staged


def package_rel(rev: str) -> str:
    """The project-relative path of release `rev`'s package, hw/releases/<REV>; LayoutError for a bad REV."""
    return str(layout.load().path("release.package", {"REV": rev}))


def run(hw: Path, rev: str, version: str, decision: str, now: datetime | None = None) -> tuple[int, dict[str, Any]]:
    """Release revision `rev` (release_locked), holding the revision's lock (layout.target_lock) for the
    whole run, so a second run of the same revision waits for this one and then inspects what it left."""
    hw = hw.resolve()
    with layout.target_lock(hw, package_rel(rev)):
        return release_locked(hw, rev, version, decision, now)


def prune_release(hw: Path, rev: str, version: str, decision: str) -> tuple[int, dict[str, Any]]:
    """--prune's release step, holding the revision's lock for the whole step: an existing package must
    already be sealed (`sealed`; a stale one refuses, and --prune never repairs it), and an absent
    revision is released (release_locked)."""
    hw = hw.resolve()
    with layout.target_lock(hw, package_rel(rev)):
        if os.path.lexists(layout.locate(hw, package_rel(rev))):
            return sealed(hw, rev, version, decision)
        return release_locked(hw, rev, version, decision)


def release_locked(hw: Path, rev: str, version: str, decision: str,
                   now: datetime | None = None) -> tuple[int, dict[str, Any]]:
    """The release run of revision `rev`; the caller holds the revision's lock (run, prune_release). It
    inspects an existing package, assembles and stages, then publishes (publish_package)."""
    target_rel = package_rel(rev)
    found = existing(hw, rev, version, decision) if os.path.lexists(layout.locate(hw, target_rel)) else None
    if found is not None and found.repairable_from is None:
        return found.code, found.report  # the same frozen manifest releases again as a no-op, or a refusal
    repairing = found is not None  # a stale package of this release: sealed again at its own release time
    release, facts = assemble(hw, rev, version, decision, found.repairable_from if found else now, repairing)
    report: dict[str, Any] = {"rev": rev, "version": version, "package": release.target_rel}
    if release.findings or facts is None:
        return 1, {**report, "findings": release.findings, "status": "refused"}
    staged = stage(release, facts)
    report["stage"] = layout.relative(hw, staged)
    if release.findings:
        return 1, {**report, "findings": release.findings, "status": "refused"}
    return publish_package(release, staged, report, repairing)


def publish_package(release: Release, staged: Path, report: dict[str, Any],
                    repairing: bool) -> tuple[int, dict[str, Any]]:
    """Seal `staged` at hw/releases/<REV>/; the caller holds the revision's lock (layout.target_lock), and
    what is there is inspected again here. An absent path is claimed. A sound package of this release
    stays and the run is a no-op. A stale package of this release is moved to .cad-work/replaced/ and the
    stage claimed, only when this run staged its repair. Anything else refuses as `existing` reports it.
    A run that claims nothing removes its stage."""
    hw, target_rel = release.hw, release.target_rel
    target = layout.locate(hw, target_rel)
    if not os.path.lexists(target):
        return claim_package(release, staged, report, None)
    found = existing(hw, release.rev, release.version, release.decision)
    if found.code == 0 or not repairing or found.repairable_from is None:
        shutil.rmtree(staged)  # another writer's package stands: this stage has no use
        return found.code, found.report
    old = layout.replaced_path(hw, target_rel)
    old.parent.mkdir(parents=True, exist_ok=True)
    os.rename(target, old)
    return claim_package(release, staged, report, old)


def claim_package(release: Release, staged: Path, report: dict[str, Any],
                  old: Path | None) -> tuple[int, dict[str, Any]]:
    """layout.claim the stage onto hw/releases/<REV>/. When that fails, the stale package moved to `old`
    goes back (put_back), and the failure stands: a PublishConflict refuses, any other is a LayoutError."""
    hw, target_rel = release.hw, release.target_rel
    target = layout.locate(hw, target_rel)
    try:
        layout.claim(staged, target, target_rel)
    except BaseException as error:
        kept = put_back(hw, old, target, target_rel) if old is not None else ""
        if isinstance(error, layout.PublishConflict):
            release.refuse(target_rel, "exists", f"{error}{kept}; rerun to compare it with the frozen manifest")
            return 1, {**report, "findings": release.findings, "status": "refused"}
        if isinstance(error, (layout.LayoutError, OSError)):
            raise layout.LayoutError(f"{error}{kept}") from error
        raise
    return 0, {**report, "stage": None, "manifest_sha256": sha256(target / MANIFEST), "files": len(release.copies),
               "manufacturing": release.manufacturing, "bundles": release.bundles,
               **({"replaced": layout.relative(hw, old)} if old is not None else {}), "status": "released"}


def put_back(hw: Path, old: Path, target: Path, target_rel: str) -> str:
    """Move the stale package at `old` back to `target` after a failed claim, by an exclusive claim of
    its own, so a package another writer placed there meanwhile is never replaced. Says where it is."""
    try:
        layout.claim(old, target, target_rel)
    except (layout.LayoutError, OSError) as error:
        return (f"; the stale package it replaced is kept at {layout.relative(hw, old)}, since putting it back "
                f"failed: {error}")
    return "; the stale package it replaced is back in place"


def acceptance_problems(manifest: dict[str, Any]) -> list[str]:
    """Why a sealed version 3 package's acceptance does not hold by its own bytes: an inventory file
    without its exact sealed copy under acceptance/, a digest that is not the inventory's, or a frozen
    anchor that is not that digest. Mutable hw/ files are never consulted."""
    acceptance = _dict(manifest.get("acceptance"))
    inventory = _dict(acceptance.get("inventory"))
    files = _dict(manifest.get("files"))
    problems = []
    for path, sha in sorted(inventory.items()):
        copy = files.get(f"acceptance/{path.removeprefix('hw/')}")
        if not isinstance(copy, dict) or copy.get("sha256") != sha or copy.get("original") != path:
            problems.append(f"its acceptance inventory names {path}, which it does not seal under acceptance/")
    if not inventory or evidence.acceptance_digest(inventory) != acceptance.get("evidence_sha256"):
        problems.append("its acceptance evidence_sha256 is not the digest of its sealed inventory")
    frozen = _dict(manifest.get("frozen"))
    if frozen.get("evidence_sha256") != acceptance.get("evidence_sha256"):
        problems.append("its frozen anchor is not its acceptance digest")
    return problems


def frozen_problems(hw: Path, version: str, manifest: dict[str, Any]) -> list[str]:
    """Why a sealed package's frozen manifest sha is not the built `version`'s manifest as it is now:
    the version fails its own manifest, or the package was sealed from another one."""
    try:
        current = sha256(layout.built_version(hw, version)[1] / MANIFEST)
    except layout.LayoutError as error:
        return [str(error)]
    if _dict(manifest.get("frozen")).get("manifest_sha256") != current:
        return [f"it was sealed from another frozen {version} manifest"]
    return []


def holds_problems(version: str, decision: str, manifest: Any) -> list[str]:
    """Why a package manifest is not manifest version 3 of `version` approved by `decision`."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        schema = manifest.get("schema_version") if isinstance(manifest, dict) else None
        return [f"its manifest version {schema!r} is not one this release.py reads"]
    frozen = _dict(manifest.get("frozen"))
    if frozen.get("version") != version or manifest.get("approval") != decision:
        return [f"it holds {frozen.get('version')} approved by {manifest.get('approval')}"]
    return []


def released_at(manifest: dict[str, Any]) -> datetime | None:
    """The UTC release time a package manifest records, or None when it records none."""
    try:
        return datetime.strptime(str(manifest.get("released_at")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def stale_package(hw: Path, version: str, decision: str, package: Path, reason: str,
                  report: dict[str, Any]) -> Existing:
    """A package that fails its seal (`reason`), refused as release.stale. It is repairable from its own
    release time when its manifest still holds `version` approved by `decision` from the frozen manifest
    as it is now; otherwise the revision is used up."""
    manifest = _json(package / MANIFEST)
    problems = holds_problems(version, decision, manifest) or frozen_problems(hw, version, manifest)
    moment = None if problems else released_at(manifest)
    remedy = ("automatic repair is not supported; use a new revision" if moment is None
              else "release.py without --prune repairs it from the same frozen manifest")
    finding = validate.finding(report["package"], f"{reason}; {remedy}", code="release.stale")
    return Existing(1, {**report, "findings": [finding], "status": "refused"}, moment)


def existing(hw: Path, rev: str, version: str, decision: str) -> Existing:
    """hw/releases/<REV>/ as it stands. Exit 0 when it verifies and holds `version` approved by `decision`
    from the frozen manifest as it is now, so a rerun rewrites nothing. A package must be manifest version
    3, and also verifies its sealed acceptance bytes and anchor. A package that fails its seal is a
    stale_package. Anything else, such as another frozen manifest or manifest version, is refused."""
    target_rel = layout.load().path("release.package", {"REV": rev})
    package = layout.locate(hw, target_rel)
    report: dict[str, Any] = {"rev": rev, "version": version, "package": target_rel}
    if not package.is_dir() or package.is_symlink():
        problems = ["it is not a directory"]
    elif reason := layout.stale(package):
        return stale_package(hw, version, decision, package, reason, report)
    else:
        manifest = _json(package / MANIFEST)
        problems = (holds_problems(version, decision, manifest) or acceptance_problems(manifest)
                    or frozen_problems(hw, version, manifest))
    if problems:
        finding = validate.finding(target_rel, f"{target_rel} exists but is not the sealed release of {version} approved "
                                               f"by {decision}: {'; '.join(problems)}; automatic repair is not supported; "
                                               "use a new revision",
                                   code="release.exists")
        return Existing(1, {**report, "findings": [finding], "status": "refused"})
    return Existing(0, {**report, "already_sealed": True, "manifest_version": SCHEMA_VERSION,
                        "manifest_sha256": sha256(package / MANIFEST), "status": "released"})


def sealed(hw: Path, rev: str, version: str, decision: str) -> tuple[int, dict[str, Any]]:
    """The exit code and report of `existing` for hw/releases/<REV>/, which --prune needs sealed already: a
    stale package refuses here, and a run without --prune repairs it when it is repairable."""
    found = existing(hw.resolve(), rev, version, decision)
    return found.code, found.report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("hw", type=Path, help="the record's hw/ directory")
    parser.add_argument("--rev", required=True, help="the release revision letter, A to Y without I, O, Q, S, X")
    parser.add_argument("--version", required=True, help="the frozen version, as product.yaml flow.frozen names it")
    parser.add_argument("--decision", required=True, help="DR-### approving this release, in hw/decisions/")
    parser.add_argument("--prune", action="store_true",
                        help="once REV is sealed (now, or already), print the plan of superseded versions and its digest")
    parser.add_argument("--yes", metavar="DIGEST",
                        help="apply the --prune plan with this digest; refused when a fresh plan differs")
    args = parser.parse_args(argv)
    if args.yes is not None and not args.prune:
        parser.error("--yes DIGEST requires --prune")
    if args.yes is not None and not re.fullmatch(r"[0-9a-f]{64}", args.yes):
        parser.error("--yes takes the 64-character digest that --prune printed")
    if not args.hw.is_dir():
        print(json.dumps({"message": f"{args.hw} is not a directory", "status": "error"}))
        return 2
    try:
        if args.prune:
            code, report = prune_release(args.hw, args.rev, args.version, args.decision)
        else:
            code, report = run(args.hw, args.rev, args.version, args.decision)
    except layout.LayoutError as error:
        print(json.dumps({"message": str(error), "status": "error"}))
        return 2
    if code == 0 and args.prune:
        try:
            pruning = prune.after_release(args.hw, args.rev, args.version, args.yes)
        except layout.LayoutError as error:
            pruning = {"message": str(error), "status": "refused"}
        code = 0 if pruning["status"] in ("plan", "pruned") else 1
        status = "released" if code == 0 else f"prune_{pruning['status']}"
        report = {**{key: value for key, value in report.items() if key != "status"}, "pruning": pruning, "status": status}
    print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - one JSON report for any infrastructure failure
        print(json.dumps({"message": f"infrastructure error: {error}", "status": "error"}))
        raise SystemExit(2)
