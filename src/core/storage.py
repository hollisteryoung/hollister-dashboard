"""
core.storage — Serialization and I/O helpers for the analysis pipeline.

stdlib json.dump rejects numpy scalars (int64, float32, etc.) that appear
naturally in pandas/numpy computations.  NumpyEncoder patches that; all
pipeline writes should go through save_analysis_json so the encoding is never
forgotten.  DataFrame helpers (load_csv_dir, load_parquet_dir) provide a
uniform loading interface for ad-hoc data staged outside the live DB.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


class NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy/pandas types."""
    def default(self, obj):
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        return super().default(obj)


def output_root() -> Path:
    """Return the repo's output/ directory as a Path (two levels up from src/core/)."""
    return Path(__file__).parent.parent.parent / "output"


def col_values(result_set, col_name):
    """Extract a single column's values from a result-set dict (columns/rows format)."""
    idx = next(i for i, c in enumerate(result_set["columns"]) if c["name"] == col_name)
    return [row[idx] for row in result_set["rows"]]


def load_analysis_json(cfg, name):
    """Load an analysis JSON file by name (e.g. 'shift-dashboard.json').

    cfg: line config dict with 'line_id' key (as returned by get_line_config()).
    name: filename within output/analyses/<line_id>/, e.g. 'shift-dashboard.json'.

    Returns the parsed JSON object.  Raises FileNotFoundError if the file is missing.
    """
    # Resolve relative to the repo root (two levels up from src/core/).
    repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    path = os.path.join(repo_root, "output", "analyses", cfg["line_id"], name)
    with open(path) as f:
        return json.load(f)


def save_analysis_json(data, path):
    """Write *data* to *path* as indented JSON using NumpyEncoder.

    Creates parent directories if they do not exist.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, cls=NumpyEncoder)


def _rs(sql: str, df, col_specs: list) -> dict:
    """Build an analysis result-set dict from a DataFrame.

    col_specs: list of (name, type[, unit]) tuples or {"name":..,"type":..} dicts.
    Rows are built with build_rows() — see that function for type-coercion rules.
    sql: description of the query / computation that produced df; stored as sql_executed.
    """
    cols = []
    names = []
    for c in col_specs:
        if isinstance(c, dict):
            cols.append(c)
            names.append(c["name"])
        else:
            d = {"name": c[0], "type": c[1]}
            if len(c) > 2 and c[2]:
                d["unit"] = c[2]
            cols.append(d)
            names.append(c[0])
    return {"sql_executed": sql, "columns": cols, "rows": build_rows(df, names)}


def build_output_envelope(
    analysis_id: str,
    parameters: dict,
    result_sets: dict,
    view_spec: dict,
    warnings: list,
    status: str = "ok",
) -> dict:
    """Return the standard analysis output envelope dict.

    Every pipeline script that saves an analysis JSON should call this instead of
    building the dict inline, to guarantee consistent schema across all analyses.
    """
    return {
        "analysis_id": analysis_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "parameters": parameters,
        "status": status,
        "result_sets": result_sets,
        "view_spec": view_spec,
        "warnings": warnings,
    }


def build_rows(df, columns):
    """Convert DataFrame rows to JSON-serializable lists."""
    rows = []
    for _, row in df.iterrows():
        rows.append([
            round(float(row[c]), 2) if isinstance(row[c], (float, np.floating)) else
            int(row[c]) if isinstance(row[c], (int, np.integer)) else
            str(row[c])
            for c in columns
        ])
    return rows
