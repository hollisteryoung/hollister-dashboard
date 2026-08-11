#!/usr/bin/env python3
"""Prove the DuckDB backend produces the same SPC numbers as live pyodbc.

This is the gate the Fabric migration plan requires before the semantic model is
repointed at Direct Lake: the SPC chart is a real quality control chart, so the
Lakehouse path has to be numerically identical to the path it replaces, not
merely close.

Runs the SPC-only pipeline twice — once against live SQL Server, once against the
local Delta export from bin/export_bronze_local.py — and deep-compares the two
weekly-dashboard-spc.json documents.

Usage:
    source .env
    python bin/verify_duckdb_parity.py
"""

import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "src")))
from core.lines import get_line_config, output_dir   # noqa: E402

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
PIPELINE = os.path.join(ROOT, "src", "pipelines", "weekly_analysis.py")
OUT_JSON = os.path.join(output_dir(get_line_config()), "weekly-dashboard-spc.json")

# Must be strictly less than the --days used for the Delta export, or the DuckDB
# side is asked for history the mirror doesn't have and the diff is meaningless.
LOOKBACK_DAYS = "25"

# Wall-clock and run identity differ by construction; everything else must match.
IGNORE_KEYS = {"generated_at", "generatedAt", "last_refresh"}
TOLERANCE = 1e-9


def _run(label, env_extra):
    env = {**os.environ, "SPC_LOOKBACK_DAYS": LOOKBACK_DAYS, **env_extra}
    print(f"\n=== {label} ===")
    t0 = time.time()
    r = subprocess.run([sys.executable, PIPELINE, "--line", "ngp2", "--spc-only"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:], file=sys.stderr)
        sys.exit(f"{label} pipeline failed (exit {r.returncode})")
    print(f"  ok in {time.time() - t0:.1f}s")
    dest = os.path.join(os.path.dirname(OUT_JSON), f"_parity_{label}.json")
    shutil.copyfile(OUT_JSON, dest)
    with open(dest, encoding="utf-8") as f:
        return json.load(f)


def diff(a, b, path=""):
    """Recursive structural/numeric diff; returns a list of human-readable deltas."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k in IGNORE_KEYS:
                continue
            if k not in a:
                out.append(f"{path}.{k}: missing on mssql side")
            elif k not in b:
                out.append(f"{path}.{k}: missing on duckdb side")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} (mssql) vs {len(b)} (duckdb)"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{path}[{i}]")
        return out
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        if abs(a - b) > TOLERANCE:
            return [f"{path}: {a} (mssql) vs {b} (duckdb)"]
        return []
    if a != b:
        return [f"{path}: {a!r} (mssql) vs {b!r} (duckdb)"]
    return []


def main():
    bronze = os.environ.get("DELTA_TABLES_PATH") or os.path.join(
        os.environ.get("TEMP", "/tmp"), "claude", "c--hollister-dashboard", "bronze")
    if not os.path.isdir(bronze):
        sys.exit(f"no Delta export at {bronze} — run bin/export_bronze_local.py first")

    live = _run("mssql", {"DB_BACKEND": "mssql"})
    lake = _run("duckdb", {"DB_BACKEND": "duckdb", "DELTA_TABLES_PATH": bronze})

    deltas = diff(live, lake)
    print(f"\n{'=' * 60}")
    if not deltas:
        print("PARITY OK — duckdb output is identical to live SQL Server output.")
        return 0
    print(f"PARITY FAILED — {len(deltas)} difference(s):\n")
    for d in deltas[:60]:
        print(f"  {d}")
    if len(deltas) > 60:
        print(f"  ... and {len(deltas) - 60} more")
    return 1


if __name__ == "__main__":
    sys.exit(main())
