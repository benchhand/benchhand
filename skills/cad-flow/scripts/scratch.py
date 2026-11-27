#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = []
# ///
"""Scratch workspace of one cad-flow worker unit: copy hw/, build and check the copy, deliver the unit's parts.

Usage:
    uv run --script scratch.py init    --hw HW --unit UNIT --part PN [--part PN ...] [--reset]
    uv run --script scratch.py build   --hw HW --unit UNIT --fidelity F0|F1|F2|F3 [--kernel auto|cadquery|build123d]
                                       [--attach REF=PARENT_REF ...]
    uv run --script scratch.py deliver --hw HW --unit UNIT
    uv run --script scratch.py sync    --hw HW --unit UNIT

The workspace is <project>/.cad-work/<UNIT>/, <project> being the directory that holds HW.

init    copies HW into <workspace>/hw/ without versions/, renders/, releases/, .git/, .cad/ (the
        part store, the pair cache, run records and the recheck stamp), the version-bound bundles
        <kind>/vN/ (checks, drawings, captures, motion, analysis) and __pycache__/, then
        records the unit's PNs and the sha256 of every copied file in <workspace>/baseline.json.
        It seeds the scratch part store with hard links to HW's stored objects and build records,
        so the scratch build reuses every unchanged part. An existing workspace is kept unless
        --reset replaces it.
build   runs cad-model `run` on the scratch hw/ under the next scratch label, cad-model `check`
        on that version, and cad-design form_check at --fidelity. HW itself is never passed to
        any of them, so HW/versions/ is neither created nor changed. Form findings are split
        into the unit's own PNs and the rest; only the unit's own blocking findings block it. A
        finding form_check marks `blocking: false` is a diagnostic (a legacy heuristic such as a
        minimum wall or block_like proposal): reported as own_diagnostics, never a pass or a
        failure, since manufacturability comes from the declared route's checks: form_check runs
        as `--version`, which also publishes the process packet
        in the scratch hw (form.process); a failed process check of the unit's own part blocks it,
        an unknown one is reported (own_unknown). Each --attach is
        passed to cad-model `run` and recorded in the scratch version, so a unit builds and
        checks against the same rigid attachments as the lead's integrated build.
deliver copies each unit PN's scratch hw/parts/<PN>/ over HW/parts/<PN>/ and lists every other
        scratch file that differs from the baseline as a proposal for the lead. When any
        HW/parts/<PN>/ changed since init it writes nothing and exits 1. A regenerated project
        output (bom.csv) is listed as a proposal with "regenerated": true and never blocks delivery:
        the lead regenerates it from the integrated record. Generated
        versions/, renders/, releases/, version-bound bundles (such as the hw/checks/vN/cad packet
        a scratch check publishes) and .cad/ output and every __pycache__/ are ignored
        during delivery, so a scratch build's own caches and evidence never reach HW.

sync    brings the lead's current record into the workspace: a part the lead retired (its directory is
        gone from HW) is removed, another unit's delivered part or the lead's edit to a shared file
        is copied in. Each synced file also takes the lead's hash in baseline.json, so deliver never
        carries it as the unit's change. A file the unit and the lead both changed, and any file of
        a part the unit owns, and a file that would collide with a directory (or the reverse) once
        applied, stays as it is and is listed under "conflicts" (exit 1). It reads the lead's
        record twice and refuses (exit 2, nothing written) when the two reads differ, when a delivery is
        staged in HW/parts/, or when a scratch path is a symlink. It never writes HW,
        and it links stored builds of newly delivered parts into the scratch store.
        Run it before build whenever the lead has integrated a delivery or a decision.

stdout: one JSON document; every path in it is absolute. build writes each tool's diagnostics to
<workspace>/<tool>-<label>.log and names the log in the JSON, so its output stays small.
Exit codes: 0 pass; 1 build: a part failed to build, a blocking check failed, or the unit's
parts have blocking form findings; deliver: HW/parts/<PN>/ changed since init; sync: conflicts; 2 usage or
infrastructure error.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, cast

SKILLS = Path(__file__).resolve().parents[2]
CAD_MODEL_SCRIPTS = SKILLS / "cad-model" / "scripts"
CAD_RECORD_SCRIPTS = SKILLS / "cad-record" / "scripts"
FORM_CHECK = SKILLS / "cad-design" / "scripts" / "form_check.py"
sys.path.insert(0, str(CAD_MODEL_SCRIPTS))
from cadmodel import scriptenv  # noqa: E402 - standard library only
CAD_MODEL = CAD_MODEL_SCRIPTS / "cad_model.py"
WORK_DIR = ".cad-work"
# .cad/ holds only caches and control files (cad-record layout cache.*, control.*): the part store is as
# large as every version together, and the scratch build writes its own pair cache and run records there.
NOT_COPIED = frozenset({"versions", "renders", "releases", ".git", ".cad"})
UNIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
PN = re.compile(r"^[A-Z]{2,5}-[0-9]{4}$")
LABEL = re.compile(r"^v([1-9][0-9]*)$")


def load_layout() -> Any:
    name = "cad_record_layout"
    if name not in sys.modules:
        path = CAD_RECORD_SCRIPTS / "layout.py"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"layout.py not found at {path}")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


def load_emit() -> Any:
    """cad-record's output emitter, for the env of the tools whose JSON this script parses."""
    name = "cad_record_emit"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, CAD_RECORD_SCRIPTS / "emit.py")
        assert spec is not None and spec.loader is not None
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


emit = load_emit()


layout = load_layout()
# hw/<kind>/vN/: every version-bound bundle (cad-record layout bundle.*: checks, drawings, renders, captures,
# motion, analysis) is generated output bound to a version; case definitions beside them (motion/cases/,
# analysis/cases/) are record inputs and stay.
BUNDLE_ROOTS = frozenset(layout.bundle_kinds())


class UsageError(Exception):
    """Bad arguments or missing workspace; exit 2 and make no delivery writes."""


def workspace(hw: Path, unit: str) -> Path:
    if not UNIT.match(unit) or ".." in unit:
        raise UsageError(f"unit {unit!r} must match {UNIT.pattern} without '..'")
    if not (hw / "parts").is_dir():
        raise UsageError(f"{hw} is not a design record: no parts/ directory")
    return hw.resolve().parent / WORK_DIR / unit  # resolved, so a link below it is the only link open_dir can meet


def is_skipped(relative: PurePosixPath, top: frozenset[str]) -> bool:
    """Whether a path relative to the copied root is left out: `top` directly below it, a version-bound
    bundle folder (<kind>/vN/), and __pycache__ anywhere."""
    parts = relative.parts
    return (parts[0] in top or "__pycache__" in parts
            or (len(parts) > 1 and parts[0] in BUNDLE_ROOTS and LABEL.match(parts[1]) is not None))


def skips(root: Path, top: frozenset[str] = NOT_COPIED) -> Callable[[str, list[str]], set[str]]:
    """A copytree `ignore` that leaves out what `is_skipped` names."""
    def ignore(directory: str, names: list[str]) -> set[str]:
        below = PurePosixPath(Path(directory).relative_to(root).as_posix())
        return {name for name in names if is_skipped(below / name, top)}
    return ignore


def tree_hashes(root: Path, top: frozenset[str] = NOT_COPIED) -> dict[str, str]:
    """sha256 per file below `root`, keyed by POSIX path relative to it, skipping what `skips` leaves out."""
    hashes = {}
    for path in sorted(root.rglob("*")):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if is_skipped(relative, top) or not path.is_file():
            continue
        hashes[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def descend(root_fd: int, names: Sequence[str], create: bool = False) -> int:
    """A new descriptor for the directory `names` below the open directory `root_fd`, opened one component
    at a time with O_NOFOLLOW. A symlink on the way is refused; `create` makes missing directories."""
    fd = os.dup(root_fd)
    try:
        for name in names:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(name, 0o777, dir_fd=fd)
            try:
                below = os.open(name, DIR_FLAGS, dir_fd=fd)
            except OSError as error:
                if error.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise UsageError(f"scratch contains a symlink or a non-directory at {name}") from error
                raise
            os.close(fd)
            fd = below
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_dir(path: Path) -> int:
    """A descriptor for the absolute directory `path`, every component opened without following links,
    so a linked ancestor of a workspace is refused too. `path` comes from a resolved HW."""
    root = os.open("/", DIR_FLAGS)
    try:
        return descend(root, path.parts[1:])
    finally:
        os.close(root)


def write_file(dir_fd: int, name: str, data: bytes, mode: int = 0o644) -> None:
    """Publish `data` as `name` in the open directory: an exclusive temporary file, then a rename over
    the name, which replaces a link at `name` instead of following it."""
    staged = f".{name}.sync-{os.getpid()}-{secrets.token_hex(4)}"
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
        os.replace(staged, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(staged, dir_fd=dir_fd)
        raise


def read_file(dir_fd: int, name: str) -> bytes:
    """The bytes of `name` in the open directory, refusing a link at `name`."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise UsageError(f"scratch contains a symlink at {name}") from error
        raise
    with os.fdopen(fd, "rb") as stream:
        return stream.read()


def load_baseline(work: Path, work_fd: int | None = None) -> dict[str, Any]:
    owned = work_fd is None
    fd = open_dir(work) if work_fd is None else work_fd
    try:
        try:
            raw = read_file(fd, "baseline.json")
        except FileNotFoundError:
            raise UsageError(f"{work} has no baseline.json; run `scratch.py init` first") from None
        return cast(dict[str, Any], json.loads(raw.decode("utf-8")))
    finally:
        if owned:
            os.close(fd)


def write_baseline(work_fd: int, baseline: dict[str, Any]) -> None:
    write_file(work_fd, "baseline.json", (json.dumps(baseline, indent=2) + "\n").encode("utf-8"))


def seed_store(hw: Path, scratch_hw: Path) -> int:
    """Hard-link HW's part store (cad-record layout cache.part_objects, cache.part_records) into the
    scratch copy and return how many files were linked. The store's writers only rename onto a name and
    never write a file in place, so no scratch write reaches HW through a link; objects stay read-only.
    Where no hard link can be made (another filesystem) seeding stops, and the scratch build builds the
    rest itself; nothing is copied, because the store can be as large as every version together."""
    lay, seeded = layout.load(), 0
    scratch_fd = open_dir(scratch_hw)
    try:
        for entry_id in ("cache.part_objects", "cache.part_records"):
            folder = layout.locate(hw, str(PurePosixPath(lay.entry(entry_id).path).parent))
            for path in sorted(folder.iterdir()) if folder.is_dir() else []:
                match = lay.classify(layout.relative(hw, path))
                if match is None or match.entry.id != entry_id or path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(hw)
                dest_fd = descend(scratch_fd, relative.parent.parts, create=True)
                try:
                    if (scratch_hw / relative).exists():
                        continue
                    os.link(path, relative.name, dst_dir_fd=dest_fd, follow_symlinks=False)
                except OSError:
                    return seeded
                finally:
                    os.close(dest_fd)
                seeded += 1
    finally:
        os.close(scratch_fd)
    return seeded


def init(hw: Path, unit: str, parts: list[str], reset: bool) -> tuple[int, dict[str, Any]]:
    bad = [pn for pn in parts if not PN.match(pn)]
    if not parts or bad:
        raise UsageError(f"--part needs one or more PNs matching {PN.pattern}; got {bad or 'none'}")
    work = workspace(hw, unit)
    if work.exists() and not reset:
        baseline = load_baseline(work)
        return 0, {"status": "exists", "unit": unit, "workspace": str(work), "scratch_hw": str(work / "hw"),
                   "parts": baseline["parts"], "note": "kept the existing workspace; --reset replaces it"}
    shutil.rmtree(work, ignore_errors=True)
    work.parent.mkdir(parents=True, exist_ok=True)
    gitignore = work.parent / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    shutil.copytree(hw, work / "hw", ignore=skips(hw))
    seeded = seed_store(hw, work / "hw")
    for pn in sorted(set(parts)):
        if not (hw / "parts" / pn).exists():
            (work / "hw" / "parts" / pn).mkdir(parents=True, exist_ok=True)
    baseline = {"unit": unit, "hw": str(hw), "parts": sorted(set(parts)), "files": tree_hashes(work / "hw")}
    (work / "baseline.json").write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    return 0, {"status": "created", "unit": unit, "workspace": str(work), "scratch_hw": str(work / "hw"),
               "parts": baseline["parts"], "files": len(baseline["files"]), "seeded_store_files": seeded}


STAGED_DELIVERY = re.compile(r"^\.[A-Z]{2,5}-[0-9]{4}\.(?:deliver|replaced)-[0-9]+$")


def refuse_staged_delivery(hw: Path) -> None:
    """deliver stages a part as hw/parts/.<PN>.deliver-<pid> and renames the old one to .<PN>.replaced-<pid>;
    while either exists a part directory can be missing, which a scan would read as a retirement."""
    parts = hw / "parts"
    staged = sorted(path.name for path in parts.iterdir() if STAGED_DELIVERY.match(path.name)) if parts.is_dir() else []
    if staged:
        raise UsageError(f"delivery in progress: {', '.join(staged)} in {parts}; run sync again when it finishes")


def lead_snapshot(hw: Path) -> dict[str, str]:
    """sha256 per file of the lead's record, read twice around two staged-delivery checks. A delivery in
    progress, or any change between the two reads, refuses the sync instead of syncing a mixed state."""
    refuse_staged_delivery(hw)
    first = delivery_tree(hw)[0]
    refuse_staged_delivery(hw)
    if delivery_tree(hw)[0] != first:
        raise UsageError(f"{hw} changed while sync read it; run sync again when the lead's edits are done")
    return first


def copy_bytes(source: Path, dir_fd: int, name: str) -> str:
    """Install the lead's `source` as `name` in the open scratch directory and return the sha256 of the
    bytes actually written."""
    with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        data, mode = stream.read(), os.fstat(stream.fileno()).st_mode & 0o777
    write_file(dir_fd, name, data, mode)
    return hashlib.sha256(data).hexdigest()


def drop_collisions(plan: list[str], present: set[str], lead: dict[str, str], conflicts: list[str]) -> list[str]:
    """The planned paths that can be applied together: a file and a directory may not share a name once
    the plan is applied to the files in `present`. A planned path that would collide is moved to
    `conflicts`, and the check repeats because dropping one change can leave another colliding."""
    while True:
        final = (present - {path for path in plan if path not in lead}) | {path for path in plan if path in lead}
        clashing = {path for path in final if any(parent.as_posix() in final for parent in PurePosixPath(path).parents[:-1])}
        clashing |= {parent.as_posix() for path in clashing for parent in PurePosixPath(path).parents[:-1] if parent.as_posix() in final}
        dropped = [path for path in plan if path in clashing]
        if not dropped:
            return plan
        conflicts.extend(dropped)
        plan = [path for path in plan if path not in clashing]


def remove_synced(scratch_fd: int, path: str) -> None:
    """Delete a path the lead retired, then the directories that leaves empty (never the scratch hw)."""
    names = path.split("/")
    try:
        parent = descend(scratch_fd, names[:-1])
    except FileNotFoundError:
        return
    try:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(names[-1], dir_fd=parent)
    finally:
        os.close(parent)
    for depth in range(len(names) - 1, 0, -1):
        above = descend(scratch_fd, names[:depth - 1])
        try:
            os.rmdir(names[depth - 1], dir_fd=above)
        except OSError:
            break
        finally:
            os.close(above)


def install_synced(hw: Path, scratch_fd: int, path: str, lead_digest: str, mine_digest: str | None) -> str:
    """Put the lead's file in the scratch copy unless it already matches; return the digest of what is there."""
    if mine_digest == lead_digest:
        return lead_digest
    names = path.split("/")
    parent = descend(scratch_fd, names[:-1], create=True)
    try:
        return copy_bytes(hw / path, parent, names[-1])
    finally:
        os.close(parent)


def sync(hw: Path, unit: str) -> tuple[int, dict[str, Any]]:
    """Bring the lead's current record into the workspace: parts the lead retired, other units' deliveries
    and the lead's edits to shared files. A file the unit has not touched takes the lead's version and
    the baseline takes it too, so deliver sees no change. A file both sides changed, and any file of
    a part the unit owns, is left alone and reported as a conflict."""
    work = workspace(hw, unit)
    work_fd = open_dir(work)
    try:
        return sync_workspace(hw, unit, work, work_fd)
    finally:
        os.close(work_fd)


def sync_workspace(hw: Path, unit: str, work: Path, work_fd: int) -> tuple[int, dict[str, Any]]:
    baseline = load_baseline(work, work_fd)
    if Path(baseline["hw"]) != hw:
        raise UsageError(f"{work} was initialised from {baseline['hw']}, not {hw}")
    scratch_hw = work / "hw"
    scratch_fd = descend(work_fd, ["hw"])
    try:
        own = tuple(f"parts/{pn}/" for pn in baseline["parts"])
        base: dict[str, str] = baseline["files"]
        lead, mine = lead_snapshot(hw), delivery_tree(scratch_hw)[0]
        added: list[str] = []
        modified: list[str] = []
        removed: list[str] = []
        conflicts: list[str] = []
        plan: list[str] = []
        seeded = seed_store(hw, scratch_hw)  # refuses a linked cache directory before anything else is written
        for path in sorted(set(lead) | set(base)):
            if lead.get(path) == base.get(path):
                continue
            if path.startswith(own) or (mine.get(path) != base.get(path) and mine.get(path) != lead.get(path)):
                conflicts.append(path)
                continue
            plan.append(path)
        plan = drop_collisions(plan, set(mine), lead, conflicts)
        for path in plan:
            if path not in lead:
                remove_synced(scratch_fd, path)
                removed.append(path)
                base.pop(path, None)
            else:
                (modified if path in base else added).append(path)
                base[path] = install_synced(hw, scratch_fd, path, lead[path], mine.get(path))
        write_baseline(work_fd, baseline)
    finally:
        os.close(scratch_fd)
    report = {"status": "conflict" if conflicts else "synced", "unit": unit, "scratch_hw": str(scratch_hw),
              "added": added, "modified": modified, "removed": removed, "conflicts": sorted(conflicts),
              "seeded_store_files": seeded}
    if conflicts:
        report["note"] = "left these as they are: the unit and the lead both changed them, or the lead changed a part the unit owns"
    return (1 if conflicts else 0), report


def run_tool(argv: list[str], log: Path) -> tuple[int, dict[str, Any] | None]:
    """Run one CAD tool through its PEP 723 dependency-set environment and log stderr/output."""
    try:
        with log.open("w", encoding="utf-8") as stderr:
            done = subprocess.run(scriptenv.script_command(Path(argv[0]), *argv[1:]),
                                  stdout=subprocess.PIPE, stderr=stderr, text=True, env=emit.json_env())  # this parser needs the full JSON
    except FileNotFoundError as error:
        raise UsageError("uv is not on PATH") from error
    try:
        return done.returncode, emit.unwrap(json.loads(done.stdout))
    except ValueError:
        with log.open("a", encoding="utf-8") as stderr:
            stderr.write(done.stdout)
        return done.returncode, None


def next_label(scratch_hw: Path) -> str:
    built = [int(m.group(1)) for d in (scratch_hw / "versions").glob("v*")
             if (m := LABEL.match(d.name)) and (d / "manifest.json").is_file()]
    return f"v{max(built, default=0) + 1}"


def build(hw: Path, unit: str, kernel: str, fidelity: str,
          attach: tuple[str, ...] | list[str] = ()) -> tuple[int, dict[str, Any]]:
    work = workspace(hw, unit)
    baseline = load_baseline(work)
    own = set(baseline["parts"])
    scratch_hw = work / "hw"
    label = next_label(scratch_hw)
    attachments = list(attach)
    report: dict[str, Any] = {"unit": unit, "scratch_hw": str(scratch_hw), "label": label, "parts": sorted(own),
                              "attach": attachments}

    run_log = work / f"run-{label}.log"
    attach_args = [arg for spec in attachments for arg in ("--attach", spec)]
    code, ran = run_tool([str(CAD_MODEL), "run", str(scratch_hw), "--label", label, "--kernel", kernel,
                          *attach_args], run_log)
    report["run"] = {"exit": code, "log": str(run_log), "findings": (ran or {}).get("findings", [])}
    if code != 0:
        report["status"] = "fail" if code == 1 else "error"
        return (1 if code == 1 else 2), report
    version = scratch_hw / "versions" / label
    report.update({"version_dir": str(version), "parts_dir": str(version / "parts"),
                   "metrics": str(version / "metrics.json"), "assembly_step": str(version / "assembly.step")})

    check_out = work / f"check-{label}.json"
    check_log = work / f"check-{label}.log"
    code, checked = run_tool([str(CAD_MODEL), "check", label, "--hw", str(scratch_hw), "--out", str(check_out)], check_log)
    report["check"] = {"exit": code, "json": str(check_out), "log": str(check_log),
                       "blocking_failures": (checked or {}).get("blocking_failures"),
                       "warnings": (checked or {}).get("warnings")}

    # form_check --version runs the same fidelity checks and the process-dispatched manufacturing checks of
    # the declared routes, publishing the process packet in the scratch hw only.
    form_out = work / f"form-{label}.json"
    form_log = work / f"form-{label}.log"
    form_code, form = run_tool([str(FORM_CHECK), "--hw", str(scratch_hw), "--version", label, "--fidelity", fidelity], form_log)
    findings = (form or {}).get("findings", [])
    if form is not None:
        form_out.write_text(json.dumps(form, indent=2) + "\n", encoding="utf-8")
    mine = [f for f in findings if f.get("part") in own]
    report["form"] = {"exit": form_code, "fidelity": fidelity, "json": str(form_out), "log": str(form_log),
                      "own_findings": [f for f in mine if f.get("blocking") is not False],
                      "own_diagnostics": [f for f in mine if f.get("blocking") is False],
                      "other_findings": [f for f in findings if f.get("part") not in own]}
    if form is not None:
        process = form.get("manufacturing") or {}
        own_open = [o for o in process.get("open") or () if _anchor_pn(o["obligation"]) in own]
        report["form"]["process"] = {"status": process.get("status"), "complete": process.get("complete"),
                                     "bundle": process.get("bundle"),
                                     "own_failures": [o for o in own_open if o["outcome"] == "fail"],
                                     "own_unknown": [o for o in own_open if o["outcome"] == "unknown"]}
    if code == 2 or form is None or form_code == 2:
        report["status"] = "error"
        return 2, report
    # A failed process check of the unit's own part blocks it; an unknown one is still exploration.
    process_failures = (report["form"].get("process") or {}).get("own_failures") or []
    passed = code == 0 and not report["form"]["own_findings"] and not process_failures
    report["status"] = "pass" if passed else "fail"
    return (0 if passed else 1), report


def _anchor_pn(obligation: str) -> str | None:
    """The PN an obligation id (family@PN[.COMP][/op][#feature]) is about, else None."""
    match = re.match(r"^[^@]+@([A-Z]{2,5}-[0-9]{4})(?![0-9])", obligation)
    return match.group(1) if match else None


def delivery_tree(root: Path) -> tuple[dict[str, str], set[str]]:
    files, directories = {}, set()
    skipped = skips(root)
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        directory = Path(current)
        ignored = skipped(current, dirnames)
        for name in list(dirnames):
            path = directory / name
            if path.is_symlink():
                raise UsageError(f"scratch contains a symlink at {path.relative_to(root)}")
            if name in ignored:
                dirnames.remove(name)
                continue
            directories.add(path.relative_to(root).as_posix())
        for name in filenames:
            path = directory / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                raise UsageError(f"scratch contains a symlink or special file at {relative}")
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files, directories


def is_record_path(path: str) -> bool:
    match = layout.classify(f"hw/{path}")
    return match is not None and match.entry.kind == "record" and match.role == "source"


def is_regenerated_output(path: str) -> bool:
    """A canonical generated project file outside parts/ (the BOM): a unit's copy is stale against the
    lead's integrated record, so deliver lists it and never writes it or blocks on it."""
    match = layout.classify(f"hw/{path}")
    return (match is not None and match.entry.kind == "generated" and match.entry.mutability == "regenerated"
            and not path.startswith("parts/"))


def validate_delivery(baseline: dict[str, Any], authorized: set[str], files: dict[str, str],
                      directories: set[str]) -> None:
    old_files: dict[str, str] = baseline["files"]
    known_parts = {path.split("/")[1] for path in old_files if path.startswith("parts/")}
    for path in files:
        if path.startswith("parts/"):
            pn = path.split("/")[1]
            if PN.fullmatch(pn) and pn not in authorized and pn not in known_parts:
                raise UsageError(f"unit is not authorized for new part {pn}; initialize it with --part {pn}")
    for directory in directories:
        if not any(path.startswith(f"{directory}/") for path in files) and not layout.load().known_directory(f"hw/{directory}"):
            raise UsageError(f"cannot deliver an unrecognized empty directory {directory}")
    for path in sorted(set(files) | set(old_files)):
        if files.get(path) != old_files.get(path):
            match = layout.classify(f"hw/{path}")
            owner = path.split("/")[1] if path.startswith("parts/") else None
            if is_regenerated_output(path):
                continue  # reported as a proposal by deliver; the lead's build regenerates it
            if match is None or match.entry.kind != "record" or match.params.get("PN") not in (None, owner):
                raise UsageError(f"cannot deliver changed non-record or non-canonical path {path}")
    for pn in authorized:
        prefix = f"parts/{pn}/"
        part_files = [path for path in files if path.startswith(prefix)]
        if not part_files or f"parts/{pn}/part.yaml" not in part_files:
            raise UsageError(f"scratch hw/parts/{pn}/ needs a canonical part.yaml before delivery")
        if any(not is_record_path(path) for path in part_files):
            raise UsageError(f"cannot deliver non-record or non-canonical part path under parts/{pn}/")


def deliver(hw: Path, unit: str) -> tuple[int, dict[str, Any]]:
    work = workspace(hw, unit)
    work_fd = open_dir(work)
    try:
        return deliver_workspace(hw, unit, work, work_fd)
    finally:
        os.close(work_fd)


def deliver_workspace(hw: Path, unit: str, work: Path, work_fd: int) -> tuple[int, dict[str, Any]]:
    baseline = load_baseline(work, work_fd)
    if Path(baseline["hw"]) != hw:
        raise UsageError(f"{work} was initialised from {baseline['hw']}, not {hw}")
    scratch_hw = work / "hw"
    files, directories = delivery_tree(scratch_hw)
    authorized = set(baseline["parts"])
    validate_delivery(baseline, authorized, files, directories)
    baseline_files: dict[str, str] = baseline["files"]
    conflicts = []
    for pn in sorted(authorized):
        then = {path: digest for path, digest in baseline_files.items() if path.startswith(f"parts/{pn}/")}
        part_dir = hw / "parts" / pn
        now = {f"parts/{pn}/{path}": digest for path, digest in tree_hashes(part_dir, frozenset()).items()} \
            if part_dir.is_dir() else {}
        if now != then:
            conflicts.append(str(part_dir))
    if conflicts:
        return 1, {"status": "conflict", "unit": unit, "changed_since_init": conflicts,
                   "note": "another actor changed these part directories; nothing was delivered"}

    delivered: list[str] = []
    for pn in sorted(authorized):
        target = hw / "parts" / pn
        staged = hw / "parts" / f".{pn}.deliver-{os.getpid()}"
        shutil.rmtree(staged, ignore_errors=True)
        source = scratch_hw / "parts" / pn
        shutil.copytree(source, staged, ignore=skips(source, frozenset()))
        if target.exists():
            retired = hw / "parts" / f".{pn}.replaced-{os.getpid()}"
            target.rename(retired)
            staged.rename(target)
            shutil.rmtree(retired)
        else:
            staged.rename(target)
        current = tree_hashes(target, frozenset())
        delivered.extend(str(target / path) for path in current)
        baseline_files = {path: digest for path, digest in baseline_files.items()
                          if not path.startswith(f"parts/{pn}/")}
        baseline_files.update({f"parts/{pn}/{path}": digest for path, digest in current.items()})
    baseline["files"] = baseline_files
    write_baseline(work_fd, baseline)
    proposals = []
    for path in sorted(set(files) | set(baseline_files)):
        if any(path.startswith(f"parts/{pn}/") for pn in authorized) or files.get(path) == baseline_files.get(path):
            continue
        change = "added" if path not in baseline_files else "removed" if path not in files else "modified"
        proposal: dict[str, Any] = {"path": str(hw / path), "scratch": str(scratch_hw / path), "change": change}
        if is_regenerated_output(path):
            proposal["regenerated"] = True
        proposals.append(proposal)
    return 0, {"status": "delivered", "unit": unit, "delivered": delivered, "proposals": proposals}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cad-flow worker scratch workspace")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "build", "deliver", "sync"):
        command = sub.add_parser(name)
        command.add_argument("--hw", type=Path, required=True, help="project hw/ record")
        command.add_argument("--unit", required=True, help="unit id from the lead's brief")
        if name == "init":
            command.add_argument("--part", action="append", default=[], metavar="PN", help="a PN this unit owns")
            command.add_argument("--reset", action="store_true", help="replace an existing workspace")
        if name == "build":
            command.add_argument("--fidelity", choices=("F0", "F1", "F2", "F3"), required=True)
            command.add_argument("--kernel", choices=("auto", "cadquery", "build123d"), default="auto")
            command.add_argument("--attach", action="append", default=[], metavar="REF=PARENT_REF",
                                 help="passed to cad-model run: rigidly attach an occurrence without a joint "
                                      "(a PN or @id; repeatable), as the lead's integrated build does")
    args = parser.parse_args(argv)
    hw = args.hw.resolve()
    try:
        if args.command == "init":
            code, report = init(hw, args.unit, args.part, args.reset)
        elif args.command == "build":
            code, report = build(hw, args.unit, args.kernel, args.fidelity, args.attach)
        elif args.command == "sync":
            code, report = sync(hw, args.unit)
        else:
            code, report = deliver(hw, args.unit)
    except (UsageError, OSError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}))
        return 2
    print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
