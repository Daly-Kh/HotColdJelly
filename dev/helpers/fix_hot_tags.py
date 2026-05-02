#!/usr/bin/env python3
"""
fix_hot_tags.py — Remove stale cold-storage tags from items that are hot on disk.

Fixes audit check D:
  Hot on disk (real file) but Jellyfin still has cold-storage tag.

After fixing each episode, updates the parent season and series tag
based on actual disk state (cold, partial-cold, or fully hot).

Usage:
    python3 helpers/fix_hot_tags.py           # dry run
    python3 helpers/fix_hot_tags.py --apply   # apply fixes
"""

import os
import sys
import datetime
import argparse
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import HOT_DIRS, EXTS, JELLYFIN, HEADERS, USER_IDS
from jellyfin import mark_as_hot, mark_as_cold, mark_as_partial_cold

COLD_TAG          = "cold-storage"
PARTIAL_COLD_TAG  = "partial-cold-storage"
SEP               = "=" * 70
SUB_SEP           = "-" * 70


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
            "id":                   iid,
            "name":                 item.get("Name", ""),
            "has_cold_tag":         COLD_TAG in tags,
            "has_partial_cold_tag": PARTIAL_COLD_TAG in tags,
            "has_cold_suffix":      any(
                s in (taglines[0] if taglines else "") or s in overview
                for s in (" — ❄ Cold Storage Media", " — ❄ Partial Cold Storage")
            ),
            "season_id":            item.get("SeasonId"),
            "series_id":            item.get("SeriesId"),
            "season_name":          item.get("SeasonName", ""),
            "series_name":          item.get("SeriesName", ""),
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

def walk_all_files():
    """Walk all hot dirs, return (hot_paths list, cold_paths set)."""
    hot  = []
    cold = set()
    for hot_dir in HOT_DIRS:
        if not os.path.exists(hot_dir):
            continue
        for root, _, fnames in os.walk(hot_dir):
            for fname in fnames:
                if os.path.splitext(fname)[1].lower() not in EXTS:
                    continue
                path = os.path.join(root, fname)
                if os.path.islink(path):
                    cold.add(path)
                else:
                    hot.append(path)
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
        return None, f"→ would call {fn}({item_id})"
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
    print(f"fix_hot_tags.py — {mode_label}")
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print(SEP)

    print("\nFetching Jellyfin library...", end=" ", flush=True)
    items_map   = fetch_leaf_items()
    parents_map = fetch_parent_items()
    seasons_map = {k: v for k, v in parents_map.items() if v["type"] == "Season"}
    series_map  = {k: v for k, v in parents_map.items() if v["type"] == "Series"}
    print(f"{len(items_map)} items,  {len(seasons_map)} seasons,  {len(series_map)} series")

    print("Walking hot dirs...", end=" ", flush=True)
    hot_paths, cold_paths = walk_all_files()
    print(f"{len(hot_paths)} hot files,  {len(cold_paths)} cold symlinks")

    # ── Build per-season and per-series cold counts from CURRENT disk state ───
    # This reflects reality BEFORE any fixes — used to determine correct parent tags
    season_cold = {}   # season_id → int
    season_hot  = {}   # season_id → int
    series_cold = {}   # series_id → int
    series_hot  = {}   # series_id → int

    for path in list(cold_paths) + hot_paths:
        info = items_map.get(path)
        if not info:
            continue
        sid = info.get("season_id")
        rid = info.get("series_id")
        is_cold_file = path in cold_paths
        if sid:
            if is_cold_file:
                season_cold[sid] = season_cold.get(sid, 0) + 1
            else:
                season_hot[sid]  = season_hot.get(sid, 0) + 1
        if rid:
            if is_cold_file:
                series_cold[rid] = series_cold.get(rid, 0) + 1
            else:
                series_hot[rid]  = series_hot.get(rid, 0) + 1

    # ── Find hot files with stale cold tag ────────────────────────────────────
    to_fix = [
        (path, items_map[path])
        for path in hot_paths
        if path in items_map and items_map[path]["has_cold_tag"]
    ]

    print(f"\nHot files with stale cold tag: {len(to_fix)}")
    if not to_fix:
        print("  Nothing to fix.")

    fixed_eps  = 0
    failed_eps = 0
    processed_seasons = set()
    processed_series  = set()

    for path, info in to_fix:
        item_id    = info["id"]
        season_id  = info.get("season_id")
        series_id  = info.get("series_id")
        parent_str = f"  [{info['series_name']} / {info['season_name']}]" if season_id else ""
        suffix_note = "  +suffix" if info["has_cold_suffix"] else ""

        print(f"\n  {info['name']}{parent_str}  id={item_id}{suffix_note}")
        ok, label = _apply_tag(item_id, "hot", apply)
        print(f"    {label}")

        if apply and not ok:
            failed_eps += 1
            continue
        fixed_eps += 1

        # season_cold/series_cold were built from cold_paths which excludes hot files,
        # so the counts already reflect the real disk state — no adjustment needed.

        # ── Update season tag based on actual disk counts ─────────────────────
        if season_id and season_id not in processed_seasons:
            processed_seasons.add(season_id)
            jf_season  = seasons_map.get(season_id)
            c          = season_cold.get(season_id, 0)
            h          = season_hot.get(season_id, 0)
            expected   = _expected_tag(c, h)
            actual     = _actual_tag(jf_season) if jf_season else "hot"

            if jf_season and expected != actual:
                print(f"    season '{jf_season['name']}'  disk: {c}c/{h}h  {actual} → {expected}")
                _, lbl = _apply_tag(season_id, expected, apply)
                print(f"    {lbl}")

        # ── Update series tag based on actual disk counts ─────────────────────
        if series_id and series_id not in processed_series:
            processed_series.add(series_id)
            jf_series  = series_map.get(series_id)
            c          = series_cold.get(series_id, 0)
            h          = series_hot.get(series_id, 0)
            expected   = _expected_tag(c, h)
            actual     = _actual_tag(jf_series) if jf_series else "hot"

            if jf_series and expected != actual:
                print(f"    series '{jf_series['name']}'  disk: {c}c/{h}h  {actual} → {expected}")
                _, lbl = _apply_tag(series_id, expected, apply)
                print(f"    {lbl}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    if apply:
        print(f"Episodes fixed:  {fixed_eps}")
        print(f"Episodes failed: {failed_eps}")
    else:
        print(f"Episodes to fix: {fixed_eps}")
        print("No changes made — pass --apply to apply.")
    print(SEP)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Remove stale cold tags from hot items.")
    parser.add_argument("--apply", action="store_true", help="Apply fixes (default: dry run)")
    args = parser.parse_args()
    run(apply=args.apply)
