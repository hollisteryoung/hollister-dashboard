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
# once per table (13×), each in its own python.exe. These guards ensure the
# expensive SQL pipeline runs only ONCE per refresh: the first caller recomputes
# under a cross-process lock and stamps a sentinel; the rest see it "fresh" and
# skip straight to the (fast) flatten step.
_SENTINEL = os.path.join(export.OUT, ".last_refresh")
_LOCK = os.path.join(export.OUT, ".refresh.lock")
_TTL = int(os.environ.get("REFRESH_TTL", "300"))   # seconds


def _fresh():
    try:
        return (time.time() - os.path.getmtime(_SENTINEL)) < _TTL
    except OSError:
        return False


def _acquire_lock(timeout=900, stale=1800):
    start = time.time()
    while True:
        try:
            fd = os.open(_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(_LOCK) > stale:
                    os.remove(_LOCK)          # steal an abandoned lock
                    continue
            except OSError:
                continue
            if time.time() - start > timeout:
                return False
            time.sleep(1)


def refresh_from_db(lines=LINES, force=False):
    """Recompute all analysis JSON from the live database, reusing the pipeline.

    Analysis-only (``--no-render``): no plotly/jinja2 needed, and no HTML cost.
    Deduplicated so 13 parallel Power BI table queries trigger only one recompute.
    """
    if not have_db():
        raise RuntimeError(
            "No DB password set — cannot recompute from SQL.\n"
            "Add DB_PASSWORD / DB_USER / DB_SERVER to .env, or call with refresh=False."
        )
    os.makedirs(export.OUT, exist_ok=True)
    if not force and _fresh():
        return
    got = _acquire_lock()
    try:
        if not force and _fresh():      # another query refreshed while we waited
            return
        for line in lines:
            t0 = time.time()
            _run([WEEKLY, "--line", line], line)       # weekly-dashboard.json (incl. SPC)
            t1 = time.time()
            _run([SHIFTS, line, "--no-render"], line)   # per-shift analysis JSON
            t2 = time.time()
            print(f"### TIMING [{line}] weekly(+SPC)={t1-t0:.0f}s  shifts={t2-t1:.0f}s", flush=True)
        with open(_SENTINEL, "w") as f:
            f.write(str(time.time()))
    finally:
        if got:
            try:
                os.remove(_LOCK)
            except OSError:
                pass


def compute_all(refresh=None, lines=LINES, force=False):
    """Return ``{table_name: DataFrame}`` for the whole semantic model.

    refresh=None  → recompute from SQL when DB creds are present, else reuse JSON.
    refresh=True  → always recompute from SQL (raises if creds missing).
    refresh=False → skip the DB; flatten whatever analysis JSON already exists.
    force=True    → bypass the freshness guard (used by the CLI build step).
    """
    import pandas as pd

    if refresh is None:
        refresh = have_db()
    if refresh:
        refresh_from_db(lines, force=force)

    tables = {}
    for name, header, rows in export.build_tables():
        tables[name] = pd.DataFrame(rows, columns=header)
    return tables


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
