import os
import json
import subprocess
import datetime
import logging
import signal
import sys
import time

from config import (
    COLD, EXTS,
    RCLONE_BIN, RCLONE_REMOTE,
    RCLONE_CHUNK_SIZE, RCLONE_UPLOAD_CUTOFF, RCLONE_STATS_INTERVAL,
    MAX_RETRIES, RETRY_WAIT_SEC,
)

# ─── Interruption handling ─────────────────────────────────────────────────────

_interrupted = False


def _handle_sigint(sig, frame):
    global _interrupted
    if not _interrupted:
        print("\n\n⚠  Ctrl+C — finishing current file then stopping safely...")
        print("   Press Ctrl+C again to force quit\n")
        _interrupted = True
    else:
        print("\nForce quitting...")
        sys.exit(1)


signal.signal(signal.SIGINT, _handle_sigint)


def is_interrupted():
    return _interrupted

# ─── Path helpers ──────────────────────────────────────────────────────────────

def file_age_days(path):
    """Days since file was last modified on disk."""
    mtime = os.path.getmtime(path)
    return (datetime.datetime.now() - datetime.datetime.fromtimestamp(mtime)).days


def oldest_file_age_days(folder):
    """
    Age in days of the oldest video file in a folder tree.
    Proxy for when content was added to the library.
    """
    oldest = None
    for root, _, files in os.walk(folder):
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in EXTS:
                continue
            fpath = os.path.join(root, fname)
            if os.path.islink(fpath):
                continue
            mtime = os.path.getmtime(fpath)
            if oldest is None or mtime < oldest:
                oldest = mtime
    if oldest is None:
        return None
    return (datetime.datetime.now() - datetime.datetime.fromtimestamp(oldest)).days


def hot_to_cold_path(src, hot_dir):
    """
    Hot path → cold WebDAV mount path.
    Symlinks point here so Jellyfin can stream cold content.
    """
    return os.path.join(COLD, os.path.relpath(src, os.path.dirname(hot_dir)))


def hot_to_rclone_dest(src, hot_dir):
    """
    Hot file path → rclone remote destination folder.
    rclone copy uploads the file INTO this folder.
    """
    relative_dir = os.path.relpath(os.path.dirname(src), os.path.dirname(hot_dir))
    return f"{RCLONE_REMOTE}/{relative_dir}"


def show_size_gb(show_path):
    """Total GB of non-symlink video files in a show folder."""
    total = 0.0
    for root, _, files in os.walk(show_path):
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in EXTS:
                continue
            fpath = os.path.join(root, fname)
            if not os.path.islink(fpath) and os.path.exists(fpath):
                total += os.path.getsize(fpath) / 1e9
    return total

# ─── Remote helpers ────────────────────────────────────────────────────────────

def file_exists_on_remote(src, hot_dir):
    """
    Check if file exists on remote with correct size.
    Uses rclone lsjson — no download needed.
    Retries 3× on transient errors.
    Returns (exists: bool, size_matches: bool)
    """
    rclone_dest = hot_to_rclone_dest(src, hot_dir)
    fname       = os.path.basename(src)
    src_size    = os.path.getsize(src)

    for attempt in range(3):
        r = subprocess.run(
            [RCLONE_BIN, "lsjson", f"{rclone_dest}/{fname}"],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            try:
                entries = json.loads(r.stdout)
                if not entries:
                    return False, False
                return True, entries[0].get("Size", -1) == src_size
            except Exception:
                return True, False
        if r.returncode == 3:
            return False, False
        if attempt < 2:
            time.sleep(5)

    return False, False


def delete_from_remote(src, hot_dir):
    """Delete file from remote. Returns True on success."""
    rclone_dest = hot_to_rclone_dest(src, hot_dir)
    fname       = os.path.basename(src)
    r = subprocess.run(
        [RCLONE_BIN, "deletefile", f"{rclone_dest}/{fname}"],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        logging.info(f"Deleted from remote: {fname}")
        return True
    logging.warning(f"Delete from remote failed for {fname}: {r.stderr.strip()}")
    return False


def folder_to_rclone_remote(folder, hot_dir):
    """Convert a local folder path to its full rclone remote path."""
    relative = os.path.relpath(folder, os.path.dirname(hot_dir))
    return f"{RCLONE_REMOTE}/{relative}"


def purge_remote_path(remote_path):
    """Purge an arbitrary remote path. Returns True on success."""
    r = subprocess.run(
        [RCLONE_BIN, "purge", remote_path],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        logging.info(f"Purged remote: {remote_path}")
        return True
    logging.warning(f"Purge failed for {remote_path}: {r.stderr.strip()}")
    return False


def purge_dir_from_remote(src, hot_dir):
    """Purge the remote directory that contained src. Returns True on success."""
    rclone_dest = hot_to_rclone_dest(src, hot_dir)
    r = subprocess.run(
        [RCLONE_BIN, "purge", rclone_dest],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        logging.info(f"Purged remote dir: {rclone_dest}")
        return True
    logging.warning(f"Purge remote dir failed for {rclone_dest}: {r.stderr.strip()}")
    return False


def rmdir_from_remote(remote_dir):
    """Remove a remote directory only if it is empty. Silent no-op if not empty."""
    r = subprocess.run(
        [RCLONE_BIN, "rmdir", remote_dir],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        logging.info(f"Removed empty remote dir: {remote_dir}")
    # non-zero just means dir wasn't empty — not an error

# ─── Core move ─────────────────────────────────────────────────────────────────

def move_to_cold(src, hot_dir, item_id=None, stats=None):
    """
    Upload a single file to cold storage via rclone.
    Streams directly to Google Drive — zero local disk usage.

    Steps:
    1. Validate source
    2. Check if already on remote (skip or clean partial)
    3. Upload with retries + exponential backoff
    4. Post-transfer size verification
    5. Remove local source
    6. Create symlink → cold WebDAV mount
    7. Mark item as cold in Jellyfin metadata (if item_id provided)

    Returns (success: bool, reason: str)
    """
    from jellyfin import mark_as_cold

    dest        = hot_to_cold_path(src, hot_dir)
    rclone_dest = hot_to_rclone_dest(src, hot_dir)
    fname       = os.path.basename(src)

    # ── 1. Validate ───────────────────────────────────────────────────────────
    if not os.path.exists(src):
        return False, "source does not exist"
    if not os.access(src, os.R_OK):
        return False, "source not readable"

    src_size = os.path.getsize(src)
    if src_size == 0:
        return False, "source is empty"

    src_gb = src_size / 1e9

    # ── 2. Already on remote? ─────────────────────────────────────────────────
    exists, size_ok = file_exists_on_remote(src, hot_dir)
    if exists:
        if size_ok:
            if not os.path.islink(src):
                try:
                    os.remove(src)
                    os.symlink(dest, src)
                    logging.info(f"Fixed symlink: {fname}")
                except Exception as e:
                    logging.warning(f"Could not fix symlink for {fname}: {e}")
            else:
                logging.info(f"Skip (already on remote): {fname}")
            if stats:
                stats.record_skipped()
            return True, "already archived"
        else:
            logging.warning(f"Partial upload detected, cleaning: {fname}")
            delete_from_remote(src, hot_dir)

    # ── 3. Upload with retries ────────────────────────────────────────────────
    upload_ok  = False
    last_error = ""
    file_start = time.time()

    for attempt in range(1, MAX_RETRIES + 1):
        if is_interrupted():
            return False, "interrupted by user"

        logging.info(f"Upload attempt {attempt}/{MAX_RETRIES}: {fname} ({src_gb:.2f}GB)")
        print(f"\n  → [{attempt}/{MAX_RETRIES}] {fname} ({src_gb:.1f}GB)")

        r = subprocess.run(
            [
                RCLONE_BIN, "copy", src, rclone_dest,
                "--checksum",
                "--no-traverse",
                "--transfers",           "1",
                "--retries",             "3",
                "--low-level-retries",   "10",
                "--retries-sleep",       "10s",
                "--stats",               RCLONE_STATS_INTERVAL,
                "--stats-one-line",
                *( ["--progress"] if sys.stdout.isatty() else [] ),
                "--drive-chunk-size",    RCLONE_CHUNK_SIZE,
                "--drive-upload-cutoff", RCLONE_UPLOAD_CUTOFF,
            ],
            stderr=subprocess.PIPE,
            text=True
        )

        if r.returncode == 0:
            upload_ok = True
            break

        last_error = r.stderr.strip() or f"exit code {r.returncode}"
        logging.warning(f"Attempt {attempt} failed for {fname}: {last_error}")

        if attempt < MAX_RETRIES:
            delete_from_remote(src, hot_dir)
            wait = RETRY_WAIT_SEC * attempt
            print(f"  ⚠  Failed — retrying in {wait}s...")
            logging.info(f"Waiting {wait}s before retry")
            for _ in range(wait):
                if is_interrupted():
                    return False, "interrupted during retry wait"
                time.sleep(1)

    if not upload_ok:
        reason = f"all {MAX_RETRIES} attempts failed: {last_error}"
        logging.error(f"FAIL — {fname}: {reason}")
        if stats:
            stats.record_failed(src, reason, src_gb)
        return False, reason

    total_elapsed = time.time() - file_start

    # ── 4. Verify ─────────────────────────────────────────────────────────────
    exists, size_ok = file_exists_on_remote(src, hot_dir)
    if not exists:
        reason = "file not found on remote after upload"
        logging.error(f"FAIL — {fname}: {reason}")
        if stats:
            stats.record_failed(src, reason, src_gb)
        return False, reason

    if not size_ok:
        reason = "size mismatch after upload"
        logging.error(f"FAIL — {fname}: {reason}")
        delete_from_remote(src, hot_dir)
        if stats:
            stats.record_failed(src, reason, src_gb)
        return False, reason

    # ── 5. Remove source ──────────────────────────────────────────────────────
    try:
        os.remove(src)
    except Exception as e:
        reason = f"could not remove source: {e}"
        logging.error(f"FAIL — {fname}: {reason}")
        if stats:
            stats.record_failed(src, reason, src_gb)
        return False, reason

    # ── 6. Symlink ────────────────────────────────────────────────────────────
    try:
        os.symlink(dest, src)
    except Exception as e:
        reason = f"symlink failed: {e}"
        logging.error(f"FAIL — {fname}: {reason}")
        if stats:
            stats.record_failed(src, reason, src_gb)
        return False, reason

    # ── 7. Mark as cold in Jellyfin ───────────────────────────────────────────
    if item_id:
        try:
            mark_as_cold(item_id)
        except Exception as e:
            # Non-fatal — file is archived, just metadata update failed
            logging.warning(f"Could not mark as cold in Jellyfin for {fname}: {e}")

    # ── Record stats ──────────────────────────────────────────────────────────
    if stats:
        stats.record_archived(fname, src_gb, total_elapsed)
        stats.print_file_done(fname, src_gb, total_elapsed)

    logging.info(f"OK — {src_gb:.2f}GB in {total_elapsed:.0f}s: {fname}")
    return True, "ok"

# ─── Show move ─────────────────────────────────────────────────────────────────

def _count_video_files(folder_path):
    """Count hot (real) and cold (symlink) video files in a folder (non-recursive)."""
    hot = cold = 0
    for fname in os.listdir(folder_path):
        if os.path.splitext(fname)[1].lower() not in EXTS:
            continue
        fpath = os.path.join(folder_path, fname)
        if os.path.islink(fpath):
            cold += 1
        elif os.path.isfile(fpath):
            hot += 1
    return hot, cold


def move_show_to_cold(show_path, hot_dir, items_map=None, stats=None):
    """
    Move all video files in a show to cold storage.
    After each season, tags Season in Jellyfin as fully-cold or partial-cold
    based on actual disk state. Tags Series the same way after all seasons.

    Returns (gb_moved: float, failed: list)
    """
    from jellyfin import mark_as_cold, mark_as_partial_cold, get_episode_parents

    gb_moved       = 0.0
    failed         = []
    show_series_id = None

    for season in sorted(os.listdir(show_path)):
        season_path = os.path.join(show_path, season)
        if not os.path.isdir(season_path):
            continue
        if is_interrupted():
            break

        logging.info(f"Season: {season}")

        for fname in sorted(os.listdir(season_path)):
            if os.path.splitext(fname)[1].lower() not in EXTS:
                continue
            src = os.path.join(season_path, fname)
            if os.path.islink(src):
                continue
            if is_interrupted():
                return gb_moved, failed

            item_id = None
            if items_map and src in items_map:
                item_id = items_map[src].get("id")

            size_gb         = os.path.getsize(src) / 1e9
            success, reason = move_to_cold(src, hot_dir, item_id=item_id, stats=stats)

            if success and reason != "already archived":
                gb_moved += size_gb
            elif not success:
                failed.append((src, reason))

        # ── Tag season based on actual disk state after processing ─────────────
        any_episode_id = None
        for fname in os.listdir(season_path):
            if os.path.splitext(fname)[1].lower() not in EXTS:
                continue
            info = (items_map or {}).get(os.path.join(season_path, fname))
            if info and info.get("id"):
                any_episode_id = info["id"]
                break

        if any_episode_id:
            try:
                series_id, season_id, _ = get_episode_parents(any_episode_id)
                if season_id:
                    hot_count, cold_count = _count_video_files(season_path)
                    if cold_count > 0 and hot_count == 0:
                        mark_as_cold(season_id)
                        logging.info(f"Season fully cold: {season}")
                    elif cold_count > 0:
                        mark_as_partial_cold(season_id)
                        logging.info(f"Season partial cold ({cold_count}c/{hot_count}h): {season}")
                if series_id:
                    show_series_id = series_id
            except Exception as e:
                logging.warning(f"Could not tag season for {season}: {e}")

    # ── Tag series based on whole show disk state ──────────────────────────────
    if show_series_id:
        try:
            total_hot = total_cold = 0
            for s in os.listdir(show_path):
                sp = os.path.join(show_path, s)
                if os.path.isdir(sp):
                    h, c = _count_video_files(sp)
                    total_hot  += h
                    total_cold += c
            if total_cold > 0 and total_hot == 0:
                mark_as_cold(show_series_id)
                logging.info("Series fully cold")
            elif total_cold > 0:
                mark_as_partial_cold(show_series_id)
                logging.info(f"Series partial cold ({total_cold}c/{total_hot}h)")
        except Exception as e:
            logging.warning(f"Could not tag series: {e}")

    return gb_moved, failed