import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ─── Jellyfin ──────────────────────────────────────────────────────────────────

JELLYFIN = os.environ["JELLYFIN_URL"]
API_KEY  = os.environ["JELLYFIN_API_KEY"]
HEADERS  = {
    "Authorization": f'MediaBrowser Client="Jellyfin Web", Device="script", '
                     f'DeviceId="archive01", Version="10.10.0", Token="{API_KEY}"'
}
USER_IDS = [u.strip() for u in os.environ["JELLYFIN_USER_IDS"].split(",")]

# ─── Storage paths ─────────────────────────────────────────────────────────────

HOT_DIRS = [d.strip() for d in os.environ["HOT_DIRS"].split(",")]
COLD     = os.environ["COLD_PATH"]

# ─── Rclone ────────────────────────────────────────────────────────────────────

RCLONE_BIN            = os.getenv("RCLONE_BIN", "/usr/local/bin/rclone")
RCLONE_REMOTE         = os.environ["RCLONE_REMOTE"]
RCLONE_CHUNK_SIZE     = os.getenv("RCLONE_CHUNK_SIZE", "128M")
RCLONE_UPLOAD_CUTOFF  = os.getenv("RCLONE_UPLOAD_CUTOFF", "128M")
RCLONE_STATS_INTERVAL = os.getenv("RCLONE_STATS_INTERVAL", "5s")

# ─── Thresholds ────────────────────────────────────────────────────────────────

MOVIE_PLAYED_COLD_DAYS        = int(os.getenv("MOVIE_PLAYED_COLD_DAYS", "7"))
MOVIE_NEVER_WATCHED_COLD_DAYS = int(os.getenv("MOVIE_NEVER_WATCHED_COLD_DAYS", "30"))
MOVIE_ABANDONED_DAYS          = int(os.getenv("MOVIE_ABANDONED_DAYS", "14"))
SHOW_INACTIVITY_COLD_DAYS     = int(os.getenv("SHOW_INACTIVITY_COLD_DAYS", "14"))
SHOW_ALL_WATCHED_COLD_DAYS    = int(os.getenv("SHOW_ALL_WATCHED_COLD_DAYS", "7"))

# ─── Archive ───────────────────────────────────────────────────────────────────

MAX_GB         = float(os.getenv("MAX_GB", "700"))
MAX_RETRIES    = int(os.getenv("MAX_RETRIES", "3"))
RETRY_WAIT_SEC = int(os.getenv("RETRY_WAIT_SEC", "30"))
RUN_MODE       = os.getenv("RUN_MODE", "archive")

# ─── Files ─────────────────────────────────────────────────────────────────────

EXTS     = set(os.getenv("EXTS", ".mkv,.mp4,.avi,.mov").split(","))
KEEP_HOT = set(filter(None, os.getenv("KEEP_HOT", "").split(",")))

# ─── Recall ────────────────────────────────────────────────────────────────────

RECALL_BLOCKING = os.getenv("RECALL_BLOCKING", "false").lower() == "true"

# ─── Logging ───────────────────────────────────────────────────────────────────

LOG_BASE_DIR = os.getenv("LOG_BASE_DIR", "/logs")
