# NGP2 SPC Gold refresh — Fabric notebook cells
#
# Create a **Python** notebook (not PySpark) in the Smart Factory workspace, attach
# the NGP2 SPC Lakehouse as its default lakehouse, and paste the two cells below.
# A Python notebook is deliberate: the output is ~10^4 rows and starts in seconds,
# where a Spark session's 1-2 minute cold start would eat a large slice of a
# 15-minute refresh window.
#
# The pipeline code itself is not duplicated here — it is synced to the Lakehouse
# by fabric_jobs/sync_code_to_lakehouse.py and imported off Files/code.

# ── Cell 1 — dependencies ─────────────────────────────────────────────────────
# Pinned so a silent upstream release can't change SPC numbers between runs.

%pip install -q duckdb==1.5.5 deltalake==1.6.2


# ── Cell 2 — run the refresh ──────────────────────────────────────────────────

import sys

CODE = "/lakehouse/default/Files/code"
for p in (CODE, f"{CODE}/src", f"{CODE}/bin"):
    if p not in sys.path:
        sys.path.insert(0, p)

from fabric_jobs.spc_gold_refresh import main

main(["--bronze", "/lakehouse/default/Tables",
      "--gold",   "/lakehouse/default/Tables"])
