"""The bundled JSON schemas as an offline registry, and their Draft 2020-12 validators, built once for
each content of a schema directory.

validate.py, manufacturing_intent.py, check_packet.py and cad-fits stackup.py validate through it.
Every `*.schema.json` in a schema directory resolves by its `$id`, so a `$ref` such as
`manufacturing.schema.json#/$defs/finding` needs no network. Each call names its schema directory, so
another checkout's cad-record keeps its own registry and validators in the same process. A validator
is cached by the sha256 of the schema it validates and of every schema file in its directory, so a
schema edited or deleted during a run is read again, as before the cache. A file is hashed again
whenever its modification time, change time, size or inode changes. jsonschema is imported only when a validator
is built, so loading this module does not need it. Load it by path and register it in `sys.modules`
as `cad_record_schema_registry`.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"
_CACHE_SIZE = 256

Stamp = tuple[int, int, int, int]  # st_mtime_ns, st_ctime_ns, st_size, st_ino
State = tuple[tuple[str, str], ...]  # (file name, sha256 of its text) of every schema in a directory
_TEXTS: dict[str, str] = {}  # schema text by its sha256


def _stamp(path: str | Path) -> Stamp:
    """The stat fields that change when a file is written or replaced; OSError when it is gone. The
    change time is among them because no user tool can set it back, as os.utime can the modification time."""
    status = os.stat(path)
    return status.st_mtime_ns, status.st_ctime_ns, status.st_size, status.st_ino


@lru_cache(maxsize=_CACHE_SIZE)
def _digest(path: Path, stamp: Stamp) -> str:
    """The sha256 of the text of `path` as it is at `stamp`; the text is kept by its digest."""
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    _TEXTS[digest] = text
    return digest


def _content(path: Path) -> str:
    """The sha256 of the current text of `path`. Raises OSError when it cannot be read."""
    return _digest(path, _stamp(path))


@lru_cache(maxsize=_CACHE_SIZE)
def _resolved(root: str) -> Path:
    return Path(root).resolve()


@lru_cache(maxsize=_CACHE_SIZE)
def _names(root: Path, stamp: Stamp) -> tuple[str, ...]:
    """The schema file names in `root` while the directory itself is unchanged (no file added or removed)."""
    return tuple(sorted(entry.name for entry in os.scandir(root)
                        if entry.name.endswith(".schema.json") and entry.is_file()))


def _directory_state(root: Path) -> State:
    """Every schema file of `root` by name, with the sha256 of its text."""
    state = []
    for name in _names(root, _stamp(root)):
        try:
            state.append((name, _content(root / name)))
        except FileNotFoundError:  # removed since the listing: the next listing leaves it out
            continue
    return tuple(state)


@lru_cache(maxsize=_CACHE_SIZE)
def _registry(root: Path, state: State) -> Any:
    from referencing import Registry, Resource  # noqa: PLC0415 - jsonschema's dependency, needed only to validate

    resources = []
    for _name, digest in state:
        contents = json.loads(_TEXTS[digest])
        resources.append((contents["$id"], Resource.from_contents(contents)))
    return Registry().with_resources(resources)


def registry(root: Path = SCHEMAS) -> Any:
    """Every schema of `root` by its $id."""
    resolved = _resolved(str(root))
    return _registry(resolved, _directory_state(resolved))


@lru_cache(maxsize=_CACHE_SIZE)
def _schema(digest: str) -> Any:
    return json.loads(_TEXTS[digest])


def schema(root: Path, name: str) -> Any:
    """The parsed `root/name`. Raises OSError or json.JSONDecodeError, as reading it does; a failure is
    not cached."""
    path = Path(root) / name
    _stamp(path)  # the error a missing file raises names `path` as given
    return _schema(_content(_resolved(str(root)) / name))


@lru_cache(maxsize=_CACHE_SIZE)
def _document_validator(digest: str, root: Path, state: State, formats: bool) -> Any:
    from jsonschema import Draft202012Validator, FormatChecker  # noqa: PLC0415 - needed only to validate

    return Draft202012Validator(_schema(digest), registry=_registry(root, state),
                                format_checker=FormatChecker() if formats else None)


def document_validator(root: Path, name: str, *, formats: bool = False) -> Any:
    """The validator of `root/name`; with `formats` it also asserts `format` keywords. Raises OSError or
    json.JSONDecodeError when a schema of `root` cannot be read."""
    resolved = _resolved(str(root))
    return _document_validator(_content(resolved / name), resolved, _directory_state(resolved), formats)


@lru_cache(maxsize=_CACHE_SIZE)
def _ref_validator(ref: str, root: Path, state: State) -> Any:
    from jsonschema import Draft202012Validator  # noqa: PLC0415 - needed only to validate

    return Draft202012Validator({"$ref": ref}, registry=_registry(root, state))


def ref_validator(root: Path, ref: str) -> Any:
    """The validator of one schema or definition of `root` by its `$ref`, such as `<$id>#/$defs/finding`."""
    resolved = _resolved(str(root))
    return _ref_validator(ref, resolved, _directory_state(resolved))
