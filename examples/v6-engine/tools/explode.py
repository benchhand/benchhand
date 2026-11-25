#!/usr/bin/env -S uv run --script

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["playwright==1.65.2"]
# ///
"""Fully exploded, per-part coloured and labelled view of a built V6 engine version.

Workaround for cad-render's `exploded` view: it follows the joint tree with small gaps and keeps one material per PN.
This script loads the version GLB in the cad-render three.js page stack, moves each occurrence along its own assembly axis,
colours every occurrence, labels it and shoots one PNG.   Usage: uv run --script explode.py GLB OUT.png   (GLB: hw/versions/vN/assembly.glb)
"""

import asyncio
import importlib.util
import json
import math
import re
import struct
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

REPO = Path(__file__).resolve().parents[3]
RENDERER = REPO / "skills/cad-render/assets/renderer"
SERVE = REPO / "skills/cad-concept/scripts/serve.py"

B1 = (0.0, 0.5, math.sqrt(3) / 2)  # bank A axis (head-1, pistons 1,3,5)
B2 = (0.0, -0.5, math.sqrt(3) / 2)  # bank B axis
X = (1.0, 0.0, 0.0)
# distance along the axis, mm; assembly coordinates (+Z up, crank axis X)
STEP = {"rod": 26, "pin": 56, "piston": 92, "head": 205, "cam": 265}
BANK_A = frozenset({"head-1", "cam-a", "cam-pulley-a"})  # plus the odd pistons, pins and rods
# a further move in assembly coordinates, mm: the belt drive slides out along -X, the block and the bracket drop away
SHIFT = {
    "cam-pulley-a": (-55, 0, 0), "cam-pulley-b": (-55, 0, 0), "crank-pulley": (-65, 0, 0), "belt": (-140, 0, 0),
    "bracket": (-35, 0, -115), "idler": (-90, 0, -115), "tensioner": (-90, 0, -115),
    "VSE-0005": (-30, 0, 0), "VSE-0001": (0, 0, -115),
}


def plan(occ: str, pn: str) -> tuple[float, float, float]:
    kind = occ.split("-")[0]
    odd_row = kind in ("piston", "pin", "rod") and occ.endswith(("1", "3", "5"))
    bank = B1 if odd_row or occ in BANK_A else B2
    d = STEP.get(kind, 0.0)
    shift = SHIFT.get(occ, (0, 0, 0))
    off = [bank[i] * d + shift[i] for i in range(3)]
    # keep bank 1/2 cylinder rows apart in X on the pin and rod rows so wrists clear each other
    if kind == "pin":
        off[0] += 14 if int(occ.rsplit("-", 1)[1]) % 2 else -14
    return (off[0], off[1], off[2])


HTML = """<!doctype html><meta charset=utf-8><style>html,body{margin:0;background:#f3f5f8;font:12px/1.25 system-ui,sans-serif}
#c{position:absolute;inset:0;z-index:0}.l{z-index:6;position:absolute;color:#10243a;font-weight:600;font-size:11px;white-space:nowrap;
text-shadow:0 0 3px #fff,0 0 3px #fff,0 0 3px #fff;transform:translate(6px,-8px)}
#key{z-index:5;position:absolute;left:14px;top:12px;background:#ffffffd9;border:1px solid #c7ced8;border-radius:6px;padding:8px 10px}
#key b{display:block;margin-bottom:4px;font-size:13px}.k{display:flex;gap:6px;align-items:center;margin:2px 0}.s{width:11px;height:11px;border-radius:2px;border:1px solid #0003}</style>
__IMPORTMAP__
<div id=key><b>Exploded V6, each part on its own axis</b></div><canvas id=c></canvas>
<script type=module>
import * as THREE from "three";
import {GLTFLoader} from "three/addons/loaders/GLTFLoader.js";
import {RoomEnvironment} from "three/addons/environments/RoomEnvironment.js";
const params = await (await fetch("/params.json")).json();
const W = params.w, H = params.h;
const renderer = new THREE.WebGLRenderer({canvas: document.getElementById("c"), antialias: true, preserveDrawingBuffer: true});
renderer.setSize(W, H, false); renderer.setPixelRatio(1);
const scene = new THREE.Scene(); scene.background = new THREE.Color(0xf3f5f8);
const pm = new THREE.PMREMGenerator(renderer); scene.environment = pm.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.55; const sun = new THREE.DirectionalLight(0xffffff, 1.1); sun.position.set(2, 4, 3); scene.add(sun);
const gltf = await new GLTFLoader().loadAsync("/model.glb");
const root = gltf.scene; scene.add(root);
const asm2three = (v) => new THREE.Vector3(v[0], v[2], -v[1]).multiplyScalar(0.001);
const key = document.getElementById("key"); const labels = [];
let i = 0;
const nodeInfo = {}; for (const [k, v] of Object.entries(params.nodes)) nodeInfo[THREE.PropertyBinding.sanitizeNodeName(k)] = v;
root.traverse((o) => {
  const info = nodeInfo[o.name]; if (!info) return;
  const hue = (i * 0.61803398875) % 1; i++;
  const col = new THREE.Color().setHSL(hue, 0.70, 0.45);
  o.traverse((m) => { if (m.isMesh) { m.material = new THREE.MeshStandardMaterial({color: col, metalness: 0.1, roughness: 0.55}); } });
  o.position.add(asm2three(info.offset));
  const row = document.createElement("div"); row.className = "k";
  row.innerHTML = `<span class=s style="background:#${col.getHexString()}"></span>${info.tag}  ${info.title}`; key.appendChild(row);
  labels.push({o, tag: info.tag});
});
root.updateMatrixWorld(true);
const box = new THREE.Box3().setFromObject(root); const ctr = box.getCenter(new THREE.Vector3()); const size = box.getSize(new THREE.Vector3());
const dir = asm2three(params.dir).normalize();
const r = size.length() / 2;
const cam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.001, 20);
cam.position.copy(ctr).addScaledVector(dir, 4); cam.up.set(0, 1, 0); cam.lookAt(ctr); cam.updateMatrixWorld(true);
// fit: project the 8 box corners
cam.updateProjectionMatrix();
const inv = cam.matrixWorldInverse; let minx = 1e9, maxx = -1e9, miny = 1e9, maxy = -1e9;
for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
  const p = new THREE.Vector3(x, y, z).applyMatrix4(inv); minx = Math.min(minx, p.x); maxx = Math.max(maxx, p.x); miny = Math.min(miny, p.y); maxy = Math.max(maxy, p.y); }
const cx = (minx + maxx) / 2, cy = (miny + maxy) / 2; let hw = (maxx - minx) / 2 * 1.08, hh = (maxy - miny) / 2 * 1.08;
const aspect = W / H; if (hw / hh < aspect) hw = hh * aspect; else hh = hw / aspect;
hw *= 1.0; cam.left = cx - hw; cam.right = cx + hw; cam.top = cy + hh; cam.bottom = cy - hh; cam.updateProjectionMatrix();
renderer.render(scene, cam);
for (const l of labels) {
  const b = new THREE.Box3().setFromObject(l.o); const p = b.getCenter(new THREE.Vector3()).project(cam);
  const d = document.createElement("div"); d.className = "l"; d.textContent = l.tag;
  d.style.left = ((p.x + 1) / 2 * W) + "px"; d.style.top = ((1 - p.y) / 2 * H) + "px"; document.body.appendChild(d);
}
window.__done = true;
</script>"""


def _load_serve():
    spec = importlib.util.spec_from_file_location("serve", SERVE)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return serve


def _nodes(raw: bytes) -> dict:
    """GLB node name -> its explode offset, label tag and short title."""
    j = json.loads(raw[20:20 + struct.unpack("<I", raw[12:16])[0]])
    nodes = {}
    for n in j["nodes"]:
        name = n.get("name", "")
        m = re.match(r"(VSE-\d+) (.*?)(?: \[@([\w-]+)\])?$", name)
        if not m or name.startswith("example"):
            continue
        pn, title = m.group(1), m.group(2)
        occ = m.group(3) or pn
        nodes[name] = {"offset": plan(occ, pn), "tag": occ.replace("VSE-0001", "crankcase").replace("VSE-0005", "crankshaft"), "title": title.split(",")[0][:34]}
    return nodes


def _handler(routes: dict, vendor, vendor_failures: list[str]) -> type[BaseHTTPRequestHandler]:
    """A request handler serving `routes` (path -> (body, content type)) and the pinned vendor modules."""

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _error(self, status, message):
            print(f"{status.value} {self.path}: {message}", file=sys.stderr)
            self._s(message.encode(), "text/plain; charset=utf-8", status)

        def _s(self, body, ct, status=HTTPStatus.OK):
            self.send_response(status); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            p = unquote(self.path.split("?")[0])
            if p in routes: return self._s(*routes[p])
            if p.startswith("/vendor/"):
                try: return self._s(vendor.get(p), "text/javascript")
                except LookupError: return self._error(HTTPStatus.NOT_FOUND, f"{p} is not a pinned module")
                except OSError as exc:  # a failed fetch or a digest mismatch, not a missing resource
                    vendor_failures.append(str(exc))
                    return self._error(HTTPStatus.BAD_GATEWAY, str(exc))
            self._error(HTTPStatus.NOT_FOUND, f"{p} not found")

    return H


async def _shoot(port: int, params: dict, out: Path, vendor_failures: list[str]) -> None:
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        ctx = await b.new_context(viewport={"width": params["w"], "height": params["h"]})
        pg = await ctx.new_page()
        pg.on("console", lambda m: print("console:", m.text, file=sys.stderr))
        pg.on("pageerror", lambda e: print("pageerror:", e, file=sys.stderr))
        await pg.goto(f"http://127.0.0.1:{port}/")
        for _ in range(180):  # one-second waits, so a vendor failure ends the run at once instead of at the timeout
            if vendor_failures:
                return
            try:
                await pg.wait_for_function("window.__done === true", timeout=1000)
                break
            except PlaywrightTimeoutError:
                continue
        else:
            raise TimeoutError("the page did not finish in 180 s")
        await pg.screenshot(path=str(out))
        await b.close()


def main() -> None:
    glb, out = Path(sys.argv[1]), Path(sys.argv[2])
    serve = _load_serve()
    index = (RENDERER / "index.html").read_text()
    imap = re.search(r"<script type=\"importmap\">.*?</script>", index, re.S).group(0)
    vendor = serve.VendorCache(serve.default_cache_dir(), serve.vendor_integrity(RENDERER / "index.html"))
    raw = glb.read_bytes()
    params = {"w": 1500, "h": 1400, "dir": (0.35, -1.4, 0.5), "nodes": _nodes(raw)}
    page_html = HTML.replace("__IMPORTMAP__", imap).encode()
    routes = {"/": (page_html, "text/html"), "/index.html": (page_html, "text/html"),
              "/params.json": (json.dumps(params).encode(), "application/json"), "/model.glb": (raw, "model/gltf-binary")}
    vendor_failures: list[str] = []

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler(routes, vendor, vendor_failures))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    try:
        asyncio.run(_shoot(port, params, out, vendor_failures))
    finally:
        srv.shutdown()
        if vendor_failures:
            sys.exit("explode.py: the renderer's vendor modules failed to load: " + "; ".join(vendor_failures))
    print("wrote", out)


main()
