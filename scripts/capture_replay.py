#!/usr/bin/env python3
"""Drive the replay page in a headless browser and capture it.

Verifies replay the same way the live dashboard is verified: click PLAY, wait
for a recorded frame to actually render, scrub, and photograph the result.

Usage:
    uv run python scripts/capture_replay.py EXP-0009
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
    parser.add_argument("experiment_id")
    parser.add_argument("--base", default="http://127.0.0.1:3000")
    parser.add_argument("--out", default=str(REPO_ROOT / "docs" / "images"))
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 950})
        page.goto(f"{args.base}/replay/{args.experiment_id}", wait_until="networkidle")

        # A recorded frame must actually render, or replay is not working.
        page.wait_for_selector("img[alt='front camera']", timeout=30_000)
        print("recorded frame rendered")

        # Match the button by role, not by text: "text=PLAY" also matches the
        # page title "REPLAY - EXP-...".
        page.get_by_role("button", name="PLAY", exact=True).click()
        page.wait_for_timeout(6000)
        page.get_by_role("button", name="PAUSE", exact=True).click()

        cursor = page.inner_text("text=/^tick \\d+ \\/ \\d+$/")
        print(f"after 6 s of playback: {cursor}")

        page.screenshot(path=out / "replay.png")
        print(f"captured {out / 'replay.png'}")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
