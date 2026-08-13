"""Resolve delta-rs targets, on OneLake over abfss or on a plain local directory.

Why this exists: **delta-rs cannot write to the Fabric Lakehouse mount.** Writing
to `/lakehouse/default/Tables/<name>` fails with

    OSError: Generic LocalFileSystem error
      Unable to rename file
      Operation not permitted (os error 1)

because delta-rs commits a transaction by atomically renaming the log entry into
place, and the OneLake FUSE mount does not implement rename. Reads through the
mount are fine — only writes are affected — which is why DuckDB's `delta_scan()`
keeps using the mount while every `write_deltalake()` / `DeltaTable(...)` call is
pointed at `abfss://` instead.

The Fabric Copy activity is unaffected because it writes through OneLake's REST
API rather than the mount.

Everything here is a no-op for a local path, so the workstation tests and
bin/verify_duckdb_parity.py keep exercising the same code with no special casing.
"""

import os

ONELAKE_HOST = "onelake.dfs.fabric.microsoft.com"


def is_remote(root):
    return str(root).startswith(("abfss://", "abfs://"))


def join(root, name):
    """Append a table name to either a URI or a filesystem path."""
    if is_remote(root):
        return f"{str(root).rstrip('/')}/{name}"
    return os.path.join(root, name)


def tables_uri(workspace, lakehouse, section="Tables"):
    """Build the OneLake URI for a Lakehouse's Tables (or Files) area.

    Accepts names or GUIDs for both parts; GUIDs are the safer choice inside a
    pipeline because they survive a rename of either item.
    """
    return f"abfss://{workspace}@{ONELAKE_HOST}/{lakehouse}/{section}"


def storage_options(root):
    """Credentials for a remote root, or None for a local one.

    Prefers ONELAKE_TOKEN so the same code can run outside a notebook (tests, ad-hoc
    repair), and falls back to the notebook's own workspace identity.
    """
    if not is_remote(root):
        return None
    token = os.environ.get("ONELAKE_TOKEN")
    if not token:
        try:
            import notebookutils                              # noqa: PLC0415
        except ImportError:
            raise RuntimeError(
                f"{root} needs a bearer token: set ONELAKE_TOKEN, or run inside a "
                "Fabric notebook where notebookutils can issue one."
            ) from None
        token = notebookutils.credentials.getToken("storage")
    return {"bearer_token": token, "use_fabric_endpoint": "true"}
