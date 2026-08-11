#!/usr/bin/env python3
"""Upload the pipeline code to the Lakehouse so the Fabric notebook can import it.

The notebook runs the real pipeline rather than a reimplementation of it, so
src/, bin/ and fabric_jobs/ have to be reachable from the notebook's import path.
They are synced to Files/code in the Lakehouse, which the notebook sees mounted at
/lakehouse/default/Files/code.

Uploads via the OneLake ADLS Gen2 endpoint using the caller's `az login` identity.
Re-run after any change to the pipeline code — nothing about this is automatic.

Usage:
    az login
    python fabric_jobs/deploy/sync_code_to_lakehouse.py \
        --workspace "Smart Factory" --lakehouse NGP2SPCLakehouse
"""

import argparse
import fnmatch
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
ONELAKE = "https://onelake.dfs.fabric.microsoft.com"

# Only what the notebook actually imports. Nothing here should carry credentials —
# .env is deliberately excluded; the Lakehouse path needs no database password.
SYNC_DIRS = ("src", "bin", "fabric_jobs")
EXCLUDE = ("__pycache__", "*.pyc", ".env", "*.log", "build", "*.abf", "vendor")


def _token(resource="https://storage.azure.com"):
    out = subprocess.run(
        ["az", "account", "get-access-token", "--resource", resource, "-o", "json"],
        capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"az login required:\n{out.stderr.strip()}")
    return json.loads(out.stdout)["accessToken"]


def _request(method, url, token, data=None):
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("x-ms-version", "2021-06-08")
    if data is not None:
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("Content-Length", str(len(data)))
    try:
        with urllib.request.urlopen(req) as r:
            return r.status
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url} -> {e.code} {e.read()[:400]!r}") from None


def _excluded(rel):
    return any(fnmatch.fnmatch(part, pat) or fnmatch.fnmatch(rel, pat)
               for pat in EXCLUDE for part in rel.replace("\\", "/").split("/"))


def _local_files():
    for d in SYNC_DIRS:
        base = os.path.join(ROOT, d)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if not _excluded(x)]
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, ROOT).replace("\\", "/")
                if not _excluded(rel):
                    yield full, rel


def upload(base_url, token, local, rel):
    """Create + append + flush — the three-call ADLS Gen2 write sequence."""
    with open(local, "rb") as f:
        body = f.read()
    url = f"{base_url}/{rel}"
    _request("PUT", f"{url}?resource=file", token)
    if body:
        _request("PATCH", f"{url}?action=append&position=0", token, data=body)
    _request("PATCH", f"{url}?action=flush&position={len(body)}", token)
    return len(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--lakehouse", default="NGP2SPCLakehouse")
    ap.add_argument("--prefix", default="Files/code")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = sorted(_local_files())
    base = (f"{ONELAKE}/{urllib.parse.quote(args.workspace)}"
            f"/{args.lakehouse}.Lakehouse/{args.prefix}")
    print(f"{len(files)} file(s) -> {base}\n")
    if args.dry_run:
        for _full, rel in files:
            print(f"  {rel}")
        return 0

    token = _token()
    total = 0
    for full, rel in files:
        total += upload(base, token, full, rel)
        print(f"  {rel}")
    print(f"\nUploaded {len(files)} file(s), {total / 1024:.0f} KiB")
    print("Notebook import path: /lakehouse/default/Files/code")
    return 0


if __name__ == "__main__":
    import urllib.parse  # noqa: E402  (used in main's f-string)
    sys.exit(main())
