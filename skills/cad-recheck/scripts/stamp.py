#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["pyyaml>=6.0", "jsonschema>=4.23", "referencing>=0.28.4"]
# ///
"""Write or verify the CAD design recheck record for the staged index.

A stamp records the staged hw/ tree hash, the stage and the evidence packet `evidence.py check` wrote
for it (cad-record), or the packet of an `evidence.py run` report (a run that stopped has none): its
expected checks, each one's status and the digest of the record inputs it
read. Only a record version 2 record stamps; a record of another version refuses at or after the
threshold. A packet that is not the assessment of the staged record, lacks an
expected check or is partial never stamps; `--passed` stamps only a packet whose stage gate, recomputed
here from its statuses and from the staged record's selection report (cad-record selection.py, with
the digest of the staged selection inputs), passes, so a stale, failed or unknown due check, or a
recorded final choice the selection does not support, cannot be stamped as passed.
`--verify` reads the staged index: the record inputs, the stamp and its packet, never the work tree
beyond the layout audit. It also refuses a project-start profile (cad-record flow_profile_gate) that blocks at
the staged flow.stage, judged on the index's product, parts and decision records, before the threshold check below. It is the whole commit-time check: a commit that stages no hw/ change passes.
`--hw` names the hw/ directory, as in evidence.py (default: hw/ at the git root). `--verify --committed` checks HEAD's
hw/ tree and stamp instead of the index, and never skips: CI runs it on a clean checkout.
flow.recheck_from is the threshold and may only stay or move earlier than
HEAD's: raising it would suppress the checks of the stages it skips.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def cad_record_root() -> Path:
    installed = Path(__file__).resolve().parent / "cad-record"
    skill_tree = Path(__file__).resolve().parents[2] / "cad-record"
    for candidate in (installed, skill_tree):
        if (candidate / "scripts" / "validate.py").is_file():
            return candidate
    raise RuntimeError(f"cannot find cad-record/scripts/validate.py; checked {installed} and {skill_tree}")


CAD_RECORD_ROOT = cad_record_root()
CAD_RECORD_SCRIPTS = CAD_RECORD_ROOT / "scripts"


def _load_module(name: str, path: Path) -> Any:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]

layout = _load_module("cad_record_layout", CAD_RECORD_SCRIPTS / "layout.py")
validate = _load_module("cad_record_validate", CAD_RECORD_SCRIPTS / "validate.py")
yaml_loader = validate.yaml_loader  # cad-record's shared YAML reader: yaml.safe_load on libyaml
intent = validate.intent
evidence = validate.evidence


def tree_blob(root: Path, path: str, committed: bool) -> bytes:
    """The content of `path` (relative to the project) in the index, or in HEAD when `committed`."""
    return git("show", f"{'HEAD' if committed else ''}:./{path}", cwd=root)


def tree_paths(root: Path, committed: bool) -> list[str]:
    """The paths below hw/ in the index, or in HEAD when `committed`."""
    if committed:
        raw = git("ls-tree", "-r", "-z", "--name-only", "HEAD", "--", "hw/", cwd=root)
    else:
        raw = git("ls-files", "-z", "--", "hw/", cwd=root)
    return [name.decode("utf-8", errors="surrogateescape") for name in raw.split(b"\0") if name]


def tree_decisions(root: Path, committed: bool, folder: Path) -> None:
    """Write the hw/decisions/DR-###*.md records of the index (HEAD when `committed`) into `folder`, so cad-record's
    decision reader can read them without the work tree."""
    folder.mkdir(parents=True, exist_ok=True)
    for path in tree_paths(root, committed):
        name = path.removeprefix("hw/decisions/")
        if path.startswith("hw/decisions/") and "/" not in name and name.startswith("DR-") and name.endswith(".md"):
            (folder / name).write_bytes(tree_blob(root, path, committed))


def tree_parts(root: Path, committed: bool) -> dict[str, Any]:
    """The parsed hw/parts/<PN>/part.yaml records of the index (HEAD when `committed`), by the PN folder."""
    found = {}
    for path in tree_paths(root, committed):
        pieces = path.split("/")
        if len(pieces) == 4 and pieces[:2] == ["hw", "parts"] and pieces[3] == "part.yaml":
            try:
                found[pieces[2]] = yaml_loader.safe_load(tree_blob(root, path, committed).decode("utf-8"))
            except (UnicodeError, yaml.YAMLError) as error:
                raise RuntimeError(f"cannot parse staged {path}: {error}") from error
    return found


def require_profile(root: Path, committed: bool, product: dict[str, Any]) -> None:
    """Refuse a tree whose project-start profile blocks at its flow.stage (cad-record flow_profile_gate). The product
    is the index's (HEAD's when `committed`), and so are the parts and the decision the profile cites: the work tree
    is never read."""
    gate = validate.profile_gate
    with tempfile.TemporaryDirectory() as scratch:
        tree_decisions(root, committed, Path(scratch) / "decisions")
        _stage, found = gate.gate(product, tree_parts(root, committed),
                                  functools.partial(validate.approval_problem, Path(scratch)))
    if found:
        raise RuntimeError(gate.refusal(found))


class GateFinding(RuntimeError):
    """A named field blocks verification of the stage advance."""


def _stamp_utc_day(stamp_time: Any) -> date:
    try:
        stamped = datetime.fromisoformat(stamp_time)
    except (TypeError, ValueError) as error:
        raise GateFinding("timestamp: the recheck stamp needs an ISO timestamp with a time zone") from error
    if stamped.utcoffset() is None:
        raise GateFinding("timestamp: the recheck stamp has no time zone offset")
    try:
        return stamped.astimezone(timezone.utc).date()
    except OverflowError as error:
        raise GateFinding("timestamp: the recheck stamp is outside the supported UTC date range") from error


def _matching_gate_date(records: list[Any], version: str, digest: str, stamped_on: date) -> None:
    versions = [r for r in records if r.gate_build and r.gate_build[0] == version]
    if not versions:
        raise GateFinding(f"gate.version: no decision for the checked version {version!r}")
    builds = [r for r in versions if r.gate_build[1] == digest]
    if not builds:
        raise GateFinding("gate.version_manifest_sha256: no decision for the checked version's manifest digest")
    if all(r.date > stamped_on for r in builds):
        raise GateFinding(f"{builds[0].id} date: gate decision is after the stamp's UTC date {stamped_on}")


def _checked_build(root: Path, committed: bool, checks: dict[str, Any]) -> tuple[str, str]:
    version = checks.get("version")
    if not isinstance(version, str):
        raise GateFinding("version: the evidence packet must name a built version")
    try:
        path = f"{layout.load().path('version.core', {'V': version})}/{layout.MANIFEST}"
    except layout.LayoutError as error:
        raise GateFinding("version: the evidence packet must name a built version") from error
    try:
        digest = hashlib.sha256(tree_blob(root, path, committed)).hexdigest()
    except RuntimeError as error:
        where = "HEAD" if committed else "the index"
        raise GateFinding(f"version_manifest_sha256: {path} is missing from {where}; track the manifest even if the "
                          "other build results are ignored") from error
    if checks.get("version_manifest_sha256") != digest:
        raise GateFinding(f"version_manifest_sha256: the evidence packet does not match {path}; check the staged build again")
    return version, digest


def require_gate_decision(root: Path, committed: bool, previous: str, advanced: str,
                          stamp: dict[str, Any], checks: dict[str, Any]) -> None:
    """Require an unsuperseded decision for this transition and the build checked by the evidence packet."""
    decision_records = validate.decision_records
    wanted = f"gate: {{from: {previous}, to: {advanced}}}"
    stamped_on = _stamp_utc_day(stamp.get("timestamp"))
    with tempfile.TemporaryDirectory() as scratch:
        tree_decisions(root, committed, Path(scratch))
        try:
            records = decision_records.read_decisions(Path(scratch))
        except decision_records.DecisionRecordError as error:
            raise GateFinding(f"hw/decisions: {str(error).replace(scratch + '/', '')}") from error
    candidates = [r for r in records if r.gate == (previous, advanced) and r.status in ("proposed", "accepted")
                  and not r.superseded_by]
    if not candidates:
        raise RuntimeError(f"flow.stage advanced past the stamped stage (gate {previous} → {advanced}) without a decision "
                           "record of the gate. Record the decision in hw/decisions as a proposed (provisional) or accepted "
                           f"record with `{wanted}` in its front matter and no `superseded-by`.")
    if stamp.get("version") != checks.get("version"):
        raise GateFinding("version: the recheck stamp does not match its evidence packet")
    version, digest = _checked_build(root, committed, checks)
    _matching_gate_date(candidates, version, digest, stamped_on)


def staged_inputs(root: Path, committed: bool = False) -> dict[str, bytes]:
    """The staged record inputs (cad-record evidence.is_record_input) by path below hw/, read from the index
    (from HEAD when `committed`)."""
    return {path[3:]: tree_blob(root, path, committed) for path in tree_paths(root, committed)
            if path.startswith("hw/") and evidence.is_record_input(path[3:])}


@dataclass(frozen=True)
class Staged:
    """The staged index's normalized record, the digest of its record inputs (evidence.record_digest) and
    the digest of its selection inputs (selection.input_digest)."""

    record: Any
    record_sha256: str
    selection_sha256: str


def staged_record(root: Path, committed: bool = False) -> Staged:
    """The normalized record of the staged index and the digests of its record and selection inputs."""
    files = staged_inputs(root, committed)
    documents = {}
    for rel, data in files.items():
        if intent.schema_for(rel) is None:
            continue
        try:
            documents[rel] = yaml_loader.safe_load(data.decode("utf-8"))
        except (UnicodeError, yaml.YAMLError) as error:
            raise RuntimeError(f"cannot parse staged hw/{rel}: {error}") from error
    return Staged(intent.with_guideline_files(intent.normalize_record(documents), files), evidence.record_digest(files),
                  evidence.selection.input_digest(documents))


def require_current_version(record: Any) -> None:
    problem = intent.version_problem(record.product)
    if problem is not None:
        raise RuntimeError(f"{problem.code}: {problem.message}. {problem.action or ''}".strip())


def require_threshold_kept(root: Path, threshold: str) -> None:
    """flow.recheck_from may stay or move earlier than HEAD's, never later."""
    try:
        head = yaml_loader.safe_load(git("show", "HEAD:./hw/product.yaml", cwd=root).decode("utf-8"))
    except (RuntimeError, UnicodeError, yaml.YAMLError):
        return  # no commit or no product.yaml yet: nothing to raise it from
    before = (head.get("flow") or {}).get("recheck_from") if isinstance(head, dict) and isinstance(head.get("flow"), dict) else None
    if before in validate.stages() and validate.stages().index(threshold) > validate.stages().index(before):
        raise RuntimeError(f"staged flow.recheck_from {threshold} is later than HEAD's {before}: the threshold may only stay "
                           "or move earlier, since raising it skips the checks of the stages in between")


def require_evidence(packet: Any, staged: Staged, stage: str) -> list[str]:
    """Refuse a packet that is not the evidence of the staged record at `stage`; return its blocking gate
    findings, the obligation and selection gates recomputed from the staged record."""
    problems = evidence.packet_problems(packet, record=staged.record, record_sha256=staged.record_sha256, stage=stage)
    if problems:
        raise RuntimeError("the evidence packet cannot stamp the staged record: " + "; ".join(problems))
    blocking: list[str] = evidence.packet_blocking(packet, record=staged.record, stage=stage,
                                                   selection_sha256=staged.selection_sha256)
    return blocking




def staged_product(root: Path, stage: str | None = None) -> dict[str, Any]:
    """Read the index version of product.yaml to grade layout paths at the staged stage."""
    try:
        raw = git("show", ":./hw/product.yaml", cwd=root).decode("utf-8")
        product = yaml_loader.safe_load(raw)
    except (RuntimeError, UnicodeError, yaml.YAMLError) as error:
        if stage is None:
            raise RuntimeError(f"cannot read staged hw/product.yaml: {error}") from error
        product = {"flow": {"stage": stage}}
    return product if isinstance(product, dict) else {}


def require_clean_layout(root: Path, product: dict[str, Any]) -> None:
    part_records = {}
    for part_path in sorted((root / "hw" / "parts").glob("*/part.yaml")):
        try:
            part = yaml_loader.safe_load(part_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise RuntimeError(f"cannot read {part_path}: {error}") from error
        if isinstance(part, dict) and isinstance(part.get("PN"), str):
            part_records[part["PN"]] = (part, str(part_path))
    findings: list[dict[str, str]] = []
    validate.check_layout(root / "hw", product, part_records, findings)
    blocking = validate.blocking(findings)
    if blocking:
        first = blocking[0]
        raise RuntimeError(f"CAD layout has {len(blocking)} blocking finding(s): {first['path']}: {first['message']}")

def git(*args: str, cwd: Path) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, check=False
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed ({result.returncode})")
    return result.stdout


def project_root(start: Path, hw: Path | None) -> Path:
    """The directory holding hw/: the parent of `hw` (relative to `start`) when given, else the git root of `start`.

    `hw` is the hw/ directory itself, as in evidence.py. Every git call runs in the project, so the paths git
    prints and the `:./` index paths are relative to it."""
    if hw is None:
        return Path(git("rev-parse", "--show-toplevel", cwd=start).decode().strip())
    hw_dir = (start / hw).resolve()
    if hw_dir.name != "hw" or not hw_dir.is_dir():
        raise RuntimeError(f"--hw must name an hw directory (for example examples/lamp-arm/hw); got {hw}")
    git("rev-parse", "--show-toplevel", cwd=hw_dir)  # refuses a directory outside a git work tree
    return hw_dir.parent


def tree_entries(root: Path, committed: bool) -> bytes:
    """The entries of hw/ as `git ls-files -s` prints them, from the index or, when `committed`, from HEAD."""
    if not committed:
        return git("ls-files", "-s", "--", "hw/", cwd=root)
    lines = []
    for line in git("ls-tree", "-r", "HEAD", "--", "hw/", cwd=root).splitlines(keepends=True):
        meta, separator, path = line.partition(b"\t")
        mode, _, sha = meta.replace(b" blob ", b" ").partition(b" ")
        lines.append(mode + b" " + sha + b" 0" + separator + path)
    return b"".join(lines)


def staged_tree_hash(root: Path, committed: bool = False) -> str:
    """Hash index entries for hw/, except the generated recheck metadata. With `committed` they come from HEAD."""
    entries = tree_entries(root, committed)
    digest = hashlib.sha256()
    for entry in entries.splitlines(keepends=True):
        _, separator, path = entry.partition(b"\t")
        if not separator or path.startswith(b"hw/.cad/"):
            continue
        digest.update(entry)
    return digest.hexdigest()


def command_version(command: str, *args: str) -> str | None:
    executable = shutil.which(command)
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, *args], capture_output=True, text=True, check=False, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    version = (result.stdout or result.stderr).strip().splitlines()
    return version[0] if version else None


def tool_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for name, command, args in (
        ("git", "git", ("--version",)),
        ("uv", "uv", ("--version",)),
        ("freecad", "freecadcmd", ("--version",)),
    ):
        version = command_version(command, *args)
        if version:
            versions[name] = version
    return versions


def load_checks(path: Path) -> Any:
    try:
        checks = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read checks JSON {path}: {error}") from error
    if isinstance(checks, dict) and checks.get("kind") == evidence.RUN_KIND:  # an `evidence.py run` report
        packet = checks.get("packet")
        if not isinstance(packet, dict):
            raise RuntimeError(f"the evidence run stopped at {checks.get('stopped_at')} ({checks.get('status')}): it has no "
                               "evidence packet; fix the cause and run it again")
        return packet
    return checks


def require_complete(checks: Any, path: Path) -> None:
    """Refuse a partial check result, such as cad-model `check --changed-only` (`scope.complete: false`)."""
    scope = checks.get("scope") if isinstance(checks, dict) else None
    if isinstance(scope, dict) and scope.get("complete") is False:
        raise RuntimeError(f"{path} is a partial check ({scope.get('kind', 'partial')} scope, complete: false); "
                           "run the full check (cad_model.py check vN without --changed-only, --since-version or --pair) "
                           "and stamp that result")


def record(stage: str, checks_path: Path, passed: bool, root: Path) -> dict[str, Any]:
    if stage not in validate.stages():
        raise RuntimeError(f"unknown stage {stage!r}; expected one of: {', '.join(validate.stages())}")
    require_clean_layout(root, staged_product(root, stage))
    checks = load_checks(checks_path)
    require_complete(checks, checks_path)
    staged = staged_record(root)
    require_current_version(staged.record)
    blocking = require_evidence(checks, staged, stage)
    if passed and blocking:
        raise RuntimeError(f"--passed contradicts the evidence packet: {len(blocking)} blocking finding(s) at {stage}: "
                           + "; ".join(blocking[:3]) + (" …" if len(blocking) > 3 else ""))
    return {
        "tree_hash": staged_tree_hash(root),
        "stage": stage,
        "record_sha256": staged.record_sha256,
        "expected_sha256": checks["expected_sha256"],
        "version": checks.get("version"),
        "checks": checks,
        "passed": passed,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "tool_versions": tool_versions(),
    }


def write_record(data: dict[str, Any], root: Path) -> Path:
    directory = root / "hw" / ".cad"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "recheck.json"
    temporary = directory / ".recheck.json.tmp"
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=validate.stages(), help="stage for a newly written recheck stamp")
    parser.add_argument("--checks", type=Path, help="JSON check results")
    parser.add_argument("--passed", action="store_true", help="record that every required check passed")
    parser.add_argument("--hw", type=Path, help="the hw/ directory, inside the repository (default: hw/ at the git root); "
                        "the stamp and every path read or written are under it")
    parser.add_argument("--verify", action="store_true", help="verify the staged stamp against the current staged hash")
    parser.add_argument("--committed", action="store_true",
                        help="with --verify: check HEAD's hw/ tree and stamp, even when nothing is staged (for CI)")
    args = parser.parse_args()
    if args.committed and not args.verify:
        parser.error("--committed needs --verify")
    if args.verify:
        if args.stage is not None or args.checks is not None or args.passed:
            parser.error("--verify cannot be combined with --stage, --checks, or --passed")
        return args
    elif args.stage is None or args.checks is None:
        parser.error("--stage and --checks are required unless --verify is used")
    return args


def main() -> int:
    args = parse_args()
    try:
        start = Path.cwd()
        root = project_root(start, args.hw)
        if args.verify:
            committed = args.committed
            # A commit that stages no hw/ change needs no stamp. --committed checks HEAD instead, so it never skips.
            if not committed and not git("diff", "--cached", "--name-only", "--diff-filter=ACDMRTUXB", "--", "hw/", cwd=root).strip():
                print(json.dumps({"passed": True, "required": False, "reason": "no staged hw/ change"}))
                return 0
            try:
                product_data = tree_blob(root, "hw/product.yaml", committed).decode("utf-8")
            except (RuntimeError, UnicodeError) as error:
                raise RuntimeError(f"cannot read staged hw/product.yaml: {error}") from error
            try:
                product = yaml_loader.safe_load(product_data)
            except yaml.YAMLError as error:
                raise RuntimeError(f"cannot parse staged hw/product.yaml: {error}") from error
            if not isinstance(product, dict):
                raise RuntimeError("staged hw/product.yaml must be a mapping")
            flow = product.get("flow")
            if not isinstance(flow, dict):
                raise RuntimeError("staged hw/product.yaml is missing flow")
            current_stage = flow.get("stage")
            threshold = flow.get("recheck_from")
            if not isinstance(current_stage, str) or current_stage not in validate.stages():
                raise RuntimeError("staged hw/product.yaml has a missing or unknown flow.stage")
            if not isinstance(threshold, str) or threshold not in validate.stages():
                raise RuntimeError("staged hw/product.yaml has a missing or unknown flow.recheck_from")
            require_clean_layout(root, product)
            if not committed:
                require_threshold_kept(root, threshold)
            require_profile(root, committed, product)
            if validate.stages().index(current_stage) < validate.stages().index(threshold):
                print(json.dumps({"passed": True, "required": False, "stage": current_stage}))
                return 0
            staged = staged_record(root, committed)
            require_current_version(staged.record)
            try:
                record_data = json.loads(tree_blob(root, "hw/.cad/recheck.json", committed).decode("utf-8"))
            except (RuntimeError, UnicodeError, json.JSONDecodeError) as error:
                raise RuntimeError(f"no staged hw/.cad/recheck.json exists or cannot read it: {error}") from error
            if not isinstance(record_data, dict):
                raise RuntimeError("recheck stamp must be a JSON object")
            actual_hash = staged_tree_hash(root, committed)
            order = validate.stages()
            stamped_stage = record_data.get("stage")
            if not isinstance(stamped_stage, str) or stamped_stage not in order:
                raise RuntimeError("recheck stamp has an unknown stage")
            # A provisional gate advances flow.stage past the gate just passed, so the stamp may sit one stage behind.
            if order.index(stamped_stage) not in (order.index(current_stage), order.index(current_stage) - 1):
                raise RuntimeError("the recheck stamp stage does not match staged hw/product.yaml flow.stage")
            if order.index(stamped_stage) < order.index(threshold):
                raise RuntimeError(f"the recheck stamp stage {stamped_stage} is below flow.recheck_from {threshold}")
            if record_data.get("passed") is not True:
                raise RuntimeError("the recheck stamp does not say passed: true")
            if record_data.get("tree_hash") != actual_hash:
                raise RuntimeError("the recheck stamp is stale for the staged hw/ tree")
            checks = record_data.get("checks")
            blocking = require_evidence(checks, staged, stamped_stage)
            stamped = checks if isinstance(checks, dict) else {}
            if stamped_stage != current_stage:
                require_gate_decision(root, committed, stamped_stage, current_stage, record_data, stamped)
            if record_data.get("record_sha256") != staged.record_sha256 or \
                    record_data.get("expected_sha256") != stamped.get("expected_sha256"):
                raise RuntimeError("the recheck stamp does not record the evidence of the staged record; stamp it again")
            if blocking:
                raise RuntimeError(f"the stamped evidence has {len(blocking)} blocking finding(s) at {stamped_stage}: "
                                   + "; ".join(blocking[:3]))
            print(json.dumps({"passed": True, "tree_hash": actual_hash, "expected_sha256": record_data["expected_sha256"]}))
            return 0
        checks_path = args.checks if args.checks.is_absolute() else start / args.checks
        data = record(args.stage, checks_path, args.passed, root)
        write_record(data, root)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except GateFinding as error:
        print(f"cad-recheck stamp: {error}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as error:
        print(f"cad-recheck stamp: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
