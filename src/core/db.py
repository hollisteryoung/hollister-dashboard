"""
Shared database configuration and connection helper for all analysis scripts.

Usage:
    from core.db import get_connection

    conn = get_connection()
    df = pd.read_sql("SELECT ...", conn)
    conn.close()

Requires MSSQL_SA_PASSWORD environment variable.
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


def get_connection(database=None):
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


def query(conn, sql, params=None):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return pd.read_sql(sql, conn, params=params)
