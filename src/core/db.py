"""
Shared database configuration and connection helper for all analysis scripts.

Usage:
    from core.db import get_connection

    conn = get_connection()
    df = pd.read_sql("SELECT ...", conn)
    conn.close()

Requires MSSQL_SA_PASSWORD environment variable.

Two backends, selected by the DB_BACKEND env var:

  mssql (default)  live on-prem SQL Server over pyodbc — the Desktop/local path.
  duckdb           DuckDB over Delta tables mirrored into a Fabric Lakehouse.
                   Used by the Fabric notebook, which cannot reach on-prem SQL
                   at all (notebooks have no gateway support), so a Dataflow Gen2
                   lands the raw tables in OneLake first and DuckDB queries those
                   with the *same SQL* the pyodbc path uses.

Keeping the SQL identical is the whole point: the two dialect differences that
matter (TOP vs LIMIT) are bridged by top_clause()/limit_clause() below, and the
Bronze Lakehouse tables carry the same names as the source tables so table
references need no rewriting either.
"""

import os
import sys
import warnings

import pandas as pd
import pyodbc

_DRIVER_PREFERENCE = [
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "SQL Server Native Client 11.0",
    "FreeTDS",
    "SQL Server",
]

DB_CONFIG = {
    "server": os.environ.get("DB_SERVER", "localhost,1433"),
    "user": os.environ.get("DB_USER", "sa"),
    "database": os.environ.get("DB_NAME"),
}

# Where the duckdb backend looks for the mirrored tables. Inside a Fabric
# notebook the attached Lakehouse is mounted on the local filesystem, so no
# abfss URI or storage credential is needed.
_DEFAULT_DELTA_ROOT = "/lakehouse/default/Tables"


def _backend():
    """Active backend, read at call time so a notebook can set DB_BACKEND after import."""
    return os.environ.get("DB_BACKEND", "mssql").strip().lower()


def top_clause(n):
    """Head-of-SELECT row limiter — `TOP n` on SQL Server, empty on DuckDB.

    Pairs with limit_clause(): write both into the query and exactly one of them
    renders for the active backend.
    """
    return "" if _backend() == "duckdb" else f"TOP {n}"


def limit_clause(n):
    """Tail-of-query row limiter — `LIMIT n` on DuckDB, empty on SQL Server."""
    return f"LIMIT {n}" if _backend() == "duckdb" else ""


def _pick_driver():
    available = set(pyodbc.drivers())
    for d in _DRIVER_PREFERENCE:
        if d in available:
            return d
    print(
        f"ERROR: no supported SQL Server ODBC driver found.\n"
        f"Installed drivers: {sorted(available)}\n"
        f"Install 'ODBC Driver 17 for SQL Server' from https://aka.ms/odbc17",
        file=sys.stderr,
    )
    sys.exit(1)


def _discover_delta_tables(root):
    """Yield (table_name, scan_expression) for every mirrored table under root.

    Accepts Delta tables (a `_delta_log` directory) and plain Parquet directories.
    Parquet support exists so the pyodbc-vs-duckdb equivalence check can run
    locally off a bounded export without standing up Delta storage first.
    """
    if not os.path.isdir(root):
        raise RuntimeError(
            f"DELTA_TABLES_PATH does not exist: {root}\n"
            "Point it at the Lakehouse Tables mount (Fabric) or a local export dir."
        )
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        posix = path.replace("\\", "/").replace("'", "''")
        if os.path.isdir(os.path.join(path, "_delta_log")):
            yield name, f"delta_scan('{posix}')"
        elif any(f.endswith(".parquet") for f in os.listdir(path)):
            yield name, f"read_parquet('{posix}/**/*.parquet')"


def _naive_projection(con, scan):
    """Select list that strips any timezone tag, or None if there is nothing to strip.

    The Ignition historian stores wall-clock *local* time in naive columns, and
    pyodbc hands those back naive — which is the representation every timestamp
    comparison in this codebase was written against and verified on.

    A Fabric Copy activity, however, writes those same naive values into Parquet as
    UTC-adjusted, so DuckDB reads them as TIMESTAMP WITH TIME ZONE. The clock value
    is intact but the label is wrong, and any session in a non-UTC timezone would
    then shift every timestamp by the offset. Casting back to a naive TIMESTAMP at
    UTC recovers exactly the pyodbc representation. Paired with `SET TimeZone='UTC'`
    below so the cast cannot depend on the host's zone.
    """
    cols = con.execute(f"DESCRIBE SELECT * FROM {scan}").fetchall()
    if not any(str(c[1]).upper() == "TIMESTAMP WITH TIME ZONE" for c in cols):
        return None
    parts = []
    for name, dtype, *_ in cols:
        q = f'"{name}"'
        parts.append(f"{q}::TIMESTAMP AS {q}"
                     if str(dtype).upper() == "TIMESTAMP WITH TIME ZONE" else q)
    return ", ".join(parts)


def _duckdb_connection():
    import duckdb

    root = os.environ.get("DELTA_TABLES_PATH", _DEFAULT_DELTA_ROOT)
    con = duckdb.connect()
    try:
        con.execute("INSTALL delta; LOAD delta;")
    except Exception as e:                                  # noqa: BLE001
        # Only fatal if a Delta table is actually encountered below; a
        # Parquet-only local export needs no extension.
        print(f"  note: delta extension unavailable ({e})", file=sys.stderr)

    # Fixes the reference point for the timezone strip in _naive_projection(), so
    # the same Bronze table reads identically on a workstation and in a notebook.
    con.execute("SET TimeZone='UTC'")

    # The line configs qualify every table as `dbo.<name>`, so mirror that schema
    # here and the existing query text resolves unchanged.
    con.execute("CREATE SCHEMA IF NOT EXISTS dbo")
    registered = normalised = 0
    skipped = []
    for name, scan in _discover_delta_tables(root):
        # Tolerate a table this DuckDB build cannot read. Bronze, Gold and the Copy
        # activity's stage tables share one Lakehouse folder, and the Gold tables are
        # written by a newer delta-rs than the notebook's DuckDB delta extension can
        # parse — a real failure seen as
        #   MalformedJsonError: data did not match any variant of untagged enum DataType
        # on `DESCRIBE ... delta_scan('.../gold_SPC')`. Nothing in the query path reads
        # Gold, so skipping it is correct; failing here would take down the whole run.
        try:
            projection = _naive_projection(con, scan)
            con.execute(f'CREATE OR REPLACE VIEW dbo."{name}" AS '
                        f"SELECT {projection or '*'} FROM {scan}")
        except Exception as e:                              # noqa: BLE001
            skipped.append((name, str(e).splitlines()[0][:120]))
            continue
        if projection:
            normalised += 1
        registered += 1
    if registered == 0:
        raise RuntimeError(f"no readable Delta/Parquet tables found under {root}")
    print(f"  duckdb backend: {registered} table(s) registered from {root}"
          + (f" ({normalised} timezone-normalised)" if normalised else ""))
    for name, err in skipped:
        print(f"  note: skipped unreadable table {name}: {err}", file=sys.stderr)
    return con


def _is_duckdb(conn):
    duckdb = sys.modules.get("duckdb")
    return duckdb is not None and isinstance(conn, duckdb.DuckDBPyConnection)


def get_connection(database=None):
    if _backend() == "duckdb":
        return _duckdb_connection()

    pw = os.environ.get("DB_PASSWORD") or os.environ.get("MSSQL_SA_PASSWORD")
    if not pw:
        print("ERROR: DB_PASSWORD (or MSSQL_SA_PASSWORD) not set", file=sys.stderr)
        sys.exit(1)
    db = database or DB_CONFIG["database"]
    if not db:
        print("ERROR: no database specified — pass database= or set DB_NAME env var", file=sys.stderr)
        sys.exit(1)
    driver = _pick_driver()
    return pyodbc.connect(
        f"DRIVER={{{driver}}};SERVER={DB_CONFIG['server']};"
        f"DATABASE={db};UID={DB_CONFIG['user']};PWD={pw};"
        "TrustServerCertificate=yes;",
        timeout=10,
        readonly=True,
    )


def _null_scalar_to_none(df):
    """Make a single-cell NULL result read as None, matching pyodbc.

    pd.read_sql over pyodbc hands back None for a NULL scalar aggregate; DuckDB's
    .df() hands back NaN/NaT. Several callers test `iloc[0, 0] is None` before
    doing int()/pd.to_datetime() on the value, so normalise just that shape —
    leaving wider frames alone keeps datetime64 columns typed for searchsorted().
    """
    if df.shape == (1, 1) and pd.isna(df.iloc[0, 0]):
        return df.astype(object).where(df.notna(), None)
    return df


def query(conn, sql, params=None):
    if _is_duckdb(conn):
        return _null_scalar_to_none(conn.execute(sql, list(params) if params else []).df())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return pd.read_sql(sql, conn, params=params)
