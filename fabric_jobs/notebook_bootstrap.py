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

# ── Cell 1 — run the refresh ──────────────────────────────────────────────────
#
# There is deliberately no `%pip install` cell. It was tried, pinning
# duckdb==1.5.5 / deltalake==1.6.2, and it does not work: the runtime's
# preinstalled versions (duckdb 1.4.4, deltalake 1.2.1) stay in force, and the
# install still costs a slice of a 15-minute window on every one of ~96 daily runs.
# The code is version-tolerant instead — see bronze_merge._arrow() and the
# per-table fault tolerance in core.db._duckdb_connection() — and both versions are
# printed on every run so the log says what was actually used.
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

# Reads go through the mount; writes must not. delta-rs commits by renaming the log
# entry into place and the OneLake FUSE mount rejects rename with EPERM, so every
# write_deltalake() goes to the abfss endpoint instead. See fabric_jobs/onelake.py.
from fabric_jobs.onelake import tables_uri

MOUNT = "/lakehouse/default/Tables"
WORKSPACE_ID = "daff049b-5e21-4d61-8cf2-465032703de5"   # Smart Factory
LAKEHOUSE_ID = "49675f59-eded-4e77-bbb9-75f9a1fecf97"   # NGP2SPCLakehouse
URI = tables_uri(WORKSPACE_ID, LAKEHOUSE_ID)

from fabric_jobs.bronze_merge import main as merge_bronze
from fabric_jobs.spc_gold_refresh import main as refresh_gold

# Timing-only, to find out where a 15-minute run actually spends its ~9-10
# minutes before optimizing the wrong thing. Remove once the breakdown is known.
import time                                                        # noqa: E402

_t0 = time.perf_counter()
merge_bronze(["--bronze", MOUNT, "--write-root", URI])
_t1 = time.perf_counter()
refresh_gold(["--bronze", MOUNT, "--gold", URI])
_t2 = time.perf_counter()
print(f"\n[timing] merge_bronze: {_t1 - _t0:6.1f}s   refresh_gold: {_t2 - _t1:6.1f}s   "
      f"total: {_t2 - _t0:6.1f}s")
