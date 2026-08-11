#!/usr/bin/env python3
"""
powerbi_compute.py — Bridge that makes the Power BI report self-contained.

It recomputes every OEE / downtime / SPC / anomaly metric from the SQL database
by running the *real* pipeline (src/pipelines + metrics + domain), then hands the
results to Power BI as pandas DataFrames. The report therefore depends only on
the database — not on any pre-committed analysis JSON ("the dashboard").

Nothing here re-implements a metric: the numbers come from the exact same code
that produces the HTML reports.

Power BI usage — Get Data ▸ Python script (Import):
    import sys
    sys.path.insert(0, r"C:\\hollister-dashboard\\bin")
    from powerbi_compute import compute_all
    for _name, _df in compute_all().items():
        globals()[_name] = _df      # Shifts, SPC, WeeklySummary, Anomalies, ...

Command line — build step / verification:
    python bin/powerbi_compute.py                # recompute from SQL, list tables
    python bin/powerbi_compute.py --write-csv    # also write powerbi_data/*.csv
    python bin/powerbi_compute.py --no-refresh   # reuse existing JSON (no DB)
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
import export_powerbi_csv as export  # noqa: E402  (build_tables + write_csv)


def _load_env():
    """Load ROOT/.env into os.environ (once) so the credentials work from cmd,
    PowerShell and Power BI's Python — not just a bash `source .env`.

    Accepts `KEY=value` and the project's `export KEY=value`; never overrides a
    variable already set in the real environment.
    """
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


_load_env()

LINES = ("ngp2", "hu3")
WEEKLY = os.path.join("src", "pipelines", "weekly_analysis.py")
SHIFTS = os.path.join("bin", "render_all_shifts.py")


def have_db():
    # The duckdb backend reads Delta tables out of an attached Fabric Lakehouse,
    # which is reached by workspace identity — there is no password to check.
    if os.environ.get("DB_BACKEND", "").strip().lower() == "duckdb":
        return True
    return bool(os.environ.get("DB_PASSWORD") or os.environ.get("MSSQL_SA_PASSWORD"))


def _run(cmd, line):
    """Run a pipeline script for one line with LINE propagated to the env.

    Forces UTF-8 stdout so the pipeline's Unicode prints (→, ×, …) don't crash on
    a cp1252 Windows console or inside Power BI's captured Python output.
    """
    env = dict(os.environ, LINE=line, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    print(f">>> [{line}] {' '.join(cmd)}", flush=True)
    subprocess.run([sys.executable, *cmd], cwd=ROOT, env=env, check=True)


# Under Power BI's pure in-report refresh, the Python source query is executed
# once per table, each in its own python.exe. These guards ensure the
# expensive SQL pipeline runs only ONCE per refresh: the first caller recomputes
# under a cross-process lock and stamps a sentinel; the rest see it "fresh" and
# skip straight to the (fast) flatten step.
#
# The sentinel/lock filenames are scoped by the *sorted set of lines requested*
# (see _scope_suffix): a report that only ever asks for ("ngp2",) must not be
# able to mark a full ("ngp2","hu3") refresh "fresh" for a different report
# that needs both lines, and vice versa — each distinct scope tracks its own
# freshness independently.
_TTL = int(os.environ.get("REFRESH_TTL", "300"))   # seconds


def _scope_suffix(lines, spc_only=False):
    suffix = "-".join(sorted(lines)) or "none"
    return f"{suffix}.spc" if spc_only else suffix


def _sentinel_path(lines, spc_only=False):
    return os.path.join(export.OUT, f".last_refresh.{_scope_suffix(lines, spc_only)}")


def _lock_path(lines, spc_only=False):
    return os.path.join(export.OUT, f".refresh.lock.{_scope_suffix(lines, spc_only)}")


def _fresh(lines, spc_only=False):
    try:
        return (time.time() - os.path.getmtime(_sentinel_path(lines, spc_only))) < _TTL
    except OSError:
        return False


def _acquire_lock(lines, spc_only=False, timeout=900, stale=1800):
    lock = _lock_path(lines, spc_only)
    start = time.time()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > stale:
                    os.remove(lock)          # steal an abandoned lock
                    continue
            except OSError:
                continue
            if time.time() - start > timeout:
                return False
            time.sleep(1)


def refresh_from_db(lines=LINES, force=False, spc_only=False):
    """Recompute all analysis JSON from the live database, reusing the pipeline.

    Analysis-only (``--no-render``): no plotly/jinja2 needed, and no HTML cost.
    Deduplicated so parallel Power BI table queries for the *same* line scope
    trigger only one recompute (see _scope_suffix).

    spc_only=True runs weekly_analysis.py --spc-only instead of the full
    weekly+shifts pair: skips every per-shift OEE/downtime/quality query,
    anomaly/cluster profiling, top-stops, and render_all_shifts.py entirely.
    ~4min per line instead of ~10-11min, for reports that only bind to SPC +
    a lightweight shift list (no OEE/downtime columns available in that mode
    — see export_powerbi_csv.py build_tables(spc_only=True)). Writes to
    weekly-dashboard-spc.json, never touching the full weekly-dashboard.json
    the full dashboard depends on.
    """
    if not have_db():
        raise RuntimeError(
            "No DB password set — cannot recompute from SQL.\n"
            "Add DB_PASSWORD / DB_USER / DB_SERVER to .env, or call with refresh=False."
        )
    os.makedirs(export.OUT, exist_ok=True)
    if not force and _fresh(lines, spc_only):
        return
    got = _acquire_lock(lines, spc_only)
    try:
        if not force and _fresh(lines, spc_only):      # another query refreshed while we waited
            return
        for line in lines:
            t0 = time.time()
            if spc_only:
                _run([WEEKLY, "--line", line, "--spc-only"], line)
                t1 = time.time()
                print(f"### TIMING [{line}] spc-only={t1-t0:.0f}s", flush=True)
            else:
                _run([WEEKLY, "--line", line], line)       # weekly-dashboard.json (incl. SPC)
                t1 = time.time()
                _run([SHIFTS, line, "--no-render"], line)   # per-shift analysis JSON
                t2 = time.time()
                print(f"### TIMING [{line}] weekly(+SPC)={t1-t0:.0f}s  shifts={t2-t1:.0f}s", flush=True)
        with open(_sentinel_path(lines, spc_only), "w") as f:
            f.write(str(time.time()))
    finally:
        if got:
            try:
                os.remove(_lock_path(lines, spc_only))
            except OSError:
                pass


def compute_all(refresh=None, lines=LINES, force=False, tables=None, spc_only=False):
    """Return ``{table_name: DataFrame}`` for the requested slice of the model.

    refresh=None  → recompute from SQL when DB creds are present, else reuse JSON.
    refresh=True  → always recompute from SQL (raises if creds missing).
    refresh=False → skip the DB; flatten whatever analysis JSON already exists.
    force=True    → bypass the freshness guard (used by the CLI build step).
    lines=LINES   → restrict to a subset of lines, e.g. ("ngp2",) for a
                    single-line report — skips that line's DB pipeline run
                    entirely and every returned table is pre-filtered to it.
    tables=None   → return every table (default); pass an iterable of names
                    to only materialize those DataFrames (e.g. a report that
                    only ever binds to a handful of the ~15 available tables).
    spc_only=False → see refresh_from_db. When True, Shifts has no
                    OEE/downtime columns (see export_powerbi_csv.py).
    """
    import pandas as pd

    if refresh is None:
        refresh = have_db()
    if refresh:
        refresh_from_db(lines, force=force, spc_only=spc_only)

    result = {}
    for name, header, rows in export.build_tables(lines=lines, spc_only=spc_only):
        if tables is not None and name not in tables:
            continue
        result[name] = pd.DataFrame(rows, columns=header)
    return result


def check_connection():
    """Fast DB login test — no recompute. Returns True on success."""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from core.db import get_connection
    from core.lines import get_line_config
    server = os.environ.get("DB_SERVER", "localhost,1433")
    user = os.environ.get("DB_USER", "sa")
    if not have_db():
        print("MSSQL_SA_PASSWORD not set (.env not found or missing the key).")
        return False
    cfg = get_line_config("ngp2")
    try:
        conn = get_connection(database=cfg.get("database"))
        cur = conn.cursor(); cur.execute("SELECT 1"); cur.fetchone()
        conn.close()
        print(f"DB CONNECT OK  ->  {user}@{server} / {cfg.get('database')}")
        return True
    except Exception as exc:
        print(f"DB ERROR ({user}@{server}): {type(exc).__name__} :: {str(exc)[:220]}")
        return False


def list_databases():
    """Connect to master and list databases the login can see — helps find the
    real DB names to put in DB_NAME_NGP2 / DB_NAME_HU3."""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from core.db import get_connection
    server = os.environ.get("DB_SERVER", "localhost,1433")
    user = os.environ.get("DB_USER", "sa")
    try:
        conn = get_connection(database="master")
        cur = conn.cursor()
        cur.execute("SELECT name FROM sys.databases ORDER BY name")
        names = [r[0] for r in cur.fetchall()]
        conn.close()
        print(f"Databases visible to {user}@{server} ({len(names)}):")
        for n in names:
            print(f"  {n}")
        return True
    except Exception as exc:
        print(f"Could not list databases ({user}@{server}): "
              f"{type(exc).__name__} :: {str(exc)[:220]}")
        return False


def main():
    if "--check" in sys.argv:
        sys.exit(0 if check_connection() else 1)
    if "--list-dbs" in sys.argv:
        sys.exit(0 if list_databases() else 1)

    refresh = "--no-refresh" not in sys.argv
    if refresh and not have_db():
        print("MSSQL_SA_PASSWORD not set — falling back to existing JSON "
              "(pass --no-refresh to silence).", file=sys.stderr)
        refresh = False

    tables = compute_all(refresh=refresh, force=refresh)
    src = "SQL (live pipeline)" if refresh else "existing analysis JSON"
    print(f"\nComputed {len(tables)} tables from {src}:")
    for name, df in tables.items():
        print(f"  {name:20s} {len(df):6d} rows x {len(df.columns)} cols")

    if "--write-csv" in sys.argv:
        os.makedirs(export.OUT, exist_ok=True)
        for name, df in tables.items():
            export.write_csv(f"{name}.csv", list(df.columns), df.values.tolist())
        print(f"\nWrote CSVs -> {os.path.relpath(export.OUT, ROOT)}/")


if __name__ == "__main__":
    main()
