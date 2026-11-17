"""assembly.access: the fitted-partner exemption covers the final engagement only.

A pin pressed into a sleeve already seated in the housing touches the sleeve by design while it is
engaged; the same contact earlier on the insertion path is a blocked path. Mutant: the sleeve carries a
gantry over the pin, which the pin meets before it reaches the sleeve. Twin: the same sleeve without the
gantry, the pin still interfering with the sleeve bore over its final engagement. Both go through access.py.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from corpus import Family, Pair, Skip

REPO = Path(__file__).resolve().parents[5]
MOTION = REPO / "skills" / "cad-motion"
PIN_RADIUS_MM = 5.05  # 0.05 mm radial interference with the r 5 sleeve bore
HOUSING, SLEEVE, PIN = "TST-0101", "TST-0102", "TST-0104"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run(mutant: bool) -> str:
    uv = shutil.which("uv")
    if uv is None:
        raise Skip("uv is not installed")
    fixture = _load("mfg_access_fixture", MOTION / "tests" / "fixtures" / "access_fixture.py")
    records = _load("mfg_record_fixtures", REPO / "skills" / "cad-record" / "tests" / "fixtures" / "manufacturing_intent.py")
    pose = fixture.pose
    housing = fixture.cut(fixture.box(-30, -30, 0, 30, 30, 20), fixture.cylinder(0, 0, 8, 11, 13))
    sleeve = fixture.cut(fixture.cylinder(0, 0, 8, 11, 8), fixture.cylinder(0, 0, 7, 5, 10))
    if mutant:  # a gantry over the sleeve bore: legs up to z 30, a bar across, higher than the pin is long
        sleeve = fixture.fuse(sleeve, fixture.box(-10.5, -3, 12, -9, 3, 30), fixture.box(9, -3, 12, 10.5, 3, 30),
                              fixture.box(-10.5, -3, 30, 10.5, 3, 32))
    pin = fixture.cylinder(0, 0, 8, PIN_RADIUS_MM, 6)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        parts = root / "parts"
        parts.mkdir()
        for pn, shape in ((HOUSING, housing), (SLEEVE, sleeve), (PIN, pin)):
            pose.write_part_step(parts / f"{pn}.step", pose.Part(pn, pn, shape))
        seat = records.interface("IF-001", HOUSING, SLEEVE, "mounting",
                                 limits={"press_fit": {"fit": "22 H7/p6", "pair": [SLEEVE, HOUSING]}})
        dowel = records.interface("IF-002", SLEEVE, PIN, "mounting",
                                  limits={"press_fit": {"fit": "10 H7/p6", "pair": [PIN, SLEEVE]}})
        operations = [
            {"id": "seat-sleeve", "kind": "press_fit", "part": SLEEVE, "into": HOUSING, "interface": "IF-001",
             "direction": [0, 0, -1], "acceptance": {"kind": "seated", "features": ["outer_face", "seat_floor"]}},
            {"id": "insert-pin", "kind": "join", "part": PIN, "into": HOUSING, "direction": [0, 0, -1],
             "acceptance": {"kind": "standoff", "features": ["pin_foot", "bore_floor"]}}]
        documents = records.record(parts=[records.part(pn, pn, process="cnc") for pn in (HOUSING, SLEEVE, PIN)],
                                   interfaces=[seat, dowel], operations=operations)
        hw = records.write_record(root / "hw", documents)
        done = subprocess.run([uv, "run", "--quiet", "--script", str(MOTION / "scripts" / "access.py"), "--hw", str(hw),
                               "--parts-dir", str(parts), "--attach", f"{SLEEVE}={HOUSING}", "--attach", f"{PIN}={HOUSING}"],
                              capture_output=True, text=True, timeout=1800, check=False)
        if done.returncode not in (0, 1):
            raise RuntimeError(done.stdout + done.stderr)
        outcomes = {row["obligation"]: row["outcome"] for row in json.loads(done.stdout)["outcomes"]}
        return outcomes["assembly.access@op:insert-pin"]


FAMILY = Family(
    id="fitted_partner_exemption", check="skills/cad-motion/scripts/access.py", owner="pkgE",
    pairs=(Pair("gantry_before_sleeve", "the pin meets its fitted partner before the final engagement", run),))
