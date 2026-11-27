# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2026 Sjors Robroek
"""Stop the concept viewer's server for good and watch the Live indicator, as a user would.

Usage: python drive_offline.py URL SERVER_PID DEADLINE_S
Runs in cad-render's Playwright environment. The driver loads the viewer, waits until it is live, sends
SIGTERM to the server, then waits up to DEADLINE_S seconds for the indicator to read offline. It prints one
JSON object: the state before the stop, the state and text at the end, and the seconds the transition took.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time

from playwright.sync_api import sync_playwright

CHROMIUM_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
LIVE = "() => document.getElementById('live').dataset.state === 'live'"
OFFLINE = "() => document.getElementById('live').textContent === 'Offline'"


def main(url: str, server_pid: str, deadline_s: str) -> int:
    report: dict[str, object] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=CHROMIUM_ARGS)
        try:
            page = browser.new_page(viewport={"width": 960, "height": 640})
            page.set_default_timeout(60_000)
            page.goto(url)
            page.wait_for_function(LIVE)
            report["before"] = page.text_content("#live")
            os.kill(int(server_pid), signal.SIGTERM)
            started = time.monotonic()
            try:
                page.wait_for_function(OFFLINE, timeout=float(deadline_s) * 1000)
            except Exception:  # a timeout is a result, not a crash: the test names it
                pass
            report["elapsed_s"] = round(time.monotonic() - started, 2)
            report["state"] = page.get_attribute("#live", "data-state")
            report["text"] = page.text_content("#live")
        finally:
            browser.close()
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
