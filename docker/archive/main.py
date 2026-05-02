import os
import logging
import sys

# Add shared modules to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../shared"))

from config import HOT_DIRS, EXTS, KEEP_HOT, MAX_GB
from jellyfin import get_all_items
from movies import movie_cold_candidate
from shows import get_show_status, show_cold_decision
from storage import show_size_gb, move_to_cold, move_show_to_cold, is_interrupted
from display import print_dry_run
from logger import setup
from stats import ArchiveStats


def collect_candidates(items_map):
    """Walk all hot dirs and classify each item as archive or keep."""
    to_archive = []
    to_keep    = []

    for hot_dir in HOT_DIRS:
        if not os.path.exists(hot_dir):
            print(f"Skipping missing dir: {hot_dir}")
            continue

        is_shows = os.path.basename(hot_dir) == "shows"

        if is_shows:
            for show_path, info in get_show_status(hot_dir, items_map).items():
                size_gb        = show_size_gb(show_path)
                label          = info["show"]
                lp_str         = info["last_activity"].strftime("%Y-%m-%d")
                should, reason = show_cold_decision(info)
                decision       = f"{'COLD' if should else 'STAY'}  | {reason}"
                bucket         = to_archive if should else to_keep
                bucket.append((lp_str, size_gb, label, show_path, decision, info))
        else:
            for root, _, files in os.walk(hot_dir):
                for fname in files:
                    if os.path.splitext(fname)[1].lower() not in EXTS:
                        continue
                    src = os.path.join(root, fname)
                    if os.path.islink(src) or not os.path.exists(src):
                        continue
                    size_gb = os.path.getsize(src) / 1e9
                    info    = items_map.get(src)
                    parts   = os.path.relpath(src, hot_dir).split(os.sep)
                    label   = parts[0] if len(parts) > 1 else fname
                    lp      = info["last_played"] if info else None
                    lp_str  = lp.strftime("%Y-%m-%d") if lp else "never"

                    if fname in KEEP_HOT:
                        to_keep.append((lp_str, size_gb, label, src, "STAY  | KEEP_HOT rule", info))
                        continue

                    should, reason = movie_cold_candidate(info, src)
                    if should is None:
                        continue

                    decision = f"{'COLD' if should else 'STAY'}  | {reason}"
                    bucket   = to_archive if should else to_keep
                    bucket.append((lp_str, size_gb, label, src, decision, info))

    return to_archive, to_keep


def dry_run():
    print("DEBUG: Starting dry_run...", flush=True)
    setup(mode="dry_run")
    print("DEBUG: Setup complete", flush=True)
    print("Fetching Jellyfin library for all users...", flush=True)
    items_map = get_all_items()
    print(f"DEBUG: Got {len(items_map)} items from Jellyfin", flush=True)
    print(f"Merged library: {len(items_map)} items\n", flush=True)
    print("DEBUG: Collecting candidates...", flush=True)
    to_archive, to_keep = collect_candidates(items_map)
    print(f"DEBUG: Found {len(to_archive)} to archive, {len(to_keep)} to keep", flush=True)
    print_dry_run(to_archive, to_keep)


def archive():
    setup(mode="archive")
    stats    = ArchiveStats()
    moved_gb = 0.0

    print("Fetching Jellyfin library for all users...")
    items_map = get_all_items()

    for hot_dir in HOT_DIRS:
        if not os.path.exists(hot_dir) or is_interrupted():
            continue

        is_shows = os.path.basename(hot_dir) == "shows"

        if is_shows:
            for show_path, info in get_show_status(hot_dir, items_map).items():
                if is_interrupted():
                    break
                should, reason = show_cold_decision(info)
                if not should:
                    continue
                size_gb = show_size_gb(show_path)
                if moved_gb + size_gb > MAX_GB:
                    print(f"\nCap of {MAX_GB}GB reached. Stopping.")
                    logging.info(f"Cap reached ({moved_gb:.1f}GB)")
                    break
                print(f"\nArchiving show: {info['show']} ({size_gb:.1f}GB)")
                gb, _ = move_show_to_cold(show_path, hot_dir, items_map=items_map, stats=stats)
                moved_gb += gb
        else:
            for root, _, files in os.walk(hot_dir):
                if is_interrupted():
                    break
                for fname in files:
                    if is_interrupted():
                        break
                    if os.path.splitext(fname)[1].lower() not in EXTS:
                        continue
                    if fname in KEEP_HOT:
                        continue
                    src = os.path.join(root, fname)
                    if os.path.islink(src) or not os.path.exists(src):
                        continue
                    info           = items_map.get(src)
                    should, reason = movie_cold_candidate(info, src)
                    if should is None or not should:
                        continue
                    size_gb = os.path.getsize(src) / 1e9
                    if moved_gb + size_gb > MAX_GB:
                        print(f"\nCap of {MAX_GB}GB reached. Stopping.")
                        logging.info(f"Cap reached ({moved_gb:.1f}GB)")
                        break
                    item_id = info.get("id") if info else None
                    print(f"\nArchiving: {fname} ({size_gb:.1f}GB)")
                    success, fail_reason = move_to_cold(src, hot_dir, item_id=item_id, stats=stats)
                    if success and fail_reason != "already archived":
                        moved_gb += size_gb

    stats.print_summary(interrupted=is_interrupted())


if __name__ == "__main__":
    mode = os.getenv("RUN_MODE", "archive")
    if mode == "dry_run":
        dry_run()
    else:
        archive()