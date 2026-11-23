"""Drive the concept viewer's node visibility in headless Chromium, as a user would.

Usage: python drive_visibility.py URL SCENE_PATH
Runs in cad-render's Playwright environment. The scene authors `lower-arm` (a group with
descendants) of `short-reach` as `visible: false`. The driver prints one JSON object that
records what it saw at each step; the test asserts on it.
"""

from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

CHROMIUM_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
SAVED = ("() => { const r = document.getElementById('shot-result');"
         " return r.dataset.kind === 'error' || r.textContent.startsWith('Saved '); }")
SETTLE_MS = 400


def main(url: str, scene_path: str) -> int:
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

            def settle() -> None:
                page.wait_for_timeout(SETTLE_MS)

            def checked() -> dict[str, bool]:
                return page.evaluate("() => Object.fromEntries([...document.querySelectorAll('#node-list input')]"
                                     ".map((box) => [box.dataset.node, box.checked]))")

            def labels() -> list[str]:
                return sorted(page.evaluate("() => [...document.querySelectorAll('.label[data-node]')]"
                                            ".filter((el) => !el.hidden && el.dataset.variant === window.cadConcept.selected).map((el) => el.dataset.node)"))

            def pick(variant: str, node: str) -> str:
                x, y = page.evaluate("([v, n]) => window.cadConcept.screenPoint(v, n)", [variant, node])
                page.mouse.click(x, y)
                settle()
                return page.evaluate("() => window.cadConcept.selected")

            def save() -> str:
                page.click("#screenshot")
                page.wait_for_function(SAVED, timeout=30_000)
                return page.text_content("#shot-result")

            page.click('button.variant[data-variant="short-reach"]')
            settle()
            report["authored_checked"] = checked()
            report["authored_labels"] = labels()
            page.click('button.variant[data-variant="long-reach"]')
            settle()
            report["hidden_pick"] = pick("short-reach", "lower-link")

            page.click('button.variant[data-variant="short-reach"]')
            settle()
            report["save_hidden"] = save()
            page.check('#node-list input[data-node="lower-arm"]')
            settle()
            report["toggled_checked"] = checked()
            report["toggled_labels"] = labels()
            report["save_shown"] = save()
            page.click('button.variant[data-variant="long-reach"]')
            settle()
            report["shown_pick"] = pick("short-reach", "lower-link")

            # A live reload keeps the toggles: hide a descendant, then rewrite the scene file.
            page.click('button.variant[data-variant="short-reach"]')
            page.uncheck('#node-list input[data-node="upper-arm"]')
            with open(scene_path, encoding="utf-8") as handle:
                scene = json.load(handle)
            scene["title"] = "Reloaded lamp"
            with open(scene_path, "w", encoding="utf-8") as handle:
                json.dump(scene, handle)
            page.wait_for_function("() => document.getElementById('scene-title').textContent === 'Reloaded lamp'")
            settle()
            report["reloaded_checked"] = checked()
        finally:
            browser.close()
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
