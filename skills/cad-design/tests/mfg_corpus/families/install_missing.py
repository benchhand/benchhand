"""install_missing: an assembly occurrence that no operation installs, other than its one base.

The verdict is the user-visible one: the `result` of the packet cad-motion access.py builds for the record,
not check_order's rows. A part that lost its installing operation looks like a second base part, and the
sequence passes unless the packet fails. The mutant drops one installing operation from the housing assembly
sequence. The twin keeps it. `_result` builds both variants.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from corpus import Family, Outcome, Pair
from support import PNS, SKILLS, TITLES, _access_geometry, load, require, reordered, socket_operations

VALID = ("seat-bearing", "fit-cover", "fasten-cover", "mount-bracket")  # the screw goes in before the bracket


def _result(dropped: str | None) -> Outcome:
    """The `result` of access.py's packet over the five parts, without the operation `dropped`."""
    require("yaml")
    root, _ = _access_geometry()
    full = reordered(socket_operations("blocked_socket.yaml"), *VALID)
    items = [copy.deepcopy(op) for op in full if op["id"] != dropped]
    fx = load("cad_record_mi_fixtures", SKILLS / "cad-record" / "tests" / "fixtures" / "manufacturing_intent.py")
    parts = [fx.part(pn, TITLES[pn], process="cnc") for pn in PNS]
    interface = fx.interface("IF-001", "TST-0101", "TST-0102", "mounting",
                             limits={"press_fit": {"fit": "22 H7/p6", "pair": ["TST-0102", "TST-0101"]}})
    documents = fx.record(parts=parts, interfaces=[interface], operations=items)
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        hw = fx.write_record(Path(tmp) / "hw", documents)
        source = hw / "manufacturing" / "sources" / "socket.step"
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "geometry" / "socket.step", source)
        attach = [arg for pn in PNS[1:] for arg in ("--attach", f"{pn}=TST-0101")]
        run = subprocess.run(["uv", "run", "--quiet", "--script", str(SKILLS / "cad-motion" / "scripts" / "access.py"),
                              "--hw", str(hw), "--parts-dir", str(root / "geometry" / "parts"), *attach],
                             capture_output=True, text=True, timeout=1800, check=False)
        if run.returncode not in (0, 1):
            raise RuntimeError(f"access.py exited {run.returncode}: {(run.stdout + run.stderr)[-1500:]}")
        result = json.loads(run.stdout)["result"]
    if result not in ("fail", "pass", "unknown"):
        raise RuntimeError(f"unexpected packet result {result!r}")
    return result  # type: ignore[no-any-return]


def _pair(pair_id: str, dropped: str, defect: str) -> Pair:
    def run(mutant: bool) -> Outcome:
        return _result(dropped if mutant else None)

    return Pair(pair_id, defect, run)


FAMILY = Family(
    id="install_missing",
    check="skills/cad-motion/scripts/access.py packet result (assembly_operations.check_order)",
    owner="pkgE",
    pairs=(
        _pair("bracket", "mount-bracket", "the bracket's installing join is missing and nothing else names it"),
        _pair("screw", "fasten-cover", "the cover screw's installing fasten is missing and nothing else names it"),
        _pair("cover", "fit-cover", "the cover's installing close is missing and the screw still fastens into it"),
        _pair("bearing", "seat-bearing", "the bearing's installing press fit is missing and the cover still closes over it"),
    ),
)
