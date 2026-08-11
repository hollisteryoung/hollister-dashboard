#!/usr/bin/env python3
"""Phase C — repoint the NPG2 SPC Dashboard semantic model at Direct Lake.

Replaces each table's `mode: import` M partition (which reads through the
Python.Execute connector the enterprise gateway refuses) with a Direct Lake
entity partition over the Gold Lakehouse tables. Direct Lake is a Fabric-to-Fabric
read, so no gateway is involved at all — that is the whole point of the migration.

Columns, measures, relationships, and the report definition are left untouched;
only the partition/connection layer changes. Note expressions.tmdl is rewritten
rather than deleted: Direct Lake still needs one shared named expression, the
AzureStorage.DataLake connector pointing at the Lakehouse.

Run this only after the Lakehouse exists and the notebook has written the gold_*
tables at least once — Direct Lake binds to real columns, so it cannot validate
against tables that are not there yet.

The original .tmdl files are copied to *.import.bak first; --revert restores them.

Usage:
    python fabric_jobs/deploy/convert_model_to_directlake.py \
        --workspace-id <guid> --lakehouse-id <guid>
    python fabric_jobs/deploy/convert_model_to_directlake.py --revert
"""

import argparse
import os
import re
import shutil
import sys
import uuid

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
MODEL = os.path.join(ROOT, "NPG2 SPC Dashboard.SemanticModel", "definition")
TABLES = os.path.join(MODEL, "tables")

EXPRESSION_NAME = "DirectLake - NPG2 SPC Dashboard"
GOLD_PREFIX = "gold_"
MODEL_TABLES = ("Lines", "Shifts", "SPC", "Snapshots")

# Each table file ends with its partition block, so the rewrite truncates from the
# `partition <Name> = m` line. Asserted below rather than assumed.
_PARTITION_RE = re.compile(r"^\tpartition (\S+) = m$", re.MULTILINE)


def _targets():
    files = [os.path.join(TABLES, f"{t}.tmdl") for t in MODEL_TABLES]
    files.append(os.path.join(MODEL, "expressions.tmdl"))
    files.append(os.path.join(MODEL, "model.tmdl"))
    return files


def revert():
    restored = 0
    for path in _targets():
        bak = path + ".import.bak"
        if os.path.exists(bak):
            shutil.copyfile(bak, path)
            os.remove(bak)
            print(f"  restored {os.path.relpath(path, ROOT)}")
            restored += 1
    if not restored:
        sys.exit("no *.import.bak files found — nothing to revert")
    return 0


def _backup(path):
    bak = path + ".import.bak"
    if not os.path.exists(bak):
        shutil.copyfile(path, bak)


def convert_table(name):
    path = os.path.join(TABLES, f"{name}.tmdl")
    with open(path, encoding="utf-8") as f:
        text = f.read()

    matches = list(_PARTITION_RE.finditer(text))
    if len(matches) != 1:
        sys.exit(f"{name}.tmdl: expected exactly one import partition, found {len(matches)}")
    m = matches[0]
    if "partition" in text[m.end():].replace(m.group(0), ""):
        sys.exit(f"{name}.tmdl: partition block is not last — refusing to truncate")

    _backup(path)
    head = text[:m.start()].rstrip("\n")
    partition = (
        f"\tpartition {name} = entity\n"
        f"\t\tmode: directLake\n"
        f"\t\tsource\n"
        f"\t\t\tentityName: {GOLD_PREFIX}{name}\n"
        f"\t\t\texpressionSource: '{EXPRESSION_NAME}'\n"
    )
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{head}\n\n{partition}\n")
    print(f"  {name:10s} -> entity {GOLD_PREFIX}{name}")


def convert_expressions(workspace_id, lakehouse_id):
    path = os.path.join(MODEL, "expressions.tmdl")
    _backup(path)
    url = f"https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}"
    body = (
        f"expression '{EXPRESSION_NAME}' =\n"
        f"\t\tlet\n"
        f'\t\t\tSource = AzureStorage.DataLake("{url}", [HierarchicalNavigation = true])\n'
        f"\t\tin\n"
        f"\t\t\tSource\n"
        f"\tlineageTag: {uuid.uuid4()}\n"
        f"\n"
        f"\tannotation PBI_IncludeFutureArtifacts = 0\n"
    )
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    print(f"  expressions.tmdl -> AzureStorage.DataLake({url})")


def update_model_annotation():
    """Drop PBISource from PBI_QueryOrder — the query no longer exists."""
    path = os.path.join(MODEL, "model.tmdl")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new = text.replace(
        'annotation PBI_QueryOrder = ["Shifts","SPC","Lines","Snapshots","PBISource"]',
        'annotation PBI_QueryOrder = ["Shifts","SPC","Lines","Snapshots"]')
    if new != text:
        _backup(path)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
        print("  model.tmdl -> PBI_QueryOrder without PBISource")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace-id")
    ap.add_argument("--lakehouse-id")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    if args.revert:
        return revert()
    if not (args.workspace_id and args.lakehouse_id):
        ap.error("--workspace-id and --lakehouse-id are required (or use --revert)")

    print("Converting NPG2 SPC Dashboard to Direct Lake:")
    for name in MODEL_TABLES:
        convert_table(name)
    convert_expressions(args.workspace_id, args.lakehouse_id)
    update_model_annotation()
    print("\nOriginals kept as *.import.bak (restore with --revert).\n"
          "Next: publish the model to the Fabric workspace, then verify with a DAX\n"
          "query that row counts match the gold_* tables.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
