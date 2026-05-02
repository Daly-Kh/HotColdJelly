"""
fix_recalled_mtimes.py

Fixes incorrect file mtimes on hot files that were recalled from GDrive before
the --use-server-modtime flag was added to the rclone download command.

When a file is recalled, rclone writes a fresh copy to disk which may reset the
mtime to the current time. This makes the archive logic think the file was
"recently added" and keep it hot indefinitely, even if it was originally added
months ago.

This script:
  1. Fetches the full remote listing in two bulk rclone calls (modtime + createdtime)
  2. Walks all hot dirs for non-symlink video files with a suspicious recent mtime
  3. Sanity check: if GDrive createdTime == modifiedTime the mtime was set at
     archive time, not the original file date — warns and skips those files
  4. If the remote mtime is older than the local mtime, restores it
  5. Reports files that can't be fixed (remote already deleted)

Usage:
  python fix_recalled_mtimes.py           # dry run, shows what would change
  python fix_recalled_mtimes.py --apply   # apply the mtime fixes
"""

import os
import sys
import json
import subprocess
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import HOT_DIRS, EXTS, RCLONE_BIN, RCLONE_REMOTE

# Files with mtime newer than this are considered suspicious (possibly reset by recall)
RECENT_DAYS = 7

# Tolerance for comparing created vs modified time (GDrive timestamps can drift by ms)
SAME_TIME_TOLERANCE = datetime.timedelta(seconds=2)

DRY_RUN = "--apply" not in sys.argv


def _parse_modtime(mod_time_str):
    """Parse an RFC3339 modtime string from rclone into a datetime."""
    if not mod_time_str:
        return None
    try:
        s = mod_time_str[:26].rstrip("Z").rstrip("0").rstrip(".")
        return datetime.datetime.fromisoformat(s)
    except Exception:
        return None


def _bulk_list(extra_flags=None):
    """
    Run rclone lsjson --recursive on the full remote root.
    Returns a dict of { relative_path → datetime } or None on failure.
    """
    cmd = [RCLONE_BIN, "lsjson", RCLONE_REMOTE, "--recursive", "--no-mimetype", "--no-modtime=false"]
    if extra_flags:
        cmd += extra_flags

    print(f"  rclone lsjson {RCLONE_REMOTE} {''.join(extra_flags or [])} ...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ERROR: rclone lsjson failed: {r.stderr.strip()}")
        return None

    result = {}
    try:
        for entry in json.loads(r.stdout):
            if entry.get("IsDir"):
                continue
            path = entry.get("Path", "")
            mt   = _parse_modtime(entry.get("ModTime"))
            if path and mt:
                result[path] = mt
    except Exception as e:
        print(f"  ERROR: could not parse lsjson output: {e}")
        return None

    return result


def remote_key(src, hot_dir):
    """
    Convert a local hot file path to the relative path key used in the remote listing.
    Matches the structure produced by hot_to_rclone_dest.
    """
    return os.path.relpath(src, os.path.dirname(hot_dir))


def main():
    now       = datetime.datetime.now()
    threshold = now - datetime.timedelta(days=RECENT_DAYS)

    print("Fetching remote file listing (2 bulk calls)...")
    modtimes    = _bulk_list()
    createdtimes = _bulk_list(["--drive-use-created-date"])
    print()

    if modtimes is None:
        print("Could not fetch remote modtimes. Aborting.")
        sys.exit(1)

    print(f"{'DRY RUN — ' if DRY_RUN else ''}Scanning hot dirs for files with mtime < {RECENT_DAYS}d old...\n")

    fixed         = []
    corrupt_mtime = []

    for hot_dir in HOT_DIRS:
        if not os.path.exists(hot_dir):
            continue
        for root, _, files in os.walk(hot_dir):
            for fname in files:
                if os.path.splitext(fname)[1].lower() not in EXTS:
                    continue
                fpath = os.path.join(root, fname)
                if os.path.islink(fpath):
                    continue

                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
                if mtime < threshold:
                    continue

                # Suspicious mtime — look up in bulk listing
                key      = remote_key(fpath, hot_dir)
                mod_time = modtimes.get(key)

                print(f"  {fname}")
                print(f"    local mtime   : {mtime.strftime('%d/%m/%y %H:%M')}  ({(now - mtime).days}d ago)")

                if mod_time is None:
                    print(f"    remote        : not on cold storage (new file or already recalled+cleaned)")
                    print(f"    → skip")
                    print()
                    continue

                print(f"    remote modtime: {mod_time.strftime('%d/%m/%y %H:%M')}  ({(now - mod_time).days}d ago)")

                created_time = createdtimes.get(key) if createdtimes else None
                if created_time is not None:
                    print(f"    remote created: {created_time.strftime('%d/%m/%y %H:%M')}  ({(now - created_time).days}d ago)")
                    if abs(mod_time - created_time) <= SAME_TIME_TOLERANCE:
                        print(f"    ⚠  modtime == createdtime — mtime was set at archive time, original date lost")
                        print(f"    → unreliable, skipping")
                        print()
                        corrupt_mtime.append((fpath, mtime, mod_time))
                        continue

                if mod_time >= mtime:
                    print(f"    → remote is same age or newer, skipping")
                    print()
                    continue

                age_days = (now - mod_time).days
                if not DRY_RUN:
                    ts = mod_time.timestamp()
                    os.utime(fpath, (ts, ts))
                    print(f"    → FIXED  (restored to {age_days}d ago)")
                else:
                    print(f"    → would restore to {age_days}d ago")
                print()
                fixed.append((fpath, mtime, mod_time))

    print("─" * 60)
    print(f"  Fixed (or would fix)            : {len(fixed)}")
    if corrupt_mtime:
        print(f"  Skipped (archive date on GDrive): {len(corrupt_mtime)}")
        for path, local_mt, remote_mt in corrupt_mtime:
            print(f"    {os.path.basename(path)}")
            print(f"      GDrive modtime {remote_mt.strftime('%d/%m/%y')} == createdtime → original date lost")
    if not fixed and not corrupt_mtime:
        print(f"  Nothing to fix — recent files are either new downloads or already recall-cleaned.")
    if DRY_RUN and fixed:
        print()
        print("  Run with --apply to restore mtimes.")


if __name__ == "__main__":
    main()
