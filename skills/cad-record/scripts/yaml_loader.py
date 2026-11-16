"""`yaml.safe_load` on libyaml's C parser: the one YAML reader of the CAD skills' scripts.

PyYAML's pure-Python SafeLoader is several times slower than its libyaml binding, CSafeLoader. Both
build the document with the same SafeConstructor and resolver. libyaml's scanner and parser accept
more than PyYAML's, though: a tab as a separator, a byte order mark inside the document, and some uses
of `!`, `?` and a comment right after a block scalar indicator. A document holding any of these goes
straight to SafeLoader, so the loader never accepts a document `yaml.safe_load` refuses. The list
comes from comparing both parsers on the tracked YAML files and on about 575,000 mutated record
documents; tests/test_yaml_loader.py keeps those cases. A document libyaml refuses is parsed again
with SafeLoader, so the `yaml.YAMLError` a caller reports, and its message, stays the one
`yaml.safe_load` raises. Bytes, a stream that cannot seek back (or lacks seekable(), tell() or
seek()), and a PyYAML without libyaml go to SafeLoader too.

Load it by path and register it in `sys.modules` as `cad_record_yaml_loader`.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

FAST_LOADER: Any = getattr(yaml, "CSafeLoader", None)
# Where libyaml and SafeLoader differ: tabs, a byte order mark, `!`, `?`, and `#` after `|` or `>`.
SAFE_LOADER_ONLY = re.compile(r"[\t\ufeff!?]|[|>][-+0-9]*#")


def _safe(stream: Any) -> Any:
    return yaml.load(stream, Loader=yaml.SafeLoader)  # noqa: S506 - SafeLoader is yaml.safe_load


def _rewindable(stream: Any) -> bool:
    """Whether `stream` can report its position and seek back to it, for a second parse."""
    seekable = getattr(stream, "seekable", None)
    return (callable(seekable) and bool(seekable()) and callable(getattr(stream, "tell", None))
            and callable(getattr(stream, "seek", None)))


def safe_load(stream: Any) -> Any:
    """The single document in `stream` (a str, bytes or an open file), as `yaml.safe_load` reads it."""
    is_file = hasattr(stream, "read")
    if FAST_LOADER is None or isinstance(stream, (bytes, bytearray)) or (is_file and not _rewindable(stream)):
        return _safe(stream)
    start = stream.tell() if is_file else None
    text = stream.read() if is_file else stream
    if not isinstance(text, str) or SAFE_LOADER_ONLY.search(text):
        if start is not None:
            stream.seek(start)
        return _safe(stream)
    try:
        return yaml.load(text, Loader=FAST_LOADER)  # noqa: S506 - CSafeLoader constructs safe types only
    except yaml.YAMLError:
        if start is not None:
            stream.seek(start)
        return _safe(stream)
