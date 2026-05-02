import os
import logging
import signal
import sys
import threading
from flask import Flask, request
from logger import setup
from config import HOT_DIRS, EXTS, USER_IDS
from jellyfin import get_all_items, get_item_path
from recall import is_cold, recall_file
from storage import delete_from_remote, purge_dir_from_remote

app = Flask(__name__)
setup(mode="listener")

# ─── Active recall threads ─────────────────────────────────────────────────────

_active_recalls      = []
_active_recalls_lock = threading.Lock()


def track_recall(path, item_id=None, play_method="DirectPlay", season_context=None):
    """
    Launch a recall thread and track it for clean shutdown.
    season_context: optional dict with season info for better logging.
    """
    def run():
        success, reason = recall_file(path, item_id=item_id, play_method=play_method)
        fname = os.path.basename(path)
        if success:
            logging.info(f"RECALL DONE: {fname} — {reason}")
        else:
            logging.error(f"RECALL FAILED: {fname} — {reason}")
        with _active_recalls_lock:
            if t in _active_recalls:
                _active_recalls.remove(t)
        # Log season progress
        with _active_recalls_lock:
            remaining = len(_active_recalls)
        logging.info(f"Active recalls remaining: {remaining}")

    t = threading.Thread(target=run, daemon=False)
    with _active_recalls_lock:
        _active_recalls.append(t)
    t.start()


def _handle_shutdown(sig, frame):
    """Graceful shutdown — wait for active recalls to finish."""
    print("\n⚠  Shutting down listener...")
    logging.info("Shutdown signal received")

    with _active_recalls_lock:
        active = list(_active_recalls)

    if active:
        print(f"  Waiting for {len(active)} active recall(s) to finish...")
        for t in active:
            t.join()
        print("  All recalls finished.")

    logging.info("Listener shutdown complete")
    sys.exit(0)

def is_first_episode_of_season(path):
    """
    Return True if this is the first episode file in its season folder.
    Determined by alphabetical sort — first file = episode 1.
    """
    season_path = os.path.dirname(path)
    episodes    = sorted([
        f for f in os.listdir(season_path)
        if os.path.splitext(f)[1].lower() in EXTS
    ])
    return len(episodes) > 0 and episodes[0] == os.path.basename(path)

signal.signal(signal.SIGINT,  _handle_shutdown)
signal.signal(signal.SIGTERM, _handle_shutdown)

# ─── Deletion cleanup ──────────────────────────────────────────────────────────

def _find_episode_in_cache(item_id):
    """Reverse-lookup an episode path from the items cache by ItemId."""
    return next((p for p, info in (_items_map or {}).items() if info.get("id") == item_id), None)



def handle_item_deleted(data):
    """
    Jellyfin sends one webhook per deleted item.

    Cold storage cleanup is intentionally NOT done here — webhook timing is
    unreliable relative to the archive process and would cause false deletions.
    Use cleanup_remote_orphans.py to safely purge cold files with no Jellyfin item.

    This handler only removes dangling symlinks left on hot for Episode/Movie.
    """
    item_id   = data.get("ItemId") or data.get("Id") or ""
    item_name = data.get("Name", "?")
    item_type = data.get("ItemType", "")
    logging.info(f"DELETION: {item_type} {item_name!r} (id={item_id}) — skipping cold, cleanup_remote_orphans handles that")

    if item_type not in ("Episode", "Movie"):
        return

    path = get_item_path(item_id) or _find_episode_in_cache(item_id)
    if not path:
        return

    if os.path.islink(path) and not os.path.exists(path):
        try:
            os.remove(path)
            logging.info(f"DELETION: removed dangling symlink — {os.path.basename(path)}")
        except Exception as e:
            logging.warning(f"DELETION: could not remove dangling symlink {os.path.basename(path)}: {e}")


# ─── Items cache ───────────────────────────────────────────────────────────────

_items_map      = None
_items_map_time = 0
CACHE_TTL       = 3600


def get_items_map():
    """Return cached items_map, refresh if older than CACHE_TTL."""
    global _items_map, _items_map_time
    import time
    now = time.time()
    if _items_map is None or now - _items_map_time > CACHE_TTL:
        logging.info("Refreshing items map cache...")
        _items_map      = get_all_items()
        _items_map_time = now
        logging.info(f"Items map refreshed: {len(_items_map)} items")
    return _items_map

# ─── Webhook ───────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    data              = request.get_json(force=True, silent=True) or {}
    notification_type = data.get("NotificationType", "")
    item_type         = data.get("ItemType", "")
    logging.info(f"Webhook received: NotificationType={notification_type!r} ItemType={item_type!r}")

    if notification_type == "ItemDeleted":
        handle_item_deleted(data)
        return "", 200

    user_id        = data.get("UserId") or data.get("userId") or ""
    item_id        = data.get("ItemId") or data.get("Id") or ""
    item_type      = data.get("ItemType", "")
    play_method    = data.get("PlayMethod", "DirectPlay")
    position_ticks = data.get("PlaybackPositionTicks", 0)

    if USER_IDS and user_id not in USER_IDS:
        logging.info(f"Skipping recall — user {user_id!r} is not a recall user (admin/test)")
        return "", 200

    if not item_id or item_type not in ("Movie", "Episode"):
        return "", 200

    items_map = get_items_map()
    path      = next((p for p, info in items_map.items() if info.get("id") == item_id), None)

    if not path:
        logging.info(f"ItemId {item_id} not found in items map")
        return "", 200

    if os.path.splitext(path)[1].lower() not in EXTS:
        return "", 200

    if not any(path.startswith(h) for h in HOT_DIRS):
        return "", 200

    if not is_cold(path):
        logging.info(f"Hot file played, no recall needed: {os.path.basename(path)}")
        return "", 200

    # ── Episode: decide recall scope ──────────────────────────────────────────
    if item_type == "Episode" and position_ticks == 0 and is_first_episode_of_season(path):
        # Starting season from the very first episode — recall whole season
        season_path   = os.path.dirname(path)
        cold_episodes = [
            os.path.join(season_path, f)
            for f in sorted(os.listdir(season_path))
            if os.path.splitext(f)[1].lower() in EXTS
            and os.path.islink(os.path.join(season_path, f))
        ]

        total_cold = len(cold_episodes)
        logging.info(f"Season recall started: {os.path.basename(season_path)}")
        logging.info(f"  Episodes to recall: {total_cold}")
        logging.info(f"  Play method: {play_method}")

        for ep_path in cold_episodes:
            ep_info = items_map.get(ep_path)
            ep_id   = ep_info.get("id") if ep_info else None
            logging.info(f"  Queuing: {os.path.basename(ep_path)}")
            track_recall(ep_path, item_id=ep_id, play_method=play_method)

    else:
        # Movie, resumed episode, or mid-season start — recall single file
        logging.info(f"Recalling single file: {os.path.basename(path)}")
        track_recall(path, item_id=item_id, play_method=play_method)

    return "", 200


@app.route("/health", methods=["GET"])
def health():
    with _active_recalls_lock:
        active = len(_active_recalls)
    return {"status": "ok", "active_recalls": active}, 200


def _warm_cache():
    logging.info("Pre-warming items cache at startup...")
    get_items_map()
    logging.info("Items cache ready.")


if __name__ == "__main__":
    threading.Thread(target=_warm_cache, daemon=True).start()
    app.run(host="0.0.0.0", port=5001, debug=False)