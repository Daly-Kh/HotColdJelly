import os

# ─── Jellyfin ──────────────────────────────────────────────────────────────────

JELLYFIN = os.getenv("JELLYFIN_URL", "http://localhost:8096")
API_KEY  = os.getenv("JELLYFIN_API_KEY", "")
HEADERS  = {
    "Authorization": f'MediaBrowser Client="Jellyfin Web", Device="script", '
                     f'DeviceId="archive01", Version="10.10.0", Token="{API_KEY}"'
}
USER_IDS = [
    u.strip() for u in
    os.getenv("JELLYFIN_USER_IDS", "").split(",")
    if u.strip()
]

# ─── Storage paths ─────────────────────────────────────────────────────────────

HOT_DIRS = [
    d.strip() for d in
    os.getenv("HOT_DIRS", "").split(",")
    if d.strip()
]

# WebDAV mount point — symlinks point here so Jellyfin can stream cold content
COLD = os.getenv("COLD_PATH", "/cold")

# ─── Rclone ────────────────────────────────────────────────────────────────────

RCLONE_BIN            = os.getenv("RCLONE_BIN", "/usr/local/bin/rclone")
RCLONE_REMOTE         = os.getenv("RCLONE_REMOTE", "")
RCLONE_CHUNK_SIZE     = os.getenv("RCLONE_CHUNK_SIZE", "128M")
RCLONE_UPLOAD_CUTOFF  = os.getenv("RCLONE_UPLOAD_CUTOFF", "128M")
RCLONE_STATS_INTERVAL = os.getenv("RCLONE_STATS_INTERVAL", "5s")

# ─── Thresholds ────────────────────────────────────────────────────────────────

MOVIE_PLAYED_COLD_DAYS        = int(os.getenv("MOVIE_PLAYED_COLD_DAYS",        "14"))
MOVIE_NEVER_WATCHED_COLD_DAYS = int(os.getenv("MOVIE_NEVER_WATCHED_COLD_DAYS", "30"))
MOVIE_ABANDONED_DAYS          = int(os.getenv("MOVIE_ABANDONED_DAYS",          "60"))
SHOW_INACTIVITY_COLD_DAYS     = int(os.getenv("SHOW_INACTIVITY_COLD_DAYS",     "30"))
SHOW_ALL_WATCHED_COLD_DAYS    = int(os.getenv("SHOW_ALL_WATCHED_COLD_DAYS",    "14"))

# ─── Archive ───────────────────────────────────────────────────────────────────

MAX_GB         = float(os.getenv("MAX_GB",         "50"))
MAX_RETRIES    = int(os.getenv("MAX_RETRIES",       "3"))
RETRY_WAIT_SEC = int(os.getenv("RETRY_WAIT_SEC",    "30"))

# ─── Recall ────────────────────────────────────────────────────────────────────

# False = background recall (GDrive)
# True  = blocking recall (NAS — fast enough to wait)
RECALL_BLOCKING = os.getenv("RECALL_BLOCKING", "false").lower() == "true"

# ─── Files ─────────────────────────────────────────────────────────────────────

EXTS = set(os.getenv("EXTS", ".mkv,.mp4,.avi,.mov").split(","))

KEEP_HOT = set(os.getenv("KEEP_HOT", "").split(",")) - {""}

# ─── Logging ───────────────────────────────────────────────────────────────────

LOG_BASE_DIR = os.getenv("LOG_BASE_DIR", "/logs")