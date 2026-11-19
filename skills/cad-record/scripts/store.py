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
fresh folder and renames that folder into place, so a reader sees the whole set or none of it. It hashes what it
linked before the folder gets its name. `place_object` links one object, swaps the link onto its name and then
hashes it, under a lock on the folder. When that hash fails, it puts the old file back, and never over a file
another writer renamed there since: such a file stands, and the old one moves to `cache.recovery`, named in
`cache.findings` (copied there from another filesystem, or kept in place, which `remove_tree` spares). Only `holds` answers from
the hashes this Store took before (`_settled`). Every write lands beside its name and the store renames it onto that
name, and every store
path is a plain file or folder. `gc` keeps what any sealed manifest under hw/ names and removes the rest, and the part store of
the former layout (hw/.cad/parts, hw/.cad/builds). Deleting the store by hand costs only recomputation, never a
version, because every version file is a link or copy of its own.

It uses only the standard library and stays compatible with Python 3.11, so a FreeCAD job could import it.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import fcntl
import hashlib
import importlib.util
import itertools
import json
import os
import re
import secrets
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
_RENAME_NOREPLACE, _RENAME_EXCHANGE = 1, 2  # renameat2 flags (Linux)
_RENAME_SWAP, _RENAME_EXCL = 2, 4  # renamex_np flags (macOS)
_AT_FDCWD = -100
_UNSUPPORTED = frozenset({errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP})  # no such rename flag here
_ATTEMPTS = itertools.count()  # numbers each name `_fresh` makes in this process
# The coarsest timestamp step the store allows for: FAT stamps mtime in 2 s steps, HFS+ and ext3 in 1 s, and Linux
# stamps from a clock that moves once per scheduler tick. A write within one step of the last keeps its timestamps.
TICK_NS = 2_000_000_000


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


def _signature(found: os.stat_result) -> tuple[int, int, int, int, int]:
    """An object's device, inode, size, mtime and ctime. A write changes mtime and ctime unless it lands within the
    clock tick of the last change, a utime that puts the mtime back still changes the ctime, and so does a link."""
    return found.st_dev, found.st_ino, found.st_size, found.st_mtime_ns, found.st_ctime_ns


def _settled(found: os.stat_result, hashed_at: int, now: int) -> bool:
    """Whether a hash that started at `hashed_at` (ns) can stand, at `now` (ns), for an object with the stat `found`:
    its last change is at least TICK_NS older, so no later write can carry that change's timestamps. git's racy-git
    rule (Documentation/technical/racy-git.txt), with the store's clock in place of the index file's mtime. A clock
    that reads earlier than `hashed_at` stepped back, and could stamp a new write like the change the hash saw, so
    the hash does not stand then. Nor does a hash older than the ctime: ctime <= hashed_at - TICK_NS < hashed_at."""
    return hashed_at <= now and max(found.st_mtime_ns, found.st_ctime_ns) <= hashed_at - TICK_NS


def _link_or_copy(source: Path, target: Path, *, exclusive: bool = False) -> None:
    """`target` as a read-only hard link to `source`, or a read-only copy where the filesystem allows no link.
    With `exclusive`, FileExistsError while `target` exists, which stays as it was."""
    try:
        os.link(source, target)
    except FileExistsError:
        if exclusive:
            raise
        shutil.copyfile(source, target)
    except OSError:  # another filesystem, FAT, a link limit: the same bytes, copied
        if not exclusive:
            shutil.copyfile(source, target)
        else:
            with open(source, "rb") as reading, open(target, "xb") as writing:
                shutil.copyfileobj(reading, writing)
    os.chmod(target, READ_ONLY)


def _copy_in(source: Path, durable: Path) -> None:
    """Copy `source` to the new file `durable` (O_EXCL: FileExistsError while it exists), with its mode, then
    fsync the copy and its folder. On any error the partial copy goes, and `source` stays as it was. Only a file
    this call created goes: an existing `durable` is never touched."""
    created = False
    try:
        with open(durable, "xb") as writing:
            created = True
            with open(source, "rb") as reading:
                shutil.copyfileobj(reading, writing)
                os.fchmod(writing.fileno(), stat.S_IMODE(os.fstat(reading.fileno()).st_mode))
            writing.flush()
            os.fsync(writing.fileno())
        handle = os.open(durable.parent, os.O_RDONLY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)
    except BaseException:
        if created:
            durable.unlink(missing_ok=True)  # the partial copy this call created
        raise


def _fresh(near: Path, kind: str) -> Path:
    """A name beside `near` for one step of one `place_object`, ending in `.<kind>`: this process, thread and a
    count, then a random part, so that no other step, attempt or process picks it. A step that creates the name
    still refuses one that exists."""
    return near.with_name(f".{near.name}.{os.getpid()}.{threading.get_ident()}.{next(_ATTEMPTS)}."
                          f"{secrets.token_hex(4)}.{kind}")


def _inode(found: os.stat_result) -> tuple[int, int]:
    return found.st_dev, found.st_ino


@contextlib.contextmanager
def _folder_lock(folder: Path) -> Iterator[None]:
    """While the block runs, hold an exclusive flock on `folder` itself. Each `place_object` into it waits its
    turn, from any process, thread or Store, so none renames onto a name there while another may still undo."""
    handle = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        os.close(handle)


class _NoAtomicRename(Exception):
    """The filesystem lacks the rename flags that `_put_in` needs."""


def _rename2(source: Path, target: Path, *, swap: bool) -> None:
    """Rename `source` onto `target` in one step, which fails (FileExistsError) while `target` exists, or with
    `swap` exchange the two, which fails (FileNotFoundError) while either is absent. Linux renameat2 with
    RENAME_NOREPLACE or RENAME_EXCHANGE, macOS renamex_np with RENAME_EXCL or RENAME_SWAP. An OSError with an
    errno in _UNSUPPORTED where the platform, its C library or the filesystem lacks the call."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform.startswith("linux"):
            done = libc.renameat2(_AT_FDCWD, os.fsencode(source), _AT_FDCWD, os.fsencode(target),
                                  _RENAME_EXCHANGE if swap else _RENAME_NOREPLACE)
        elif sys.platform == "darwin":
            done = libc.renamex_np(os.fsencode(source), os.fsencode(target), _RENAME_SWAP if swap else _RENAME_EXCL)
        else:
            raise AttributeError(sys.platform)
    except AttributeError as exc:  # another platform, or an older C library without the call
        raise OSError(errno.ENOSYS, f"no atomic rename flags here ({exc})") from exc
    if done != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(source), None, str(target))


def _put_in(staged: Path, target: Path) -> bool:
    """Rename `staged` onto `target` in one step that destroys nothing: it swaps the two, so that `staged` then
    names the file `target` named, or, while there is no `target`, moves `staged` there. Whether `target` named
    a file. _NoAtomicRename, with nothing changed, where the filesystem lacks the swap."""
    for _ in range(3):  # each step fails, changing nothing, when another writer adds or removes `target` meanwhile
        try:
            _rename2(staged, target, swap=True)
            return True
        except FileNotFoundError:
            pass
        except OSError as exc:
            if exc.errno in _UNSUPPORTED:
                raise _NoAtomicRename(str(exc)) from exc
            raise
        try:
            _rename2(staged, target, swap=False)
            return False
        except FileExistsError:
            pass
    raise StoreError(f"{target} kept appearing and disappearing while it was placed")


def _names(path: Path, mine: tuple[int, int]) -> bool:
    """Whether `path` names the inode `mine`."""
    try:
        return _inode(os.stat(path, follow_symlinks=False)) == mine
    except FileNotFoundError:
        return False


def _restore(path: Path, target: Path) -> bool:
    """Rename `path` back onto `target` while there is no `target`. False, with `path` kept, when there is one."""
    try:
        _rename2(path, target, swap=False)
    except FileExistsError:
        return False
    return True


def _move_aside(target: Path) -> Path | None:
    """Rename whatever `target` names to a fresh name beside it, never onto an existing file. The new name, or
    None when there is no `target`."""
    for _ in range(5):
        aside = _fresh(target, "aside")
        try:
            _rename2(target, aside, swap=False)
            return aside
        except FileExistsError:  # a kept file has that name: it stays, and the next name is fresh
            continue
        except FileNotFoundError:
            return None
    raise StoreError(f"no free name beside {target} to move it aside")


def _take_back(staged: Path, target: Path, mine: tuple[int, int]) -> list[tuple[Path, str]]:
    """Undo `_put_in`, where `staged` names the old file if there was one, without destroying any file but the
    link `mine`. The file at `target` moves aside. If it is `mine`, it goes. Otherwise it is another writer's,
    and goes back. Then the old file goes back. Each goes back only while there is no `target`, so a file
    renamed there in between stands. The files that could not go back stay where they are, and the list says
    what each holds: `old` or `foreign`."""
    kept = []
    aside = _move_aside(target)
    if aside is not None and _names(aside, mine):
        aside.unlink()
    elif aside is not None and not _restore(aside, target):
        kept.append((aside, "foreign"))
    if os.path.lexists(staged) and not _restore(staged, target):
        kept.append((staged, "old"))
    return kept


def _settle(staged: Path, target: Path, mine: tuple[int, int] | None, renamed: bool, matched: bool,
            kept: list[tuple[Path, str]]) -> bool:
    """End `_publish`: whether `target` is verified. When nothing was `renamed`, `staged` is the link and goes.
    With `matched` (a hash of `mine` to its name, and a clean close), `target` still naming `mine` is a success,
    and the old file at `staged` goes, as a rename onto `target` would remove it. A `target` that names another
    file is another writer's, and stands, with the old file kept. Without `matched`, `_take_back` undoes the
    rename. Each file kept goes to `kept`, as `_take_back` lists it."""
    if not renamed or mine is None:
        staged.unlink(missing_ok=True)
        return False
    if not matched:
        kept.extend(_take_back(staged, target, mine))
        return False
    if not _names(target, mine):  # another writer renamed its file onto `target` after the swap
        if os.path.lexists(staged):
            kept.append((staged, "old"))
        return False
    staged.unlink(missing_ok=True)  # the file `target` named before
    return True


def _publish(staged: Path, target: Path, digest: str, kept: list[tuple[Path, str]]) -> bool:
    """Rename `staged` onto `target` (`_put_in`), then hash the renamed inode through a descriptor opened before
    the rename. A hard link shares its object's inode, so a write through the object up to that hash shows in
    it. Whether `target` is verified (`_settle`): only once the hash matched, the descriptor closed cleanly and
    `target` still names the inode. An exception from the hash or the close undoes the rename, then
    propagates. Each file kept beside `target` goes to `kept`. Only under _folder_lock."""
    mine: tuple[int, int] | None = None
    renamed = matched = False
    try:
        with staged.open("rb") as stream:
            mine = _inode(os.fstat(stream.fileno()))
            _put_in(staged, target)
            renamed = True
            matched = hashlib.file_digest(stream, "sha256").hexdigest() == digest
    except BaseException:
        # an interruption inside `_put_in` after its rename: `staged` then no longer names the link
        renamed = renamed or (mine is not None and not _names(staged, mine))
        _settle(staged, target, mine, renamed, False, kept)
        raise
    return _settle(staged, target, mine, renamed, matched, kept)


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
    """Swap two existing paths in one step where the platform can (`_rename2`). False where it cannot, with both
    paths unchanged."""
    try:
        _rename2(source, target, swap=True)
    except OSError:
        return False
    return True


class Store:
    def __init__(self, hw: Path) -> None:
        self.hw = Path(hw)
        # each object this Store hashed: its stat and the time (ns) when that hash started, and whether it matched.
        # `holds` reuses one only while it is settled (`_settled`); `record`, `place` and `place_object` never do.
        self._checked: dict[str, tuple[tuple[int, int, int, int, int], int, bool]] = {}
        self._held = threading.local()  # the keys whose lock this thread holds through this Store
        self._pinned: set[tuple[int, int]] = set()  # kept files that stay in place: `remove_tree` spares them

    def _path(self, entry_id: str, digest: str) -> Path:
        return Path(layout.resolve(self.hw, entry_id, SHA=digest))

    def _folder(self, entry_id: str) -> Path:
        """The folder of `entry_id`, created. hw/.cad/cache/ carries the .gitignore that keeps it out of git."""
        folder = (Path(layout.resolve(self.hw, entry_id, NAME="x")).parent if entry_id == "cache.recovery" else
                  self._path(entry_id, "0" * 64).parent)
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
        """Whether the object `digest` exists and its bytes still hash to its name. It answers from an earlier hash
        while the object's stat is unchanged, its last change is at least TICK_NS older than that hash, and the
        clock reads no earlier than that hash did."""
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
        signature, hashed_at, matched = self._checked.get(digest, (None, 0, False))
        if signature == _signature(found) and _settled(found, hashed_at, time.time_ns()):
            return matched
        return self._hash(digest)

    def _hash(self, digest: str) -> bool:
        """Whether the object `digest` is a plain file whose bytes, read now, hash to its name. The result, the stat
        and the time before the read go to the cache that `holds` reads."""
        path = self._path("cache.part_objects", digest)
        started = time.time_ns()  # before the stat and the read, so that a write during either is never settled
        try:
            found = os.stat(path, follow_symlinks=False)
            matched = stat.S_ISREG(found.st_mode) and file_sha(path) == digest
        except FileNotFoundError:  # gc removed it
            self._checked.pop(digest, None)
            return False
        self._checked[digest] = (_signature(found), started, matched)
        return matched

    def intern(self, path: Path) -> str:
        """Store the file at `path` as an object and return its sha256. The store holds equal bytes once: when it
        holds them already, `path` becomes a read-only link to that object, or a copy where links fail."""
        digest = file_sha(path)
        if self.holds(digest) and self.place_object(digest, path):
            return digest
        target = self._path("cache.part_objects", digest)
        staged = self._folder("cache.part_objects") / f".{digest}.{os.getpid()}.{threading.get_ident()}.tmp"
        _link_or_copy(Path(path), staged)
        started = time.time_ns()
        if file_sha(staged) != digest:  # the source changed during the link or copy
            staged.unlink()
            raise StoreError(f"{path} changed while it was stored")
        os.replace(staged, target)
        self._checked[digest] = (_signature(os.stat(target)), started, True)
        return digest

    def place_object(self, digest: str, target: Path) -> bool:
        """Make `target` the object `digest`: a hard link where possible, else a copy. It renames the link onto
        `target`, then hashes the inode it renamed, under a lock on `target`'s folder (`_publish`). True once its
        bytes hash to `digest`, the descriptor closed cleanly and `target` still names that inode. False when the
        object is absent or its bytes hash to another name, with the old `target` (or none) put back, or when
        another writer replaced `target` meanwhile, which stays. A file that cannot go back without replacing a
        newer one moves to the recovery folder (`_keep`), and cache.findings names it (`kept: <path> holds ...`).
        False with `target` untouched where the filesystem has no atomic swap (`_rename2`), so the caller builds
        instead. An exception from the hash or the close puts the old `target` back, then propagates."""
        if not self.holds(digest):
            return False
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        kept: list[tuple[Path, str]] = []
        try:
            with _folder_lock(target.parent):
                try:
                    staged = self._stage(digest, target)
                except FileNotFoundError:  # gc removed it meanwhile
                    self._checked.pop(digest, None)
                    return False
                try:
                    placed = _publish(staged, target, digest, kept)
                except _NoAtomicRename:
                    placed = False
        finally:  # also when an exception leaves _publish
            for path, held in kept:
                self._keep(path, held, digest, target)
        if not placed:
            self._checked.pop(digest, None)
        return placed

    def _stage(self, digest: str, target: Path) -> Path:
        """A new link to (or copy of) the object `digest` at a fresh name beside `target` (`_fresh`), never over
        an existing file."""
        for _ in range(5):
            staged = _fresh(target, "link")
            try:
                _link_or_copy(self._path("cache.part_objects", digest), staged, exclusive=True)
                return staged
            except FileExistsError:  # a kept file has that name: it stays, and the next name is fresh
                continue
        raise StoreError(f"no free name beside {target} to stage object {digest}")

    def _keep(self, path: Path, held: str, digest: str, target: Path) -> None:
        """Secure the file `path` that a failed `place_object` of `digest` onto `target` kept, then name it in
        cache.findings. It moves to the recovery folder (cache.recovery) with a no-replace rename onto a fresh
        name. Across filesystems, it is copied there instead (`_copy_in`), and the original goes only once the
        copy and the folder are synced. When that fails too, the file stays where it is, and `remove_tree`
        spares it, so the caller's cleanup cannot remove it. The finding is best effort: an error writing it
        never changes what `place_object` returns or raises."""
        try:
            path = self._recover(path, held, target)
        except Exception:  # noqa: BLE001 - the file stays where it is, and remove_tree spares it
            with contextlib.suppress(OSError):
                self._pinned.add(_inode(os.stat(path, follow_symlinks=False)))
        what = ("the file that the target named before" if held == "old" else
                "a file another writer renamed onto the target")
        try:
            self._finding(digest, f"kept: {path} holds {what}. place_object of object {digest} onto {target} "
                                  "failed, and could not put it back without replacing a newer file there")
        except Exception:  # noqa: BLE001 - best effort: findings.jsonl may be unwritable, and the file is kept
            pass

    def _recover(self, path: Path, held: str, target: Path) -> Path:
        """Where `_keep` puts `path`: renamed into the recovery folder on the same filesystem, else copied there,
        with the original removed once the copy is synced. An OSError or StoreError leaves `path` as it was."""
        folder = self._folder("cache.recovery")
        rename = os.stat(folder).st_dev == os.stat(path, follow_symlinks=False).st_dev
        for _ in range(5):
            durable = _fresh(folder / target.name, held)
            durable = durable.with_name(durable.name.lstrip("."))
            try:
                if rename:
                    _rename2(path, durable, swap=False)
                    return durable
                _copy_in(path, durable)
            except FileExistsError:  # a kept file has that name: it stays, and the next name is fresh
                continue
            except OSError as exc:
                if not rename or exc.errno != errno.EXDEV:
                    raise
                rename = False  # another filesystem after all: copy instead
                continue
            with contextlib.suppress(OSError):  # the synced copy stands either way
                path.unlink()
            return durable
        raise StoreError(f"no free name in {folder} for {path}")

    def remove_tree(self, folder: Path) -> None:
        """Remove `folder` as `shutil.rmtree(folder, ignore_errors=True)` does, but spare each file that a failed
        `place_object` of this Store kept in place (`_keep`), and the folders that hold it."""
        folder = Path(folder)
        if not self._pinned:
            shutil.rmtree(folder, ignore_errors=True)
            return
        for root, dirnames, filenames in os.walk(folder, topdown=False):  # never follows a link
            for name in [*filenames, *dirnames]:
                path = Path(root) / name
                with contextlib.suppress(OSError):
                    found = os.stat(path, follow_symlinks=False)
                    if stat.S_ISDIR(found.st_mode):
                        path.rmdir()  # fails while it holds a spared file
                    elif _inode(found) not in self._pinned:
                        path.unlink()
        with contextlib.suppress(OSError):
            folder.rmdir()

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
            if not self._hash(digest):  # never `holds`: a reused record never rests on the cache
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
        with the old folder beside it as `.<name>.<pid>.replaced`. It hashes each member it laid down before
        it renames the folder into place and never after, so a write through an object between the hash and the
        rename goes unseen. No production code calls `place`. `place_object` hashes after its rename (#250)."""
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
                    if file_sha(target) != digest:  # changed since a hash `holds` trusted: never publish it
                        raise FileNotFoundError(digest)
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
