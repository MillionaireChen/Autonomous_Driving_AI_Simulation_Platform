#!/usr/bin/env python3
"""Drive the dashboard in a headless browser and capture screenshots.

This is how the dashboard is verified on a machine with no display: it clicks
START, waits for real telemetry to arrive over the WebSocket, and photographs
the result. The images double as the README assets.

Requires the backend, the model service, CARLA and the frontend to be running.

Usage:
    uv run python scripts/capture_dashboard.py --out docs/images
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3000")
    parser.add_argument("--out", default=str(REPO_ROOT / "docs" / "images"))
    parser.add_argument("--settle-ms", type=int, default=25000,
                        help="how long to let the episode run before shooting")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 950})
        page.goto(args.url, wait_until="networkidle")
        page.screenshot(path=out / "dashboard-idle.png")
        print(f"captured {out / 'dashboard-idle.png'}")

        page.click("text=START")
        # Wait for the first camera frame rather than a fixed sleep: that is
        # the proof the whole chain is live.
        page.wait_for_selector("img[alt='front camera']", timeout=90_000)
        print("first camera frame received in the browser")

        page.wait_for_timeout(args.settle_ms)
        page.screenshot(path=out / "dashboard-running.png")
        print(f"captured {out / 'dashboard-running.png'}")

        # Let the episode finish so the result panel is populated.
        try:
            page.wait_for_selector("text=/PASS|FAIL/", timeout=120_000)
            page.wait_for_timeout(1500)
            page.screenshot(path=out / "dashboard-result.png")
            print(f"captured {out / 'dashboard-result.png'}")
        except Exception as exc:
            print(f"result panel did not appear: {exc}")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
