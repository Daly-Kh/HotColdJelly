import os
import sys
import subprocess
import threading
import logging
import time
import requests

from config import (
    EXTS, HOT_DIRS,
    RCLONE_BIN, RCLONE_REMOTE,
    RCLONE_CHUNK_SIZE, RCLONE_UPLOAD_CUTOFF,
    RECALL_BLOCKING, JELLYFIN, HEADERS,
)
from jellyfin import mark_as_hot, mark_recently_played
from storage import hot_to_cold_path


def is_cold(path):
    """Return True if path is a symlink (archived to cold storage)."""
    return os.path.islink(path)


def find_hot_dir(path):
    """Return which HOT_DIR this path belongs to, or None."""
    for hot_dir in HOT_DIRS:
        if path.startswith(hot_dir):
            return hot_dir
    return None


def rclone_remote_path(src, hot_dir):
    """
    Convert a hot file path to its full rclone remote path.
    /hot/movies/Dune/Dune.mkv → GDrive:dNAS/Media/Cold/movies/Dune/Dune.mkv
    """
    relative = os.path.relpath(src, os.path.dirname(hot_dir))
    return f"{RCLONE_REMOTE}/{relative}"


def _is_currently_playing(item_id):
    """Check if item is currently being streamed in any Jellyfin session."""
    if not item_id:
        return False
    try:
        r = requests.get(f"{JELLYFIN}/Sessions", headers=HEADERS, timeout=5)
        if r.status_code != 200:
            return False
        for session in r.json():
            if session.get("NowPlayingItem", {}).get("Id") == item_id:
                return True
    except Exception as e:
        logging.warning(f"Could not check session status: {e}")
    return False


def _wait_for_playback_end(item_id, fname, check_interval=30, max_wait=1800):
    """
    Wait until item is no longer playing before swapping symlink.
    Returns True if playback ended, False if timed out.
    """
    if not item_id:
        return True
    waited = 0
    while _is_currently_playing(item_id):
        if waited >= max_wait:
            logging.warning(f"Timed out waiting for playback to end: {fname}")
            return False
        logging.info(f"Waiting for playback to finish ({waited}s): {fname}")
        time.sleep(check_interval)
        waited += check_interval
    return True


def _delete_from_remote(remote_path, fname):
    """
    Delete a file from rclone remote after successful recall.
    Non-fatal — logs warning on failure but does not affect recall result.
    """
    r = subprocess.run(
        [RCLONE_BIN, "deletefile", remote_path],
        stderr=subprocess.PIPE,
        text=True
    )
    if r.returncode == 0:
        logging.info(f"Deleted from remote: {fname}")
    else:
        logging.warning(f"Could not delete from remote (non-fatal): {fname} — {r.stderr.strip()}")


def _download(remote_path, dest_path):
    """Download a file from rclone remote to exact dest_path."""
    r = subprocess.run(
        [
            RCLONE_BIN, "copyto",
            remote_path,
            dest_path,
            "--checksum",
            "--no-traverse",
            "--transfers",           "1",
            "--retries",             "3",
            "--low-level-retries",   "10",
            "--retries-sleep",       "10s",
            *( ["--progress"] if sys.stdout.isatty() else [] ),
            "--use-server-modtime",
            "--drive-chunk-size",    RCLONE_CHUNK_SIZE,
            "--drive-upload-cutoff", RCLONE_UPLOAD_CUTOFF,
        ],
        stderr=subprocess.PIPE,
        text=True
    )
    if r.returncode != 0:
        return False, r.stderr.strip()
    return True, ""


def recall_file(src, item_id=None, play_method="DirectPlay"):
    """
    Recall a single cold file back to hot storage.
    RECALL_BLOCKING=False: background recall, stream from GDrive meanwhile
    RECALL_BLOCKING=True:  blocking recall, wait for download (fast on NAS)
    """
    if not is_cold(src):
        return False, "not a cold file"
    hot_dir = find_hot_dir(src)
    if not hot_dir:
        return False, "path not in any hot dir"

    if RECALL_BLOCKING:
        return _recall_blocking(src, hot_dir, item_id)
    else:
        threading.Thread(
            target=_recall_background,
            args=(src, hot_dir, item_id, play_method),
            daemon=False
        ).start()
        return True, "recall started in background"


def _recall_blocking(src, hot_dir, item_id=None):
    """
    Blocking recall — removes symlink first, downloads synchronously.
    Restores symlink on any failure so Jellyfin keeps working.
    """
    fname          = os.path.basename(src)
    remote_path    = rclone_remote_path(src, hot_dir)
    symlink_target = os.readlink(src)

    logging.info(f"RECALL blocking: {fname}")

    try:
        os.remove(src)
    except Exception as e:
        return False, f"could not remove symlink: {e}"

    try:
        ok, err = _download(remote_path, src)
    except Exception as e:
        _restore_symlink(src, symlink_target, fname)
        return False, f"download exception: {e}"

    if not ok:
        _restore_symlink(src, symlink_target, fname)
        logging.error(f"RECALL FAIL blocking — {fname}: {err}")
        return False, f"download failed: {err}"

    if not os.path.isfile(src) or os.path.islink(src) or os.path.getsize(src) == 0:
        _restore_symlink(src, symlink_target, fname)
        return False, "bad file after download"

    if item_id:
        try:
            mark_as_hot(item_id)
            mark_recently_played(item_id)
        except Exception as e:
            logging.warning(f"Could not mark as hot for {fname}: {e}")

    _delete_from_remote(remote_path, fname)

    logging.info(f"RECALL OK blocking: {fname}")
    return True, "ok"


def _recall_background(src, hot_dir, item_id=None, play_method="DirectPlay"):
    """
    Background recall — downloads to temp file while symlink stays intact.
    Waits for playback to finish (Transcode) or swaps immediately (DirectPlay).
    Atomic swap at the end.
    """
    fname       = os.path.basename(src)
    tmp_path    = src + ".recalling"
    remote_path = rclone_remote_path(src, hot_dir)

    logging.info(f"RECALL background starting ({play_method}): {fname}")

    try:
        ok, err = _download(remote_path, tmp_path)
    except Exception as e:
        logging.error(f"RECALL FAIL background — download exception for {fname}: {e}")
        _cleanup_tmp(tmp_path)
        return

    if not ok:
        logging.error(f"RECALL FAIL background — {fname}: {err}")
        _cleanup_tmp(tmp_path)
        return

    if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) == 0:
        logging.error(f"RECALL FAIL background — bad temp file: {fname}")
        _cleanup_tmp(tmp_path)
        return

    logging.info(f"Download complete: {fname}")

    # Wait for playback to end only if transcoding
    if play_method == "Transcode":
        logging.info(f"Transcode — waiting for playback to end: {fname}")
        _wait_for_playback_end(item_id, fname)
    else:
        logging.info(f"DirectPlay — swapping immediately: {fname}")

    # Atomic swap
    try:
        os.remove(src)
        os.rename(tmp_path, src)
    except Exception as e:
        logging.error(f"RECALL FAIL background — swap failed for {fname}: {e}")
        if os.path.exists(tmp_path) and not os.path.exists(src):
            _restore_symlink(src, hot_to_cold_path(src, hot_dir), fname)
        _cleanup_tmp(tmp_path)
        return

    if item_id:
        try:
            from jellyfin import mark_as_hot, mark_recently_played, update_season_series_hot_status
            success = mark_as_hot(item_id)
            if success:
                logging.info(f"Marked episode as hot: {fname}")
            else:
                logging.warning(f"mark_as_hot returned False: {fname}")
            mark_recently_played(item_id)
            update_season_series_hot_status(item_id)
        except Exception as e:
            logging.warning(f"Could not mark as hot for {fname}: {e}")

    _delete_from_remote(remote_path, fname)


def _restore_symlink(src, target, fname):
    """Restore symlink after failed recall so Jellyfin keeps working."""
    try:
        if not os.path.exists(src):
            os.symlink(target, src)
            logging.info(f"Restored symlink: {fname}")
    except Exception as e:
        logging.error(f"Could not restore symlink for {fname}: {e}")


def _cleanup_tmp(tmp_path):
    """Remove leftover temp file."""
    try:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    except Exception:
        pass