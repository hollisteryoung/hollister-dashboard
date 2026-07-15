#!/usr/bin/env python3
"""
Render all HTML dashboards from the committed JSON snapshot — no DB required.

This is the Stage-2-only counterpart to render_all_shifts.py.  It reads
the JSON files already present in output/analyses/<line>/ and
output/analyses/<line>/shifts/ and writes self-contained HTML to
output/reports/<line>/.

Use this when you have no database connection and want to (re-)generate
the HTML from the last committed snapshot — e.g. for review, sharing, or
troubleshooting without touching the live historian.

Usage:
    python3 bin/render_snapshot.py [ngp2|hu3]
"""

import argparse
import json
import os
import sys

_SRC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, _SRC_DIR)


def main():
    parser = argparse.ArgumentParser(
        description="Render HTML from the committed JSON snapshot (no DB needed)."
    )
    parser.add_argument("line", nargs="?", default="ngp2", choices=["ngp2", "hu3"])
    args = parser.parse_args()
    line = args.line
    os.environ["LINE"] = line

    from rendering.weekly_report import main as render_weekly
    from rendering.shift_report import render_shift_from_data

    # ── weekly dashboard ───────────────────────────────────────────────────────
    print(f"Rendering weekly dashboard for {line}...", flush=True)
    render_weekly()

    # ── per-shift dashboards ───────────────────────────────────────────────────
    weekly_path = os.path.join(
        "output", "analyses", line, "weekly-dashboard.json"
    )
    with open(weekly_path) as f:
        weekly = json.load(f)

    targets = [s for s in weekly["shifts"] if not s.get("skipped")]
    print(f"\nRendering {len(targets)} shifts...", flush=True)

    ok = skipped = 0
    for s in targets:
        sd, st = s["shift_date"], s["shift_type"]
        json_path = os.path.join(
            "output", "analyses", line, "shifts", f"shift-{sd}-{st}.json"
        )
        if not os.path.exists(json_path):
            print(f"  SKIP {sd} {st} — no bundled JSON at {json_path}", flush=True)
            skipped += 1
            continue
        with open(json_path) as f:
            data = json.load(f)
        render_shift_from_data(data)
        print(f"  {sd} {st}", flush=True)
        ok += 1

    print(f"\nDone — {ok} shift(s) rendered, {skipped} skipped.")
    print(f"Output: output/reports/{line}/")


if __name__ == "__main__":
    main()
