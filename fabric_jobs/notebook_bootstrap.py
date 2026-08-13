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
#
# Two steps, in order. The merge folds the tail the pipeline's Copy activities just
# landed in stage_* into the Bronze tables (Copy activity has no incremental
# refresh of its own — see fabric_jobs/bronze_merge.py), then the Gold refresh runs
# the real SPC pipeline over Bronze via DuckDB.
#
# The merge is a no-op if no stage tables exist, so this same notebook still works
# unchanged behind the Dataflow Gen2 route, which writes Bronze directly.

import sys

CODE = "/lakehouse/default/Files/code"
for p in (CODE, f"{CODE}/src", f"{CODE}/bin"):
    if p not in sys.path:
        sys.path.insert(0, p)

TABLES = "/lakehouse/default/Tables"

from fabric_jobs.bronze_merge import main as merge_bronze
from fabric_jobs.spc_gold_refresh import main as refresh_gold

merge_bronze(["--bronze", TABLES])
refresh_gold(["--bronze", TABLES, "--gold", TABLES])
