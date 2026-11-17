#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11,<3.15"
# dependencies = ["pyyaml>=6.0", "jsonschema>=4.0", "cadquery-ocp-novtk==8.0.1.1.0"]
# ///
"""Print the per-family detection, abstention and false-positive rates of the mutation corpus.

Run:  uv run --script skills/cad-design/tests/mfg_corpus/rates.py [--family ID ...] [--out PATH]
Writes the JSON report (default: <tempdir>/mfg-corpus-rates.json) and prints its path after the table.
Exit 0 always when the corpus ran; the rates are a baseline, not a gate (a ratchet compares two reports).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", action="append", help="only this family id (repeatable)")
    parser.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "mfg-corpus-rates.json")
    args = parser.parse_args(argv)
    families = corpus.load_families()
    if args.family:
        unknown = set(args.family) - {f.id for f in families}
        if unknown:
            parser.error(f"unknown family: {', '.join(sorted(unknown))}")
        families = [f for f in families if f.id in args.family]
    rows = corpus.evaluate(families)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"format": 1, "families": rows}, indent=2) + "\n", encoding="utf-8")
    print(corpus.table(rows))
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
