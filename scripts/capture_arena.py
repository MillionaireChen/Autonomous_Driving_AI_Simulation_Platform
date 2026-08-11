#!/usr/bin/env python3
"""Run a match in the Model Arena from a headless browser and capture it.

Usage:
    uv run python scripts/capture_arena.py --a pid --b cnn_il
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
    parser.add_argument("--base", default="http://127.0.0.1:3000")
    parser.add_argument("--a", default="pid")
    parser.add_argument("--b", default="cnn_il")
    parser.add_argument("--out", default=str(REPO_ROOT / "docs" / "images"))
    parser.add_argument("--timeout-s", type=int, default=180)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 950})
        page.goto(f"{args.base}/arena", wait_until="networkidle")

        page.select_option("select >> nth=0", args.a)
        page.select_option("select >> nth=1", args.b)
        page.screenshot(path=out / "arena-idle.png")

        page.get_by_role("button", name="RUN BOTH").click()

        # Both runs are sequential on the one CARLA server, so this waits for
        # the second to finish rather than for the table to first appear.
        deadline = args.timeout_s * 1000
        page.wait_for_function(
            """() => {
                const cells = [...document.querySelectorAll('th div.muted')];
                return cells.length === 2 && cells.every(
                    c => /COMPLETED|FAILED|STOPPED/.test(c.textContent || ''));
            }""",
            timeout=deadline,
        )
        page.wait_for_timeout(1200)
        page.screenshot(path=out / "arena.png")
        print(f"captured {out / 'arena.png'}")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
