# Configuration reference

All settings are passed as environment variables (Docker) or constants in `dev/config.py` (local).

---

## Jellyfin

| Variable | Required | Description |
|----------|----------|-------------|
| `JELLYFIN_URL` | ✓ | Full URL to your Jellyfin instance, e.g. `http://192.168.1.10:8096` |
| `JELLYFIN_API_KEY` | ✓ | API key from Jellyfin Dashboard → API Keys |
| `JELLYFIN_USER_IDS` | ✓ | Comma-separated user IDs; play history is merged across all of them |

---

## Storage paths

| Variable | Required | Description |
|----------|----------|-------------|
| `HOT_DIRS` | ✓ | Comma-separated absolute paths to hot media directories (e.g. `/mnt/media/movies,/mnt/media/shows`) |
| `COLD_PATH` | ✓ | Local mount point where cold storage is accessible (WebDAV, NFS, FUSE, etc.) |

---

## Volume mounts (Docker only)

| Variable | Description |
|----------|-------------|
| `HOST_MEDIA_VOLUME` | Host path to the root of your media drive |
| `COLD_MOUNT` | Host path to the cold storage mount (same location as `COLD_PATH` inside the container) |
| `RCLONE_CONFIG` | Host path to the directory containing `rclone.conf` |
| `LOGS_VOLUME` | Host path where logs should be written |

---

## Rclone

| Variable | Default | Description |
|----------|---------|-------------|
| `RCLONE_BIN` | `/usr/local/bin/rclone` | Path to the rclone binary |
| `RCLONE_REMOTE` | — | Remote and path as configured in `rclone.conf`, e.g. `myremote:path/to/Cold` |
| `RCLONE_CHUNK_SIZE` | `128M` | Upload chunk size (tune for your remote) |
| `RCLONE_UPLOAD_CUTOFF` | `128M` | File size above which multipart uploads are used |
| `RCLONE_STATS_INTERVAL` | `5s` | How often rclone logs transfer progress |

---

## Archive thresholds

These control when a file qualifies for archival.

| Variable | Default | Description |
|----------|---------|-------------|
| `MOVIE_PLAYED_COLD_DAYS` | `7` | Days since last play → archive |
| `MOVIE_NEVER_WATCHED_COLD_DAYS` | `30` | Days since added, never watched → archive |
| `MOVIE_ABANDONED_DAYS` | `14` | Days in-progress with no activity → archive |
| `SHOW_INACTIVITY_COLD_DAYS` | `14` | Days since any episode played → archive the whole show |
| `SHOW_ALL_WATCHED_COLD_DAYS` | `7` | Days after finishing all episodes → archive the show |

---

## Archive run

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_GB` | `700` | Max GB to upload per run — set based on your remote's daily transfer limits |
| `MAX_RETRIES` | `3` | How many times to retry a failed rclone upload |
| `RETRY_WAIT_SEC` | `30` | Seconds to wait between retries |
| `RUN_MODE` | `archive` | `archive` to execute, `dry_run` to log only |

---

## Recall

| Variable | Default | Description |
|----------|---------|-------------|
| `RECALL_BLOCKING` | `false` | `false` = background recall (symlink stays, player streams via mount); `true` = blocking recall (symlink removed, playback waits for download) |

Use `false` for slow remotes (cloud storage). Use `true` for fast remotes (local NAS, gigabit LAN).

---

## Files

| Variable | Default | Description |
|----------|---------|-------------|
| `EXTS` | `.mkv,.mp4,.avi,.mov` | Comma-separated file extensions to manage |
| `KEEP_HOT` | *(empty)* | Comma-separated filenames to never archive |

---

## Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_BASE_DIR` | `/logs` | Directory where log files are written |
