#!/usr/bin/env python3
"""
audit.py — Read-only consistency check between disk state and Jellyfin metadata.

Checks:
  A  Broken symlinks         — symlink on disk but file missing from cold remote (data loss)
  B  Jellyfin lost a cold file — symlink on disk but Jellyfin doesn't list it (fix: rescan library)
  C  Missing cold tag        — cold on disk but episode has no cold-storage tag
  D  Stale cold tag          — hot on disk but episode still has cold-storage tag
  E  Season tag mismatch     — season tag doesn't match its actual hot/cold ep counts
  F  Series tag mismatch     — series tag doesn't match its actual hot/cold ep counts
  G  Remote orphans          — file on cold remote but deleted from Jellyfin (wasted space)
  H  Missing hot symlink     — file on cold remote, in Jellyfin, but no symlink on hot

Fix scripts:
  A  → helpers/fix_broken_symlinks.py --apply
  B  → trigger a Jellyfin library rescan
  C  → helpers/fix_series_cold_tags.py --apply
  D  → helpers/fix_hot_tags.py --apply
  E  → helpers/fix_series_cold_tags.py --apply
  F  → helpers/fix_series_cold_tags.py --apply
  G  → helpers/cleanup_remote_orphans.py --purge
  H  → helpers/fix_missing_symlinks.py --apply

Run from dev/:
    source venv/bin/activate
    python3 helpers/audit.py              # full audit (includes remote check)
    python3 helpers/audit.py --skip-remote  # skip slow rclone listing
"""

import os
import sys
import json
import subprocess
import datetime
import argparse
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import HOT_DIRS, EXTS, JELLYFIN, HEADERS, USER_IDS, COLD, RCLONE_BIN, RCLONE_REMOTE

_HOT_BASES = list({os.path.dirname(h) for h in HOT_DIRS})

COLD_TAG          = "cold-storage"
COLD_SUFFIX       = " — ❄ Cold Storage Media"
PARTIAL_COLD_TAG  = "partial-cold-storage"
PARTIAL_COLD_SUFFIX = " — ❄ Partial Cold Storage"


# ─── Jellyfin fetch ────────────────────────────────────────────────────────────

def _paginate(params):
    batch  = 500
    start  = 0
    params = dict(params)
    while True:
        params["limit"]      = batch
        params["startIndex"] = start
        r = requests.get(f"{JELLYFIN}/Items", headers=HEADERS, params=params, timeout=30)
        r.raise_for_status()
        data  = r.json()
        yield from data.get("Items", [])
        start += batch
        if start >= data.get("TotalRecordCount", 0):
            break


def fetch_leaf_items():
    """
    Fetch all Movie + Episode items.
    Returns: {path: {id, name, type, has_cold_tag, has_partial_cold_tag, has_cold_suffix, ...}}
    """
    result = {}
    for item in _paginate({
        "recursive":        True,
        "includeItemTypes": "Movie,Episode",
        "fields":           "Path,Tags,Taglines,Overview,SeriesId,SeasonId,SeriesName,SeasonName",
        "userId":           USER_IDS[0],
    }):
        path = item.get("Path", "")
        iid  = item.get("Id", "")
        if not path or not iid:
            continue
        tags     = item.get("Tags", [])
        taglines = item.get("Taglines", [])
        overview = item.get("Overview", "")
        result[path] = {
            "id":                   iid,
            "name":                 item.get("Name", ""),
            "type":                 item.get("Type", ""),
            "has_cold_tag":         COLD_TAG in tags,
            "has_partial_cold_tag": PARTIAL_COLD_TAG in tags,
            "has_cold_suffix":      (
                (bool(taglines) and (COLD_SUFFIX in taglines[0] or PARTIAL_COLD_SUFFIX in taglines[0]))
                or COLD_SUFFIX in overview or PARTIAL_COLD_SUFFIX in overview
            ),
            "series_id":    item.get("SeriesId"),
            "season_id":    item.get("SeasonId"),
            "series_name":  item.get("SeriesName", ""),
            "season_name":  item.get("SeasonName", ""),
        }
    return result


def fetch_parent_items():
    """
    Fetch all Series + Season items with their tags.
    Returns: {id: {name, type, series_name, has_cold_tag, has_partial_cold_tag}}
    """
    result = {}
    for item_type in ("Series", "Season"):
        for item in _paginate({
            "recursive":        True,
            "includeItemTypes": item_type,
            "fields":           "Tags,SeriesName",
            "userId":           USER_IDS[0],
        }):
            iid = item.get("Id", "")
            if not iid:
                continue
            tags = item.get("Tags", [])
            result[iid] = {
                "name":                 item.get("Name", ""),
                "series_name":          item.get("SeriesName", item.get("Name", "")),
                "type":                 item_type,
                "has_cold_tag":         COLD_TAG in tags,
                "has_partial_cold_tag": PARTIAL_COLD_TAG in tags,
            }
    return result


# ─── Disk walk ─────────────────────────────────────────────────────────────────

def walk_disk():
    files = []
    for hot_dir in HOT_DIRS:
        if not os.path.exists(hot_dir):
            print(f"  ⚠  Missing hot dir (skipping): {hot_dir}")
            continue
        for root, _, fnames in os.walk(hot_dir):
            for fname in fnames:
                if os.path.splitext(fname)[1].lower() not in EXTS:
                    continue
                path    = os.path.join(root, fname)
                is_link = os.path.islink(path)
                files.append({
                    "path":      path,
                    "hot_dir":   hot_dir,
                    "is_cold":   is_link,
                    "is_broken": is_link and not os.path.exists(path),
                })
    return files


def check_remote(items_map):
    """
    List all video files on the rclone remote in one pass and return:
      orphans          — folders with no Jellyfin item and no hot symlink (G)
      missing_symlinks — files in Jellyfin but missing their hot symlink (H)
    Returns (orphans, missing_symlinks) or (None, None) on rclone error.
    """
    r = subprocess.run(
        [RCLONE_BIN, "lsjson", RCLONE_REMOTE, "--recursive", "--files-only"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  ⚠  rclone lsjson failed: {r.stderr.strip()}")
        return None, None

    try:
        entries = json.loads(r.stdout)
    except Exception as e:
        print(f"  ⚠  Could not parse rclone output: {e}")
        return None, None

    orphan_folders   = {}
    missing_symlinks = []

    for entry in entries:
        relative = entry.get("Path", "")
        if os.path.splitext(relative)[1].lower() not in EXTS:
            continue
        hot_path = next(
            (os.path.join(base, relative) for base in _HOT_BASES
             if any(os.path.join(base, relative).startswith(h) for h in HOT_DIRS)),
            None,
        )

        if hot_path and os.path.lexists(hot_path):
            continue  # symlink or real file already on hot — nothing to do

        folder = os.path.dirname(relative)
        if hot_path and hot_path in items_map:
            # In Jellyfin but no hot entry — missing symlink (H)
            missing_symlinks.append({
                "relative":  relative,
                "hot_path":  hot_path,
                "cold_path": os.path.join(COLD, relative),
            })
        else:
            # Not in Jellyfin, no symlink — orphan folder (G)
            if folder not in orphan_folders:
                orphan_folders[folder] = {
                    "relative_dir": folder,
                    "remote_path":  f"{RCLONE_REMOTE}/{folder}",
                }

    return list(orphan_folders.values()), missing_symlinks


def check_cold_mount():
    try:
        return os.path.isdir(COLD) and len(os.listdir(COLD)) > 0
    except Exception:
        return False


# ─── Helpers ───────────────────────────────────────────────────────────────────

SEP     = "=" * 70
SUB_SEP = "-" * 70


def _section(letter, title, count):
    print(f"\n{SUB_SEP}")
    print(f"{letter}. {title}  ({count})")
    print(SUB_SEP)


def _short(path, hot_dir):
    try:
        return os.path.relpath(path, os.path.dirname(hot_dir))
    except ValueError:
        return path


def _expected_tag(cold_eps, hot_eps):
    """Return the tag a season/series SHOULD have based on disk counts."""
    if cold_eps == 0:
        return "hot"
    if hot_eps == 0:
        return "cold"
    return "partial"


def _actual_tag(jf):
    """Return the tag a season/series ACTUALLY has in Jellyfin."""
    if jf["has_cold_tag"]:
        return "cold"
    if jf["has_partial_cold_tag"]:
        return "partial"
    return "hot"


def _tag_label(tag):
    if tag == "cold":
        return "cold-storage ❄"
    if tag == "partial":
        return "partial-cold-storage ⚡"
    return "no cold tag ✓"


# ─── Main ──────────────────────────────────────────────────────────────────────

def run(skip_remote=False):
    print(SEP)
    print("HotColdJelly — Audit Report")
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print(SEP)

    mount_ok = check_cold_mount()
    print(f"\nCold mount ({COLD}): {'✓ accessible' if mount_ok else '✗ NOT accessible'}")
    if not mount_ok:
        print("  ⚠  Broken symlink check cannot distinguish mount-down from file-gone.")

    print("\nFetching Jellyfin library (leaf items)...", end=" ", flush=True)
    try:
        items_map = fetch_leaf_items()
    except Exception as e:
        print(f"\n  ERROR: {e}")
        sys.exit(1)
    print(f"{len(items_map)} items")

    print("Fetching Jellyfin library (series + seasons)...", end=" ", flush=True)
    try:
        parents_map = fetch_parent_items()
    except Exception as e:
        print(f"\n  ERROR: {e}")
        sys.exit(1)
    series_map  = {k: v for k, v in parents_map.items() if v["type"] == "Series"}
    seasons_map = {k: v for k, v in parents_map.items() if v["type"] == "Season"}
    print(f"{len(series_map)} series,  {len(seasons_map)} seasons")

    print("Walking hot dirs...", end=" ", flush=True)
    disk_files = walk_disk()
    cold_files = [f for f in disk_files if f["is_cold"]]
    hot_files  = [f for f in disk_files if not f["is_cold"]]
    broken     = [f for f in disk_files if f["is_broken"]]
    print(f"{len(hot_files)} hot,  {len(cold_files)} cold symlinks,  {len(broken)} broken")

    # ── Build per-season and per-series disk counts ────────────────────────────
    # state[id] = {cold_eps, hot_eps, name, series_name, series_id (season only)}
    season_state = {}
    series_state = {}

    for f in disk_files:
        if f["is_broken"]:
            continue
        info = items_map.get(f["path"])
        if not info or not info.get("series_id"):
            continue
        season_id = info["season_id"]
        series_id = info["series_id"]

        if season_id and season_id not in season_state:
            season_state[season_id] = {
                "cold_eps": 0, "hot_eps": 0,
                "season_name": info["season_name"],
                "series_name": info["series_name"],
                "series_id":   series_id,
            }
        if series_id and series_id not in series_state:
            series_state[series_id] = {
                "cold_eps": 0, "hot_eps": 0,
                "series_name": info["series_name"],
            }

        if season_id:
            if f["is_cold"]:
                season_state[season_id]["cold_eps"] += 1
            else:
                season_state[season_id]["hot_eps"]  += 1
        if series_id:
            if f["is_cold"]:
                series_state[series_id]["cold_eps"] += 1
            else:
                series_state[series_id]["hot_eps"]  += 1

    # ── Check A ────────────────────────────────────────────────────────────────
    _section("A", "BROKEN SYMLINKS", len(broken))
    if not broken:
        print("  (none)")
    else:
        if not mount_ok:
            print("  ⚠  Cold mount is DOWN — likely a mount issue, not missing GDrive files.")
            print("     Remount and re-run before taking action.\n")
        for f in broken:
            target = os.readlink(f["path"])
            print(f"  {_short(f['path'], f['hot_dir'])}")
            print(f"    target: {target}")
            print()

    # ── Check B ────────────────────────────────────────────────────────────────
    b_issues = [f for f in cold_files if not f["is_broken"] and f["path"] not in items_map]
    _section("B", "JELLYFIN LOST A COLD FILE — symlink on disk but not in Jellyfin library", len(b_issues))
    if not b_issues:
        print("  (none)")
    else:
        print("  The file is archived (symlink on disk, data on remote) but Jellyfin no longer lists it.")
        print("  Most likely cause: library rescan missed it, or it was removed from Jellyfin without")
        print("  deleting the file. Fix: trigger a Jellyfin library rescan.\n")
        for f in b_issues:
            print(f"  {_short(f['path'], f['hot_dir'])}")

    # ── Check C ────────────────────────────────────────────────────────────────
    c_issues = [
        f for f in cold_files
        if not f["is_broken"]
        and f["path"] in items_map
        and not items_map[f["path"]]["has_cold_tag"]
    ]
    _section("C", "COLD ON DISK — MISSING COLD TAG IN JELLYFIN (episode level)", len(c_issues))
    if not c_issues:
        print("  (none)")
    else:
        print("  Archived but Jellyfin tag missing. Fix: run helpers/fix_series_cold_tags.py\n")
        for f in c_issues:
            info       = items_map[f["path"]]
            parent_str = f"  [{info['series_name']} / {info['season_name']}]" if info.get("season_id") else ""
            print(f"  {_short(f['path'], f['hot_dir'])}{parent_str}")
            print(f"    jellyfin: {info['name']}  id={info['id']}")
            print()

    # ── Check D ────────────────────────────────────────────────────────────────
    d_issues = [
        f for f in hot_files
        if f["path"] in items_map
        and items_map[f["path"]]["has_cold_tag"]
    ]
    _section("D", "HOT ON DISK — STALE COLD TAG IN JELLYFIN (episode level)", len(d_issues))
    if not d_issues:
        print("  (none)")
    else:
        print("  Recalled but mark_as_hot failed, or file manually restored.")
        print("  Fix: run helpers/fix_hot_tags.py\n")
        for f in d_issues:
            info       = items_map[f["path"]]
            parent_str = f"  [{info['series_name']} / {info['season_name']}]" if info.get("season_id") else ""
            suffix_note = "  (suffix also stale)" if info["has_cold_suffix"] else "  (suffix already clean)"
            print(f"  {_short(f['path'], f['hot_dir'])}{parent_str}")
            print(f"    jellyfin: {info['name']}  id={info['id']}{suffix_note}")
            print()

    # ── Check E ────────────────────────────────────────────────────────────────
    e_issues = []
    for season_id, state in season_state.items():
        jf = seasons_map.get(season_id)
        if jf is None:
            continue
        expected = _expected_tag(state["cold_eps"], state["hot_eps"])
        actual   = _actual_tag(jf)
        if expected != actual:
            total = state["cold_eps"] + state["hot_eps"]
            e_issues.append({
                "id":          season_id,
                "name":        f"{state['series_name']} — {state['season_name']}",
                "expected":    expected,
                "actual":      actual,
                "cold_eps":    state["cold_eps"],
                "hot_eps":     state["hot_eps"],
                "total":       total,
            })
    # Seasons in Jellyfin with a cold/partial tag but zero episodes on disk
    for season_id, jf in seasons_map.items():
        if (jf["has_cold_tag"] or jf["has_partial_cold_tag"]) and season_id not in season_state:
            e_issues.append({
                "id":       season_id,
                "name":     f"{jf['series_name']} — {jf['name']}",
                "expected": "hot",
                "actual":   _actual_tag(jf),
                "cold_eps": 0,
                "hot_eps":  0,
                "total":    0,
            })

    _section("E", "SEASON TAG MISMATCH", len(e_issues))
    if not e_issues:
        print("  (none)")
    else:
        print("  Fix: run helpers/fix_series_cold_tags.py\n")
        for row in sorted(e_issues, key=lambda r: r["name"]):
            print(f"  {row['name']}")
            print(f"    disk: {row['cold_eps']}c / {row['hot_eps']}h  ({row['total']} total)")
            print(f"    expected: {_tag_label(row['expected'])}")
            print(f"    actual:   {_tag_label(row['actual'])}")
            print(f"    id={row['id']}")
            print()

    # ── Check F ────────────────────────────────────────────────────────────────
    f_issues = []
    for series_id, state in series_state.items():
        jf = series_map.get(series_id)
        if jf is None:
            continue
        expected = _expected_tag(state["cold_eps"], state["hot_eps"])
        actual   = _actual_tag(jf)
        if expected != actual:
            total = state["cold_eps"] + state["hot_eps"]
            f_issues.append({
                "id":       series_id,
                "name":     state["series_name"],
                "expected": expected,
                "actual":   actual,
                "cold_eps": state["cold_eps"],
                "hot_eps":  state["hot_eps"],
                "total":    total,
            })
    for series_id, jf in series_map.items():
        if (jf["has_cold_tag"] or jf["has_partial_cold_tag"]) and series_id not in series_state:
            f_issues.append({
                "id":       series_id,
                "name":     jf["name"],
                "expected": "hot",
                "actual":   _actual_tag(jf),
                "cold_eps": 0,
                "hot_eps":  0,
                "total":    0,
            })

    _section("F", "SERIES TAG MISMATCH", len(f_issues))
    if not f_issues:
        print("  (none)")
    else:
        print("  Fix: run helpers/fix_series_cold_tags.py\n")
        for row in sorted(f_issues, key=lambda r: r["name"]):
            print(f"  {row['name']}")
            print(f"    disk: {row['cold_eps']}c / {row['hot_eps']}h  ({row['total']} total)")
            print(f"    expected: {_tag_label(row['expected'])}")
            print(f"    actual:   {_tag_label(row['actual'])}")
            print(f"    id={row['id']}")
            print()

    # ── Checks G + H (one rclone pass) ────────────────────────────────────────
    g_issues = []
    h_issues = []
    if skip_remote:
        print("\nSkipping remote checks (--skip-remote)")
    else:
        print("\nListing cold remote (this may take a moment)...", end=" ", flush=True)
        g_raw, h_raw = check_remote(items_map)
        if g_raw is None:
            print("ERROR — skipping checks G and H")
        else:
            g_issues = g_raw
            h_issues = h_raw
            print(f"{len(g_issues)} orphaned folder(s),  {len(h_issues)} missing symlink(s)")

    _section("G", "REMOTE ORPHANS — folders on cold with no matching Jellyfin item", len(g_issues))
    if skip_remote:
        print("  (skipped — run without --skip-remote to include)")
    elif not g_issues:
        print("  (none)")
    else:
        print("  Fix: run helpers/cleanup_remote_orphans.py --purge\n")
        for o in sorted(g_issues, key=lambda x: x["relative_dir"]):
            print(f"  {o['relative_dir']}")
            print(f"    remote: {o['remote_path']}")
            print()

    _section("H", "MISSING HOT SYMLINK — file on cold, in Jellyfin, but no symlink on hot", len(h_issues))
    if skip_remote:
        print("  (skipped — run without --skip-remote to include)")
    elif not h_issues:
        print("  (none)")
    else:
        print("  Fix: run helpers/fix_missing_symlinks.py --apply\n")
        for h in sorted(h_issues, key=lambda x: x["relative"]):
            print(f"  {h['relative']}")
            print(f"    hot: {h['hot_path']}")
            print()

    # ── Summary ───────────────────────────────────────────────────────────────
    total_issues = len(broken) + len(b_issues) + len(c_issues) + len(d_issues) + len(e_issues) + len(f_issues) + len(g_issues) + len(h_issues)
    print(f"\n{SEP}")
    print("SUMMARY")
    print(SEP)
    print(f"  A  Broken symlinks:                    {len(broken):>4}  →  fix_broken_symlinks.py --apply")
    print(f"  B  Cold on disk, not in Jellyfin:      {len(b_issues):>4}  →  manual review")
    print(f"  C  Missing cold tag (episode):         {len(c_issues):>4}  →  fix_series_cold_tags.py --apply")
    print(f"  D  Stale cold tag (episode):           {len(d_issues):>4}  →  fix_hot_tags.py --apply")
    print(f"  E  Season tag mismatches:              {len(e_issues):>4}  →  fix_series_cold_tags.py --apply")
    print(f"  F  Series tag mismatches:              {len(f_issues):>4}  →  fix_series_cold_tags.py --apply")
    if skip_remote:
        print(f"  G  Remote orphans:                     skipped")
        print(f"  H  Missing hot symlinks:               skipped")
    else:
        print(f"  G  Remote orphans:                     {len(g_issues):>4}  →  cleanup_remote_orphans.py --purge")
        print(f"  H  Missing hot symlinks:               {len(h_issues):>4}  →  fix_missing_symlinks.py --apply")
    print(f"  {'─' * 56}")
    print(f"  Total issues:                          {total_issues:>4}")
    print(SEP)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HotColdJelly audit report")
    parser.add_argument("--skip-remote", action="store_true", help="Skip the rclone remote orphan check (faster)")
    args = parser.parse_args()
    run(skip_remote=args.skip_remote)
