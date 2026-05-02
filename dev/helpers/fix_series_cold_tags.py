#!/usr/bin/env python3
"""
fix_series_cold_tags.py — Fix cold/partial-cold tags on episodes, seasons, and series.

Fixes audit checks C, E, F.

Three possible states for seasons and series:
  fully cold   (cold_eps > 0, hot_eps == 0) → cold-storage tag
  partial cold (cold_eps > 0, hot_eps > 0)  → partial-cold-storage tag
  hot          (cold_eps == 0)              → no cold tag

This script determines the correct state from disk and applies it.
Episodes (check C) are always either cold-tagged or not — no partial concept.

Usage:
    python3 helpers/fix_series_cold_tags.py           # dry run
    python3 helpers/fix_series_cold_tags.py --apply   # apply fixes
"""

import os
import sys
import datetime
import argparse
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import HOT_DIRS, EXTS, JELLYFIN, HEADERS, USER_IDS
from jellyfin import mark_as_cold, mark_as_partial_cold, mark_as_hot

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
        "fields":           "Path,Tags,SeriesId,SeasonId,SeriesName,SeasonName",
        "userId":           USER_IDS[0],
    }):
        path = item.get("Path", "")
        iid  = item.get("Id", "")
        if not path or not iid:
            continue
        tags = item.get("Tags", [])
        result[path] = {
            "id":                   iid,
            "name":                 item.get("Name", ""),
            "has_cold_tag":         COLD_TAG in tags,
            "has_partial_cold_tag": PARTIAL_COLD_TAG in tags,
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

def walk_cold_symlinks():
    """Return resolved (non-broken) cold symlinks."""
    files = []
    for hot_dir in HOT_DIRS:
        if not os.path.exists(hot_dir):
            continue
        for root, _, fnames in os.walk(hot_dir):
            for fname in fnames:
                if os.path.splitext(fname)[1].lower() not in EXTS:
                    continue
                path = os.path.join(root, fname)
                if os.path.islink(path) and os.path.exists(path):
                    files.append(path)
    return files


def walk_hot_files():
    files = []
    for hot_dir in HOT_DIRS:
        if not os.path.exists(hot_dir):
            continue
        for root, _, fnames in os.walk(hot_dir):
            for fname in fnames:
                if os.path.splitext(fname)[1].lower() not in EXTS:
                    continue
                path = os.path.join(root, fname)
                if not os.path.islink(path):
                    files.append(path)
    return files


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
    """Call the right Jellyfin function. Returns (ok, label)."""
    if not apply:
        fn_name = {"cold": "mark_as_cold", "partial": "mark_as_partial_cold", "hot": "mark_as_hot"}[target_tag]
        return None, f"→ would call {fn_name}({item_id})"
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
    print(f"fix_series_cold_tags.py — {mode_label}")
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print(SEP)

    print("\nFetching Jellyfin library...", end=" ", flush=True)
    items_map   = fetch_leaf_items()
    parents_map = fetch_parent_items()
    seasons_map = {k: v for k, v in parents_map.items() if v["type"] == "Season"}
    series_map  = {k: v for k, v in parents_map.items() if v["type"] == "Series"}
    print(f"{len(items_map)} items,  {len(seasons_map)} seasons,  {len(series_map)} series")

    print("Walking disk...", end=" ", flush=True)
    cold_paths = walk_cold_symlinks()
    hot_paths  = walk_hot_files()
    print(f"{len(cold_paths)} cold,  {len(hot_paths)} hot")

    # ── Build season and series disk state ────────────────────────────────────
    season_state = {}   # season_id → {cold_eps, hot_eps, season_name, series_name, series_id}
    series_state = {}   # series_id → {cold_eps, hot_eps, series_name}

    for path in cold_paths + hot_paths:
        info = items_map.get(path)
        if not info or not info.get("series_id"):
            continue
        season_id = info["season_id"]
        series_id = info["series_id"]
        is_cold   = os.path.islink(path)

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
            if is_cold:
                season_state[season_id]["cold_eps"] += 1
            else:
                season_state[season_id]["hot_eps"]  += 1
        if series_id:
            if is_cold:
                series_state[series_id]["cold_eps"] += 1
            else:
                series_state[series_id]["hot_eps"]  += 1

    # ── C: Cold episodes missing their tag ────────────────────────────────────
    c_issues = [
        (path, items_map[path])
        for path in cold_paths
        if path in items_map and not items_map[path]["has_cold_tag"]
    ]

    c_fixed = c_failed = 0
    print(f"\n{SUB_SEP}")
    print(f"C. COLD EPISODES MISSING TAG  ({len(c_issues)})")
    print(SUB_SEP)
    if not c_issues:
        print("  (none)")
    else:
        for path, info in c_issues:
            parent_str = f"  [{info['series_name']} / {info['season_name']}]" if info.get("season_id") else ""
            print(f"  {info['name']}{parent_str}  id={info['id']}")
            ok, label = _apply_tag(info["id"], "cold", apply)
            print(f"    {label}")
            print()
            if apply:
                if ok:
                    c_fixed += 1
                else:
                    c_failed += 1
            else:
                c_fixed += 1

    # ── E: Season tag mismatches ──────────────────────────────────────────────
    e_issues = []

    for season_id, state in season_state.items():
        jf = seasons_map.get(season_id)
        if jf is None:
            continue
        expected = _expected_tag(state["cold_eps"], state["hot_eps"])
        actual   = _actual_tag(jf)
        if expected != actual:
            e_issues.append((season_id, state, jf, expected, actual))

    # Seasons tagged in Jellyfin but no episodes found on disk
    for season_id, jf in seasons_map.items():
        if (jf["has_cold_tag"] or jf["has_partial_cold_tag"]) and season_id not in season_state:
            fake_state = {"cold_eps": 0, "hot_eps": 0,
                          "season_name": jf["name"], "series_name": jf["series_name"]}
            e_issues.append((season_id, fake_state, jf, "hot", _actual_tag(jf)))

    e_fixed = e_failed = 0
    print(f"\n{SUB_SEP}")
    print(f"E. SEASON TAG MISMATCHES  ({len(e_issues)})")
    print(SUB_SEP)
    if not e_issues:
        print("  (none)")
    else:
        for season_id, state, jf, expected, actual in sorted(
            e_issues, key=lambda x: (x[1]["series_name"], x[1]["season_name"])
        ):
            total = state["cold_eps"] + state["hot_eps"]
            print(f"  {state['series_name']} — {state['season_name']}")
            print(f"    disk: {state['cold_eps']}c / {state['hot_eps']}h  ({total} total)")
            print(f"    {actual} → {expected}  id={season_id}")
            ok, label = _apply_tag(season_id, expected, apply)
            print(f"    {label}")
            print()
            if apply:
                if ok:
                    e_fixed += 1
                else:
                    e_failed += 1
            else:
                e_fixed += 1

    # ── F: Series tag mismatches ──────────────────────────────────────────────
    f_issues = []

    for series_id, state in series_state.items():
        jf = series_map.get(series_id)
        if jf is None:
            continue
        expected = _expected_tag(state["cold_eps"], state["hot_eps"])
        actual   = _actual_tag(jf)
        if expected != actual:
            f_issues.append((series_id, state, jf, expected, actual))

    for series_id, jf in series_map.items():
        if (jf["has_cold_tag"] or jf["has_partial_cold_tag"]) and series_id not in series_state:
            fake_state = {"cold_eps": 0, "hot_eps": 0, "series_name": jf["name"]}
            f_issues.append((series_id, fake_state, jf, "hot", _actual_tag(jf)))

    f_fixed = f_failed = 0
    print(f"\n{SUB_SEP}")
    print(f"F. SERIES TAG MISMATCHES  ({len(f_issues)})")
    print(SUB_SEP)
    if not f_issues:
        print("  (none)")
    else:
        for series_id, state, jf, expected, actual in sorted(
            f_issues, key=lambda x: x[1]["series_name"]
        ):
            total = state["cold_eps"] + state["hot_eps"]
            print(f"  {state['series_name']}")
            print(f"    disk: {state['cold_eps']}c / {state['hot_eps']}h  ({total} total)")
            print(f"    {actual} → {expected}  id={series_id}")
            ok, label = _apply_tag(series_id, expected, apply)
            print(f"    {label}")
            print()
            if apply:
                if ok:
                    f_fixed += 1
                else:
                    f_failed += 1
            else:
                f_fixed += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    total_fixed  = c_fixed  + e_fixed  + f_fixed
    total_failed = c_failed + e_failed + f_failed

    print(SEP)
    print("SUMMARY")
    print(SEP)
    if apply:
        print(f"  C  Episodes fixed:  {c_fixed}  (failed: {c_failed})")
        print(f"  E  Seasons fixed:   {e_fixed}  (failed: {e_failed})")
        print(f"  F  Series fixed:    {f_fixed}  (failed: {f_failed})")
        print(f"  {'─'*40}")
        print(f"     Total fixed:     {total_fixed}")
        if total_failed:
            print(f"     Total failed:    {total_failed}  ← check Jellyfin connectivity")
    else:
        print(f"  C  Episodes to fix: {c_fixed}")
        print(f"  E  Seasons to fix:  {e_fixed}")
        print(f"  F  Series to fix:   {f_fixed}")
        print(f"  {'─'*40}")
        print(f"     Total to fix:    {total_fixed}")
        print("  No changes made — pass --apply to apply.")
    print(SEP)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix cold/partial-cold tags on seasons and series.")
    parser.add_argument("--apply", action="store_true", help="Apply fixes (default: dry run)")
    args = parser.parse_args()
    run(apply=args.apply)
