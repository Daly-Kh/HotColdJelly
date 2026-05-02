#!/usr/bin/env python3
"""
fix_missing_symlinks.py — Create hot symlinks for files that exist on cold but have no hot entry.

Fixes the state where the archive uploaded a file to cold but the hot symlink
was never created (or was deleted), leaving the file stranded on cold with no
pointer on hot and nothing visible in Jellyfin.

For each affected file:
  1. Create the symlink at the hot path pointing to the cold WebDAV mount
  2. Mark the item as cold in Jellyfin (if found in library)

Run from dev/:
    source venv/bin/activate
    python3 helpers/fix_missing_symlinks.py           # dry run
    python3 helpers/fix_missing_symlinks.py --apply   # apply fixes
"""

import os
import sys
import json
import subprocess
import datetime
import argparse
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import HOT_DIRS, EXTS, COLD, RCLONE_BIN, RCLONE_REMOTE, JELLYFIN, HEADERS, USER_IDS
from jellyfin import mark_as_cold

SEP     = "=" * 70
SUB_SEP = "-" * 70

_HOT_BASES = list({os.path.dirname(h) for h in HOT_DIRS})


def list_remote_files():
    """Return list of relative paths for every video file on the cold remote."""
    r = subprocess.run(
        [RCLONE_BIN, "lsjson", RCLONE_REMOTE, "--recursive", "--files-only"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  ✗ rclone lsjson failed: {r.stderr.strip()}")
        sys.exit(1)
    try:
        entries = json.loads(r.stdout)
    except Exception as e:
        print(f"  ✗ Could not parse rclone output: {e}")
        sys.exit(1)
    return [
        e["Path"] for e in entries
        if os.path.splitext(e.get("Path", ""))[1].lower() in EXTS
    ]


def resolve_hot_path(relative):
    """Map a remote relative path to its expected absolute hot path."""
    for base in _HOT_BASES:
        candidate = os.path.join(base, relative)
        if any(candidate.startswith(h) for h in HOT_DIRS):
            return candidate
    return None


def fetch_items_by_path():
    """Fetch all Movie+Episode items from Jellyfin keyed by path."""
    result = {}
    batch  = 500
    start  = 0
    while True:
        r = requests.get(f"{JELLYFIN}/Items", headers=HEADERS, params={
            "recursive":        True,
            "includeItemTypes": "Movie,Episode",
            "fields":           "Path",
            "userId":           USER_IDS[0],
            "limit":            batch,
            "startIndex":       start,
        }, timeout=30)
        r.raise_for_status()
        data = r.json()
        for item in data.get("Items", []):
            path = item.get("Path", "")
            iid  = item.get("Id", "")
            if path and iid:
                result[path] = {"id": iid, "name": item.get("Name", "")}
        start += batch
        if start >= data.get("TotalRecordCount", 0):
            break
    return result


def run(apply: bool):
    mode_label = "APPLY" if apply else "DRY RUN (pass --apply to make changes)"
    print(SEP)
    print(f"fix_missing_symlinks.py — {mode_label}")
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print(SEP)

    print("\nFetching Jellyfin library...", end=" ", flush=True)
    items_map = fetch_items_by_path()
    print(f"{len(items_map)} items")

    print("Listing cold remote (this may take a moment)...", end=" ", flush=True)
    remote_files = list_remote_files()
    print(f"{len(remote_files)} video files on cold")

    candidates = []
    for relative in remote_files:
        hot_path = resolve_hot_path(relative)
        if not hot_path:
            continue
        if os.path.lexists(hot_path):
            continue  # real file or symlink already there — skip
        cold_path = os.path.join(COLD, relative)
        candidates.append((relative, hot_path, cold_path))

    print(f"\nMissing symlinks: {len(candidates)}")
    if not candidates:
        print("\nNothing to fix.")
        return

    print(f"\n{SUB_SEP}")
    fixed = failed = 0

    for relative, hot_path, cold_path in sorted(candidates):
        print(f"\n  {relative}")
        item = items_map.get(hot_path)
        if item:
            print(f"    Jellyfin: {item['name']}  id={item['id']}")
        else:
            print(f"    Jellyfin: not in library (symlink will be created, rescan to re-add)")

        if not apply:
            print(f"    → would create symlink: {hot_path} → {cold_path}")
            fixed += 1
            continue

        try:
            os.makedirs(os.path.dirname(hot_path), exist_ok=True)
            os.symlink(cold_path, hot_path)
            print(f"    ✓ Symlink created")
        except Exception as e:
            print(f"    ✗ Symlink failed: {e}")
            failed += 1
            continue

        if item:
            ok = mark_as_cold(item["id"])
            print(f"    {'✓' if ok else '✗'} mark_as_cold")

        fixed += 1

    print(f"\n{SEP}")
    if apply:
        print(f"Fixed:   {fixed}")
        print(f"Failed:  {failed}")
        if fixed:
            print("\nRun a Jellyfin library rescan to re-add any items not found above.")
    else:
        print(f"Would fix: {fixed}")
        print("No changes made — pass --apply to apply.")
    print(SEP)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply fixes (default: dry run)")
    args = parser.parse_args()
    run(apply=args.apply)
