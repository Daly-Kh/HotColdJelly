#!/usr/bin/env python3
"""
cleanup_remote_orphans.py — Find and purge remote folders with no matching Jellyfin item.

An orphan is a folder on the rclone remote whose video file(s) no longer appear
in the Jellyfin library — typically left behind by deletions that happened before
the ItemDeleted webhook handler existed.

Strategy:
  1. List all video files on the remote (rclone lsjson --recursive)
  2. Reconstruct the expected hot path for each remote file
  3. Cross-reference against the current Jellyfin library
  4. Any remote folder with no Jellyfin match → orphan
  5. Dry-run by default; pass --purge to actually delete

Run from dev/:
    source venv/bin/activate
    python3 helpers/cleanup_remote_orphans.py           # dry run
    python3 helpers/cleanup_remote_orphans.py --purge   # delete orphans
"""

import os
import sys
import json
import subprocess
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import HOT_DIRS, EXTS, RCLONE_BIN, RCLONE_REMOTE
from jellyfin import get_all_items

SEP     = "=" * 70
SUB_SEP = "-" * 70

# Base dirs that map to the remote root (one per hot_dir)
_HOT_BASES = list({os.path.dirname(h) for h in HOT_DIRS})


def remote_path_to_hot_path(relative):
    """
    Convert a rclone-relative path (e.g. 'movies/Barbie (2023)/Barbie.mkv')
    to an absolute hot path by trying each known hot base.
    Returns the first match that starts under a known hot_dir, or None.
    """
    for base in _HOT_BASES:
        candidate = os.path.join(base, relative)
        if any(candidate.startswith(h) for h in HOT_DIRS):
            return candidate
    return None


def list_remote_files():
    """
    Return list of dicts: {relative_path, remote_folder, fname}
    for every video file found on the remote.
    """
    print("Listing remote files (this may take a moment)...", flush=True)
    r = subprocess.run(
        [RCLONE_BIN, "lsjson", RCLONE_REMOTE, "--recursive", "--files-only"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(f"ERROR: rclone lsjson failed: {r.stderr.strip()}")
        sys.exit(1)

    try:
        entries = json.loads(r.stdout)
    except Exception as e:
        print(f"ERROR: could not parse rclone output: {e}")
        sys.exit(1)

    files = []
    for entry in entries:
        relative = entry.get("Path", "")
        if os.path.splitext(relative)[1].lower() not in EXTS:
            continue
        files.append({
            "relative":      relative,
            "remote_folder": f"{RCLONE_REMOTE}/{os.path.dirname(relative)}",
            "fname":         os.path.basename(relative),
        })

    print(f"Found {len(files)} video file(s) on remote.\n")
    return files


def find_orphans(remote_files, items_map):
    """
    Return list of orphan dicts: {remote_folder, relative_dir, reason}
    Deduped by remote_folder — one purge per folder is enough.
    """
    seen_folders = {}

    for rf in remote_files:
        hot_path = remote_path_to_hot_path(rf["relative"])

        if hot_path is None:
            reason = "path does not map to any known hot dir"
        elif hot_path in items_map:
            continue  # still in Jellyfin — keep it
        elif os.path.islink(hot_path):
            continue  # symlink on disk — still active cold item
        else:
            reason = "not in Jellyfin library and no symlink on disk"

        folder = rf["remote_folder"]
        if folder not in seen_folders:
            relative_dir = os.path.dirname(rf["relative"])
            seen_folders[folder] = {"remote_folder": folder, "relative_dir": relative_dir, "reason": reason}

    return list(seen_folders.values())


def purge_folder(remote_folder):
    """Purge a remote folder via rclone. Returns True on success."""
    r = subprocess.run(
        [RCLONE_BIN, "purge", remote_folder],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        return True
    print(f"  ERROR: purge failed: {r.stderr.strip()}")
    return False


def run(do_purge=False):
    print(SEP)
    print("HotColdJelly — Remote Orphan Cleanup")
    print(SEP)

    print("\nFetching Jellyfin library...", end=" ", flush=True)
    try:
        items_map = get_all_items()
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    print(f"{len(items_map)} items")

    remote_files = list_remote_files()
    orphans      = find_orphans(remote_files, items_map)

    print(SUB_SEP)
    print(f"Orphaned remote folders: {len(orphans)}")
    print(SUB_SEP)

    if not orphans:
        print("  (none — remote is clean)")
        return

    for o in sorted(orphans, key=lambda x: x["relative_dir"]):
        print(f"  {o['relative_dir']}")
        print(f"    remote: {o['remote_folder']}")
        print(f"    reason: {o['reason']}")
        print()

    if not do_purge:
        print(f"Dry run — {len(orphans)} folder(s) would be purged.")
        print("Re-run with --purge to delete them.")
        return

    print(f"\nPurging {len(orphans)} folder(s)...")
    ok = fail = 0
    for o in orphans:
        print(f"  Purging: {o['relative_dir']} ... ", end="", flush=True)
        if purge_folder(o["remote_folder"]):
            print("OK")
            ok += 1
        else:
            print("FAILED")
            fail += 1

    print(f"\n{SEP}")
    print(f"Done — {ok} purged, {fail} failed.")
    print(SEP)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--purge", action="store_true", help="Actually delete orphaned remote folders")
    args = parser.parse_args()
    run(do_purge=args.purge)
