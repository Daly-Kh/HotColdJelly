#!/usr/bin/env python3
"""
fix_broken_symlinks.py — Remove broken cold symlinks and clean up Jellyfin metadata.

Fixes audit check A:
  Symlink exists on disk but its target on the cold mount doesn't resolve,
  meaning the file is gone from cold storage.

For each broken symlink:
  1. Remove the dangling symlink from disk
  2. Mark the item as hot in Jellyfin (strip cold-storage tag + suffix)
  3. Update parent Season and Series tags based on real disk state

Run from dev/:
    source venv/bin/activate
    python3 helpers/fix_broken_symlinks.py           # dry run
    python3 helpers/fix_broken_symlinks.py --apply   # apply fixes
"""

import os
import sys
import datetime
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import HOT_DIRS, EXTS
from jellyfin import mark_as_hot, mark_as_cold, mark_as_partial_cold
import requests
from config import JELLYFIN, HEADERS, USER_IDS

COLD_TAG         = "cold-storage"
PARTIAL_COLD_TAG = "partial-cold-storage"
SEP              = "=" * 70
SUB_SEP          = "-" * 70


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
            "id":        iid,
            "name":      item.get("Name", ""),
            "season_id": item.get("SeasonId"),
            "series_id": item.get("SeriesId"),
            "season_name": item.get("SeasonName", ""),
            "series_name": item.get("SeriesName", ""),
            "has_cold_tag": COLD_TAG in tags,
        }
    return result


def fetch_parent_items():
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

def find_broken_symlinks():
    broken = []
    for hot_dir in HOT_DIRS:
        if not os.path.exists(hot_dir):
            continue
        for root, _, fnames in os.walk(hot_dir):
            for fname in fnames:
                if os.path.splitext(fname)[1].lower() not in EXTS:
                    continue
                path = os.path.join(root, fname)
                if os.path.islink(path) and not os.path.exists(path):
                    broken.append(path)
    return broken


def count_season_disk_state(season_path):
    """Return (hot_count, cold_count) for video files in a season folder."""
    hot = cold = 0
    if not os.path.isdir(season_path):
        return 0, 0
    for fname in os.listdir(season_path):
        if os.path.splitext(fname)[1].lower() not in EXTS:
            continue
        fpath = os.path.join(season_path, fname)
        if os.path.islink(fpath):
            if os.path.exists(fpath):   # valid cold symlink
                cold += 1
            # broken symlinks are excluded — we're about to remove them
        elif os.path.isfile(fpath):
            hot += 1
    return hot, cold


# ─── Tag helpers ───────────────────────────────────────────────────────────────

def _expected_tag(cold_eps, hot_eps):
    if cold_eps == 0:
        return "hot"
    if hot_eps == 0:
        return "cold"
    return "partial"


def _actual_tag(jf):
    if jf["has_cold_tag"]:
        return "cold"
    if jf["has_partial_cold_tag"]:
        return "partial"
    return "hot"


def _apply_tag(item_id, target_tag, apply):
    if not apply:
        fn = {"cold": "mark_as_cold", "partial": "mark_as_partial_cold", "hot": "mark_as_hot"}[target_tag]
        return None, f"→ would call {fn}()"
    if target_tag == "cold":
        ok = mark_as_cold(item_id)
    elif target_tag == "partial":
        ok = mark_as_partial_cold(item_id)
    else:
        ok = mark_as_hot(item_id)
    return ok, ("✓" if ok else "✗ FAILED")


# ─── Main ──────────────────────────────────────────────────────────────────────

def run(apply: bool):
    mode_label = "APPLY" if apply else "DRY RUN (pass --apply to make changes)"
    print(SEP)
    print(f"fix_broken_symlinks.py — {mode_label}")
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print(SEP)

    print("\nFetching Jellyfin library...", end=" ", flush=True)
    items_map   = fetch_leaf_items()
    parents_map = fetch_parent_items()
    seasons_map = {k: v for k, v in parents_map.items() if v["type"] == "Season"}
    series_map  = {k: v for k, v in parents_map.items() if v["type"] == "Series"}
    print(f"{len(items_map)} items,  {len(seasons_map)} seasons,  {len(series_map)} series")

    print("Scanning for broken symlinks...", end=" ", flush=True)
    broken = find_broken_symlinks()
    print(f"{len(broken)} found")

    if not broken:
        print("\nNothing to fix.")
        return

    print(f"\n{SUB_SEP}")
    fixed = failed = not_in_jf = 0
    processed_seasons = set()
    processed_series  = set()

    for path in sorted(broken):
        hot_dir     = next((h for h in HOT_DIRS if path.startswith(h)), None)
        rel         = os.path.relpath(path, os.path.dirname(hot_dir)) if hot_dir else path
        info        = items_map.get(path)

        print(f"\n  {rel}")
        print(f"    target: {os.readlink(path)}")

        if not info:
            print(f"    ⚠  Not found in Jellyfin library — removing symlink only")
            if apply:
                try:
                    os.remove(path)
                    print(f"    ✓ Symlink removed")
                except Exception as e:
                    print(f"    ✗ Could not remove symlink: {e}")
            not_in_jf += 1
            continue

        item_id   = info["id"]
        season_id = info.get("season_id")
        series_id = info.get("series_id")
        print(f"    Jellyfin: {info['name']}  id={item_id}")

        # Remove symlink
        if apply:
            try:
                os.remove(path)
                print(f"    ✓ Symlink removed")
            except Exception as e:
                print(f"    ✗ Could not remove symlink: {e}")
                failed += 1
                continue

        # Strip cold tag from item
        ok, label = _apply_tag(item_id, "hot", apply)
        print(f"    mark_as_hot: {label}")
        if apply and not ok:
            failed += 1
            continue
        fixed += 1

        # Update season tag
        if season_id and season_id not in processed_seasons:
            processed_seasons.add(season_id)
            jf_season    = seasons_map.get(season_id)
            season_path  = os.path.dirname(path)
            h, c         = count_season_disk_state(season_path)
            expected     = _expected_tag(c, h)
            actual       = _actual_tag(jf_season) if jf_season else "hot"
            if jf_season and expected != actual:
                print(f"    season '{jf_season['name']}'  disk: {c}c/{h}h  {actual} → {expected}")
                _, lbl = _apply_tag(season_id, expected, apply)
                print(f"    {lbl}")

        # Update series tag
        if series_id and series_id not in processed_series:
            processed_series.add(series_id)
            jf_series   = series_map.get(series_id)
            show_path   = os.path.dirname(os.path.dirname(path))
            total_h = total_c = 0
            if os.path.isdir(show_path):
                for season_dir in os.listdir(show_path):
                    sp = os.path.join(show_path, season_dir)
                    if os.path.isdir(sp):
                        h, c = count_season_disk_state(sp)
                        total_h += h
                        total_c += c
            expected = _expected_tag(total_c, total_h)
            actual   = _actual_tag(jf_series) if jf_series else "hot"
            if jf_series and expected != actual:
                print(f"    series '{jf_series['name']}'  disk: {total_c}c/{total_h}h  {actual} → {expected}")
                _, lbl = _apply_tag(series_id, expected, apply)
                print(f"    {lbl}")

    print(f"\n{SEP}")
    if apply:
        print(f"Fixed:          {fixed}")
        print(f"Not in Jellyfin:{not_in_jf}")
        print(f"Failed:         {failed}")
    else:
        print(f"Broken symlinks to fix: {fixed + not_in_jf}")
        print("No changes made — pass --apply to apply.")
    print(SEP)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply fixes (default: dry run)")
    args = parser.parse_args()
    run(apply=args.apply)
