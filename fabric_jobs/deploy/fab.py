#!/usr/bin/env python3
"""Minimal Fabric REST client — used by the deploy steps and for ad-hoc queries.

Exists because driving the Fabric API through `az rest` on Windows is unreliable
in two specific ways, both hit in practice:

  * `az.exe` is launched via cmd.exe, which mangles quotes in `--query` JMESPath
    expressions and in any non-trivial `--body`.
  * az writes its response through the console codec (cp1252 here), so any
    non-ASCII in a display name comes back as undecodable bytes.

This talks to the API directly over HTTPS and only borrows az for the bearer
token, which sidesteps both. Requires `az login` (tenant-level is enough — Fabric
is not an Azure-subscription resource, so no subscription is needed).

CLI:
    python fab.py get /workspaces
    python fab.py get /workspaces/<id>/lakehouses
    python fab.py post /workspaces/<id>/lakehouses --body '{"displayName":"X"}'
"""

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "https://api.fabric.microsoft.com/v1"
FABRIC_RESOURCE = "https://api.fabric.microsoft.com"
PBI_RESOURCE = "https://analysis.windows.net/powerbi/api"

_TOKENS = {}


def _az():
    """Resolve the az launcher. On Windows it is az.bat, which CreateProcess will
    not find from the bare name "az"."""
    for name in ("az.bat", "az.cmd", "az"):
        path = shutil.which(name)
        if path:
            return path
    sys.exit("az not found on PATH — install with: python -m pip install azure-cli")


def token(resource=FABRIC_RESOURCE):
    if resource not in _TOKENS:
        r = subprocess.run([_az(), "account", "get-access-token",
                            "--resource", resource, "-o", "json"],
                           capture_output=True)
        if r.returncode != 0:
            sys.exit("az login required:\n" + r.stderr.decode("utf-8", "replace")[:600])
        # Decode leniently: az emits console-encoded bytes, and the token itself
        # is always ASCII even when surrounding fields are not.
        _TOKENS[resource] = json.loads(r.stdout.decode("utf-8", "replace"))["accessToken"]
    return _TOKENS[resource]


def call(method, path, body=None, resource=FABRIC_RESOURCE, base=BASE):
    url = path if path.startswith("http") else base + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method.upper(), data=data)
    req.add_header("Authorization", f"Bearer {token(resource)}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            if r.status == 202:
                return {"_status": 202, "_location": r.headers.get("Location")}
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method.upper()} {url} -> {e.code}\n{detail[:1200]}") from None


def get(path, **kw):
    return call("GET", path, **kw)


def post(path, body=None, **kw):
    return call("POST", path, body=body, **kw)


def find(items, name, key="displayName"):
    """Exact-match a display name in an API collection response."""
    for it in items.get("value", items if isinstance(items, list) else []):
        if it.get(key) == name:
            return it
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("method", choices=["get", "post"])
    ap.add_argument("path")
    ap.add_argument("--body")
    ap.add_argument("--pbi", action="store_true", help="use the Power BI v2 audience")
    args = ap.parse_args()
    res = PBI_RESOURCE if args.pbi else FABRIC_RESOURCE
    body = json.loads(args.body) if args.body else None
    out = call(args.method, args.path, body=body, resource=res)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
