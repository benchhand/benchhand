# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The content-addressed store of cached outputs under hw/.cad/cache/, which holds each byte sequence once.

Objects (cad-record layout `cache.part_objects`, `hw/.cad/cache/objects/<sha256>`) are the bytes of cached files,
named by their sha256 and read-only (0444). Records (`cache.part_records`, `hw/.cad/cache/records/<key>.json`) map
the key of a computation, `layout.input_key` of its allowlisted inputs, to its members (each relative path and the
sha256 of its object) and the producer's facts. Locks (`cache.locks`) serialize the producers of one key.

A producer looks a key up with `record`. On a miss it takes `lock(key)`, looks it up once more, computes, and
`claim`s the record. `claim` takes the key's lock itself, so a producer that skipped `lock` still cannot
overwrite a record another claimer just kept. The first usable record of a key wins, and a
second writer whose members differ is a finding in `cache.findings`: the key misses an input. A stale or unreadable
record gives way to the next claim, and `retire` removes a record its producer judged unusable for a reason outside
the key. `record` re-hashes
every member on read and names why a record is stale. `place` lays a record's members down beside each other in a
fresh folder and renames that folder into place, so a reader sees the whole set or none of it. `place_object`
links one object. Every write lands beside its name and the store renames it onto that name, and every store
path is a plain file or folder. `gc` keeps what any sealed manifest under hw/ names and removes the rest, and the part store of
the former layout (hw/.cad/parts, hw/.cad/builds). Deleting the store by hand costs only recomputation, never a
version, because every version file is a link or copy of its own.

It uses only the standard library and stays compatible with Python 3.11, so a FreeCAD job could import it.
"""

from __future__ import annotations

import contextlib
import ctypes
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping

if "cad_record_layout" not in sys.modules:
    _SPEC = importlib.util.spec_from_file_location("cad_record_layout", Path(__file__).resolve().parent / "layout.py")
    if _SPEC is None or _SPEC.loader is None:
        raise ImportError("cannot load layout.py beside store.py")
    sys.modules["cad_record_layout"] = importlib.util.module_from_spec(_SPEC)  # dataclasses look the module up here
    _SPEC.loader.exec_module(sys.modules["cad_record_layout"])
layout = sys.modules["cad_record_layout"]

FORMAT = 2  # the record format: `record` reuses a record of this format only
SHA = re.compile(r"[0-9a-f]{64}")
READ_ONLY = 0o444
FORMER = (".cad/parts", ".cad/builds")  # the part store before hw/.cad/cache/, removed by gc
_RENAME_EXCHANGE = 2  # renameat2 flag (Linux)
_RENAME_SWAP = 2  # renamex_np flag (macOS)


class StoreError(RuntimeError):
    """A store operation that would risk a sealed file, such as gc with an unreadable manifest."""


@dataclass(frozen=True)
class Lookup:
    """A record found by `Store.record`: the record, or None and why (`miss: ...` or `stale: ...`)."""

    record: dict[str, Any] | None
    reason: str


def file_sha(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _signature(found: os.stat_result) -> tuple[int, int, int]:
    """An object's inode, size and mtime. Every write to its bytes changes the mtime, and a hard link changes none."""
    return found.st_ino, found.st_size, found.st_mtime_ns


def _link_or_copy(source: Path, target: Path) -> None:
    """`target` as a read-only hard link to `source`, or a read-only copy where the filesystem allows no link."""
    try:
        os.link(source, target)
    except OSError:  # another filesystem, FAT, a link limit: the same bytes, copied
        shutil.copyfile(source, target)
    os.chmod(target, READ_ONLY)


def _members(record: Mapping[str, Any]) -> dict[str, str]:
    """The record's members, each a plain relative POSIX path with an object sha256. ValueError otherwise."""
    members = record.get("members")
    if not isinstance(members, Mapping) or not members:
        raise ValueError("a record needs members: relative paths mapped to object sha256s")
    for name, digest in members.items():
        path = PurePosixPath(name) if isinstance(name, str) else None
        if (path is None or not name or path.is_absolute() or ".." in path.parts or path.as_posix() != name
                or not isinstance(digest, str) or not SHA.fullmatch(digest)):
            raise ValueError(f"record member {name!r} must be a plain relative path with an object sha256")
    return dict(members)


def _names_version(text: str, label: str) -> bool:
    return re.search(rf"(^|[/._-]){re.escape(label)}([/._-]|$)", text) is not None



def _exchange(source: Path, target: Path) -> bool:
    """Swap two existing paths in one step where the platform can (Linux renameat2, macOS renamex_np). False
    where it cannot, with both paths unchanged."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform.startswith("linux"):
            at_fdcwd = -100
            return bool(libc.renameat2(at_fdcwd, os.fsencode(source), at_fdcwd, os.fsencode(target),
                                       _RENAME_EXCHANGE) == 0)
        if sys.platform == "darwin":
            return bool(libc.renamex_np(os.fsencode(source), os.fsencode(target), _RENAME_SWAP) == 0)
    except (OSError, AttributeError):  # an older C library without the call
        return False
    return False


class Store:
    def __init__(self, hw: Path) -> None:
        self.hw = Path(hw)
        # each object this Store hashed, with its inode, size and mtime at that hash: an edit in place changes the mtime
        self._checked: dict[str, tuple[tuple[int, int, int], bool]] = {}
        self._held = threading.local()  # the keys whose lock this thread holds through this Store

    def _path(self, entry_id: str, digest: str) -> Path:
        return layout.resolve(self.hw, entry_id, SHA=digest)

    def _folder(self, entry_id: str) -> Path:
        """The folder of `entry_id`, created. hw/.cad/cache/ carries the .gitignore that keeps it out of git."""
        folder = self._path(entry_id, "0" * 64).parent
        if not folder.is_dir():
            folder.mkdir(parents=True, exist_ok=True)
        ignore = folder.parent / ".gitignore"
        if not ignore.is_file():
            staged = ignore.with_name(f".gitignore.{os.getpid()}.{threading.get_ident()}.tmp")  # transient.store_gitignore
            staged.write_text("*\n", encoding="utf-8")
            os.replace(staged, ignore)
        return folder

    # --- objects ---------------------------------------------------------------------------------------------------

    def holds(self, digest: Any) -> bool:
        """Whether the object `digest` exists and its bytes still hash to its name."""
        if not isinstance(digest, str) or not SHA.fullmatch(digest):
            return False
        path = self._path("cache.part_objects", digest)
        try:
            found = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            self._checked.pop(digest, None)
            return False
        if not stat.S_ISREG(found.st_mode):
            return False
        signature = _signature(found)
        if self._checked.get(digest, (None, False))[0] != signature:
            self._checked[digest] = (signature, file_sha(path) == digest)
        return self._checked[digest][1]

    def intern(self, path: Path) -> str:
        """Store the file at `path` as an object and return its sha256. The store holds equal bytes once: when it
        holds them already, `path` becomes a read-only link to that object, or a copy where links fail."""
        digest = file_sha(path)
        if self.holds(digest) and self.place_object(digest, path):
            return digest
        target = self._path("cache.part_objects", digest)
        staged = self._folder("cache.part_objects") / f".{digest}.{os.getpid()}.{threading.get_ident()}.tmp"
        _link_or_copy(Path(path), staged)
        if file_sha(staged) != digest:  # the source changed during the link or copy
            staged.unlink()
            raise StoreError(f"{path} changed while it was stored")
        os.replace(staged, target)
        self._checked[digest] = (_signature(os.stat(target)), True)
        return digest

    def place_object(self, digest: str, target: Path) -> bool:
        """Make `target` the object `digest`: a hard link where possible, else a copy, renamed onto `target`.
        False, with `target` untouched, when the object is absent or its bytes hash to another name."""
        if not self.holds(digest):
            return False
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name(f".{target.name}.{os.getpid()}.link")
        try:
            _link_or_copy(self._path("cache.part_objects", digest), staged)
        except FileNotFoundError:  # gc removed it meanwhile
            self._checked.pop(digest, None)
            return False
        os.replace(staged, target)
        return True

    # --- records ---------------------------------------------------------------------------------------------------

    def record(self, key: str) -> Lookup:
        """The record of `key` with every member re-hashed, or None and why: `miss: ...` when there is none or its
        JSON does not parse, `stale: <member> ...` when a member's object is absent or changed."""
        path = self._path("cache.part_records", key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Lookup(None, "miss: no record")
        except (OSError, ValueError) as exc:
            return Lookup(None, f"miss: the record cannot be read ({exc})")
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            return Lookup(None, f"miss: the record is not of format {FORMAT}")
        try:
            members = _members(data)
        except ValueError as exc:
            return Lookup(None, f"miss: {exc}")
        for name, digest in sorted(members.items()):
            object_path = self._path("cache.part_objects", digest)
            if not object_path.is_file():
                return Lookup(None, f"stale: {name} names object {digest}, which is missing")
            if not self.holds(digest):
                return Lookup(None, f"stale: {name} object {digest} does not hash to its name")
        return Lookup(data, "")

    def claim(self, key: str, record: Mapping[str, Any], *, version_labels: tuple[str, ...] = ()) -> str:
        """Keep `record` under `key` unless a usable record is there already: `claimed`, `replaced: <why>` (the
        record there was stale or unreadable), `exists` (the same members) or `conflict: ...` (a second writer's
        members differ, and cache.findings logs it). Refuses (ValueError) a record whose member paths name one of
        `version_labels`: a cached output is never version-specific."""
        if not SHA.fullmatch(key):
            raise ValueError(f"a store key is a sha256, not {key!r}")
        members = _members(record)
        for digest in members.values():
            if not self.holds(digest):
                raise ValueError(f"record member object {digest} is not in the store; intern it first")
        for label in version_labels:
            named = [name for name in members if _names_version(name, label)]
            if named:
                raise ValueError(f"the record names version label {label} ({', '.join(named)}): a cached output "
                                 "must not depend on the version it is first built for")
        target = self._path("cache.part_records", key)
        staged = self._folder("cache.part_records") / f".{key}.json.{os.getpid()}.{threading.get_ident()}.tmp"
        staged.write_text(json.dumps({**record, "format": FORMAT}, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8")
        try:
            with self.lock(key):  # one claimer of a key at a time, so the check and the write cannot interleave
                found = self.record(key)
                kept = found.record
                if kept is None:  # none, stale or unreadable: this record takes the name whole
                    os.replace(staged, target)
                    return "claimed" if found.reason == "miss: no record" else f"replaced: {found.reason}"
                if kept.get("members") == members:
                    return "exists"
                differ = sorted(n for n in set(members) | set(kept["members"])
                                if members.get(n) != kept["members"].get(n))
                outcome = (f"conflict: members {', '.join(differ)} differ from the record kept for this key, so the "
                           "key is missing an input")
                self._finding(key, outcome)
                return outcome
        finally:
            staged.unlink(missing_ok=True)

    def retire(self, key: str) -> None:
        """Remove the record of `key`, so that the next `claim` of `key` keeps its record. A producer calls it
        under `lock(key)` for a record it judged unusable for a reason outside the key, such as changed
        producer code that its facts name."""
        if not SHA.fullmatch(key):
            raise ValueError(f"a store key is a sha256, not {key!r}")
        with self.lock(key):
            self._path("cache.part_records", key).unlink(missing_ok=True)

    def _finding(self, key: str, message: str) -> None:
        path = layout.resolve(self.hw, "cache.findings")
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"key": key, "finding": message, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        with path.open("a", encoding="utf-8") as stream:  # one line, appended: the store only appends to this log
            stream.write(line + "\n")

    @contextlib.contextmanager
    def lock(self, key: str) -> Iterator[None]:
        """While the block runs, hold the producer lock of `key` (flock). Another holder waits: another process,
        or another thread or Store. The thread that holds it can take it again through this Store, as `claim`
        and `retire` do inside a producer's lock."""
        if not SHA.fullmatch(key):
            raise ValueError(f"a store key is a sha256, not {key!r}")
        held = self._held.__dict__.setdefault("keys", set())
        if key in held:
            yield
            return
        self._folder("cache.locks")
        with self._path("cache.locks", key).open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            held.add(key)
            try:
                yield
            finally:
                held.discard(key)
                fcntl.flock(handle, fcntl.LOCK_UN)

    def place(self, record: Mapping[str, Any], destination: Path) -> str:
        """Lay `record`'s members down as the folder `destination`, whole or not at all: "" when placed, else why
        (`stale: ...`) with `destination` untouched. Where the platform allows, it swaps an existing `destination`
        out in one step. Elsewhere it moves the old folder aside, renames the new one in, and moves the old one
        back if that rename fails. Only there can a kill between the two renames leave `destination` absent,
        with the old folder beside it as `.<name>.<pid>.replaced`."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        members = _members(record)
        staged = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", suffix=".placing", dir=destination.parent))
        try:
            for name, digest in sorted(members.items()):
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if not self.holds(digest):
                        raise FileNotFoundError(digest)
                    _link_or_copy(self._path("cache.part_objects", digest), target)
                except FileNotFoundError:  # gc removed it, or its bytes changed, after holds() read it
                    self._checked.pop(digest, None)
                    return f"stale: {name} names object {digest}, which is missing or changed"
            if not destination.exists():
                os.rename(staged, destination)
                return ""
            if _exchange(staged, destination):
                shutil.rmtree(staged)  # it now holds the replaced folder
                return ""
            aside = destination.with_name(f".{destination.name}.{os.getpid()}.replaced")
            os.rename(destination, aside)
            try:
                os.rename(staged, destination)
            except OSError:  # put the old folder back before reporting the failure
                os.rename(aside, destination)
                raise
            shutil.rmtree(aside)
            return ""
        finally:
            if staged.exists():
                shutil.rmtree(staged)

    # --- retention -------------------------------------------------------------------------------------------------

    def referenced(self) -> tuple[set[str], int]:
        """Every sha256 a sealed manifest under hw/ names (versions, releases and every sealed bundle kind), and
        how many manifests it read. A manifest counts where it seals its folder (`layout.sealed`). A file named
        manifest.json inside a sealed folder is payload, which the outer seal already lists. StoreError on a seal
        it cannot read, so gc never removes what it names."""
        lay = layout.load()
        found: set[str] = set()
        manifests = 0
        for root, dirnames, filenames in os.walk(self.hw):  # never follows a link
            folder = Path(root)
            dirnames[:] = sorted(d for d in dirnames if not (folder == self.hw and d == ".cad"))
            if layout.MANIFEST not in filenames or lay.sealed(layout.relative(self.hw, folder)) is None:
                continue
            path = folder / layout.MANIFEST
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise StoreError(f"{path} cannot be read ({exc}); gc removed nothing") from exc
            inventory = document.get("files") if isinstance(document, dict) else None
            if not isinstance(inventory, dict):
                raise StoreError(f"{path} has no files inventory; gc removed nothing")
            manifests += 1
            for value in inventory.values():  # a release manifest lists {sha256, original}
                digest = value.get("sha256") if isinstance(value, Mapping) else value
                if isinstance(digest, str):
                    found.add(digest)
        return found, manifests

    def gc(self, dry_run: bool = False) -> dict[str, Any]:
        """Remove every object no sealed manifest references and no live record needs, every record that is not
        live, and the part store of the former layout. A record is live when a manifest references one of its
        members and gc keeps every member. `freed_bytes` counts only objects no other link still holds."""
        referenced, manifests = self.referenced()
        objects_dir = self._path("cache.part_objects", "0" * 64).parent
        records_dir = self._path("cache.part_records", "0" * 64).parent
        present = {p.name for p in objects_dir.iterdir() if SHA.fullmatch(p.name)} if objects_dir.is_dir() else set()
        records: dict[Path, set[str]] = {}
        for path in sorted(records_dir.glob("*.json")) if records_dir.is_dir() else []:
            try:
                members = json.loads(path.read_text(encoding="utf-8")).get("members")
            except (OSError, ValueError, AttributeError):
                members = None
            records[path] = {d for d in members.values() if isinstance(d, str)} if isinstance(members, dict) else set()
        live = {path for path, digests in records.items() if digests & referenced and digests <= present}
        keep = referenced | {d for path in live for d in records[path]}
        removed, removed_bytes, freed_bytes = [], 0, 0
        for digest in sorted(present - keep):
            path = objects_dir / digest
            info = path.stat()
            removed.append(digest)
            removed_bytes += info.st_size
            freed_bytes += info.st_size if info.st_nlink == 1 else 0
            if not dry_run:
                path.unlink()
        stale = [path for path in records if path not in live]
        for path in stale:
            freed_bytes += path.stat().st_size
            if not dry_run:
                path.unlink()
        former = [rel for rel in sorted(FORMER) if (self.hw / rel).is_dir() and not (self.hw / rel).is_symlink()]
        if not dry_run:
            for rel in former:
                shutil.rmtree(self.hw / rel, onerror=_writable_then_remove)
        return {"manifests": manifests, "objects": {"kept": len(present) - len(removed), "removed": len(removed)},
                "records": {"kept": len(live), "removed": len(stale)}, "removed_bytes": removed_bytes,
                "freed_bytes": freed_bytes, "removed_objects": removed, "former_layout_removed": former}


def _writable_then_remove(function: Any, path: str, _info: Any) -> None:
    """rmtree's retry for a read-only file of the former store: make its folder writable, then remove it again."""
    os.chmod(os.path.dirname(path), 0o755)
    function(path)
