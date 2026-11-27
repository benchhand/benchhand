"""Drive the concept viewer's node visibility in headless Chromium, as a user would.

Usage: python drive_visibility.py URL SCENE_PATH VALID_SOURCE
Runs in cad-render's Playwright environment. The scene authors `lower-arm` (a group with
descendants) of `short-reach` as `visible: false`. The driver prints one JSON object that
records what it saw at each step; the test asserts on it.
"""

from __future__ import annotations

import json
import sys

from playwright.sync_api import Page, sync_playwright

CHROMIUM_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
SAVED = ("() => { const r = document.getElementById('shot-result');"
         " return r.dataset.kind === 'error' || r.textContent.startsWith('Saved '); }")
SETTLE_MS = 400


class Viewer:
    """The viewer page, with the few actions and readings the scenario needs."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def settle(self) -> None:
        self.page.wait_for_timeout(SETTLE_MS)

    def select(self, variant: str) -> None:
        self.page.click(f'button.variant[data-variant="{variant}"]')
        self.settle()

    def checked(self) -> dict[str, bool]:
        return self.page.evaluate("() => Object.fromEntries([...document.querySelectorAll('#node-list input')]"
                                  ".map((box) => [box.dataset.node, box.checked]))")

    def labels(self) -> list[str]:
        """Nodes whose label is shown on the selected variant."""
        return sorted(self.page.evaluate(
            "() => [...document.querySelectorAll('.label[data-node]')]"
            ".filter((el) => !el.hidden && el.dataset.variant === window.cadConcept.selected)"
            ".map((el) => el.dataset.node)"))

    def pick(self, variant: str, node: str) -> str:
        """Click where `node` is on screen; return the variant that ends up selected."""
        x, y = self.page.evaluate("([v, n]) => window.cadConcept.screenPoint(v, n)", [variant, node])
        self.page.mouse.click(x, y)
        self.settle()
        return self.page.evaluate("() => window.cadConcept.selected")

    def save(self) -> str:
        self.page.click("#screenshot")
        self.page.wait_for_function(SAVED, timeout=30_000)
        return self.page.text_content("#shot-result")

    def set_node(self, node: str, on: bool) -> None:
        selector = f'#node-list input[data-node="{node}"]'
        self.page.check(selector) if on else self.page.uncheck(selector)
        self.settle()


def authored(viewer: Viewer, report: dict[str, object]) -> None:
    viewer.select("short-reach")
    report["authored_checked"] = viewer.checked()
    report["authored_labels"] = viewer.labels()
    viewer.select("long-reach")
    report["hidden_pick"] = viewer.pick("short-reach", "lower-link")


def toggled(viewer: Viewer, report: dict[str, object]) -> None:
    viewer.select("short-reach")
    report["save_hidden"] = viewer.save()
    viewer.set_node("lower-arm", True)
    report["toggled_checked"] = viewer.checked()
    report["toggled_labels"] = viewer.labels()
    report["save_shown"] = viewer.save()
    # The PNG shows every variant: hiding something in another variant changes the picture and the key.
    viewer.select("long-reach")
    viewer.set_node("bulb", False)
    viewer.select("short-reach")
    report["save_other_variant"] = viewer.save()
    viewer.select("long-reach")
    report["shown_pick"] = viewer.pick("short-reach", "lower-link")


def reloaded(viewer: Viewer, report: dict[str, object], scene_path: str) -> None:
    """A live reload keeps the toggles: hide a descendant, then rewrite the scene file."""
    viewer.select("short-reach")
    viewer.set_node("upper-arm", False)
    with open(scene_path, encoding="utf-8") as handle:
        scene = json.load(handle)
    scene["title"] = "Reloaded lamp"
    with open(scene_path, "w", encoding="utf-8") as handle:
        json.dump(scene, handle)
    viewer.page.wait_for_function("() => document.getElementById('scene-title').textContent === 'Reloaded lamp'")
    viewer.settle()
    report["reloaded_checked"] = viewer.checked()


def switched(viewer: Viewer, report: dict[str, object], scene_path: str, valid_source: str) -> None:
    """A failed switch to another file leaves the first file's controls on the first file's overrides.

    `other.scene.json` is invalid at first and shares every node id with the first scene. Its checkboxes must
    start from its own file, not from what was toggled while the failed switch was pending.
    """
    other = scene_path.replace("lamp-arm.scene.json", "other.scene.json")
    viewer.page.select_option("#scene-select", "other.scene.json")
    viewer.page.wait_for_function("() => !document.getElementById('notice').hidden")
    viewer.select("short-reach")
    viewer.set_node("base-plate", False)  # the first file is still on screen: this is its override
    report["failed_switch_checked"] = viewer.checked()
    with open(valid_source, encoding="utf-8") as src, open(other, "w", encoding="utf-8") as dst:
        dst.write(src.read())
    viewer.page.wait_for_function("() => document.getElementById('notice').hidden")
    viewer.settle()
    viewer.select("short-reach")
    report["other_checked"] = viewer.checked()
    viewer.page.select_option("#scene-select", "lamp-arm.scene.json")
    viewer.page.wait_for_function("() => window.cadConcept.file === 'lamp-arm.scene.json'")
    viewer.settle()
    viewer.select("short-reach")
    report["back_checked"] = viewer.checked()


def stale_load(viewer: Viewer, report: dict[str, object]) -> None:
    """A slow, older load that arrives after a newer one must change nothing."""
    page = viewer.page
    held: list = []
    gate = {"open": False}
    page.route("**/scenes/other.scene.json", lambda route: route.continue_() if gate["open"] else held.append(route))
    page.select_option("#scene-select", "other.scene.json")  # this request is held
    page.select_option("#scene-select", "lamp-arm.scene.json")  # the newer one answers at once
    viewer.settle()
    gate["open"] = True
    for route in held:
        route.continue_()
    viewer.settle()
    page.unroute("**/scenes/other.scene.json")
    report["stale_title"] = page.text_content("#scene-title")
    report["stale_loaded_file"] = page.evaluate("() => window.cadConcept.loadedFile")
    report["stale_select"] = page.evaluate("() => document.getElementById('scene-select').value")


def save_during_switch(viewer: Viewer, report: dict[str, object]) -> None:
    """Switching files while a save is in flight must not mix the new scene into the old scene's bundle."""
    page = viewer.page
    held: list = []
    gate = {"open": False}

    def hold(route) -> None:
        route.continue_() if gate["open"] else held.append(route)

    page.route("**/scenes/lamp-arm.scene.json", hold)  # a save that re-reads the scene would wait here
    page.route("**/api/screenshot", hold)
    viewer.select("short-reach")
    page.click("#screenshot")
    page.select_option("#scene-select", "other.scene.json")
    page.wait_for_function("() => window.cadConcept.loadedFile === 'other.scene.json'")
    gate["open"] = True
    for route in held:
        route.continue_()
    page.wait_for_function(SAVED, timeout=30_000)
    page.unroute("**/scenes/lamp-arm.scene.json")
    page.unroute("**/api/screenshot")
    report["save_during_switch"] = page.text_content("#shot-result")


def main(url: str, scene_path: str, valid_source: str) -> int:
    errors: list[str] = []
    report: dict[str, object] = {"errors": errors}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=CHROMIUM_ARGS)
        try:
            page = browser.new_page(viewport={"width": 960, "height": 640})
            page.set_default_timeout(60_000)
            page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
            page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
            page.goto(url)
            page.wait_for_function("() => window.cadConcept && window.cadConcept.ready")
            page.evaluate("() => window.cadConcept.ready")
            viewer = Viewer(page)
            authored(viewer, report)
            toggled(viewer, report)
            reloaded(viewer, report, scene_path)
            switched(viewer, report, scene_path, valid_source)
            stale_load(viewer, report)
            save_during_switch(viewer, report)
        finally:
            browser.close()
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
