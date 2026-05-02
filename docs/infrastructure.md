# Infrastructure

## Components

```
┌─────────────────────────────────────────────────┐
│                   Docker host                   │
│                                                 │
│  ┌───────────────────┐  ┌──────────────────┐   │
│  │  listener         │  │  archive         │   │
│  │  (Flask, :5001)   │  │  (cron/manual)   │   │
│  └────────┬──────────┘  └────────┬─────────┘   │
│           │                      │              │
│           └──────────┬───────────┘              │
│                      │                          │
│            shared/ (config, jellyfin,           │
│                    storage, logger)             │
└──────────────────────┬──────────────────────────┘
                       │
           ┌───────────┼───────────┐
           │           │           │
     Jellyfin      rclone      Cold mount
     (API +        (upload/     (WebDAV / NFS /
      webhooks)     download)    FUSE — local)
                       │
                  Cold remote
                  (S3, Backblaze,
                   GDrive, SFTP…)
```

## Two containers

| Container | Role |
|-----------|------|
| `hot-cold-jelly_listener` | Webhook receiver. Handles `PlaybackStart` (recall) and `ItemDeleted` (cold cleanup). Always running. |
| `hot-cold-jelly_archive` | Archival runner. Scans hot directories, applies thresholds, uploads qualifying files. Run on a schedule or manually. |

Both containers share the same `shared/` Python modules (config, Jellyfin client, storage utilities, logger).

## Cold storage access: two paths

Cold storage is accessed two different ways depending on the operation:

| Operation | How | Why |
|-----------|-----|-----|
| Upload / download / delete | rclone via the configured remote | Efficient, retry-able, supports chunked transfer |
| Streaming during background recall | Local mount (`COLD_PATH`) | Jellyfin reads symlink targets directly — no rclone needed for playback |

The local mount must stay up for Jellyfin to stream cold content. rclone handles all data movement.

## Jellyfin integration

- **API** — used for play history, item metadata, tagging (read/write)
- **Webhooks** — Jellyfin pushes `PlaybackStart` and `ItemDeleted` events to the listener

The listener also maintains an in-memory items cache (`_items_map`) populated at startup and refreshed hourly. This is necessary for deletion handling — by the time the `ItemDeleted` webhook fires, the item is already gone from the Jellyfin database, so the listener falls back to its own cache to resolve file paths.

## Ports

| Port | Service |
|------|---------|
| 5001 | Listener webhook endpoint (`POST /webhook`) |

## File layout

```
HotColdJelly/
├── docker/
│   ├── docker-compose.yml
│   ├── .env                     # your config (not committed)
│   ├── .env.example
│   ├── shared/                  # modules shared by both containers
│   │   ├── config.py
│   │   ├── jellyfin.py
│   │   ├── logger.py
│   │   ├── stats.py
│   │   └── storage.py
│   ├── listener/
│   │   ├── Dockerfile
│   │   ├── listener.py
│   │   └── recall.py
│   └── archive/
│       ├── Dockerfile
│       ├── display.py
│       ├── main.py
│       ├── movies.py
│       └── shows.py
└── dev/                         # local dev (flat mirror of docker/ modules)
    ├── .env                     # your config (not committed)
    ├── .env.example
    ├── config.py
    ├── display.py
    ├── jellyfin.py
    ├── listener.py
    ├── logger.py
    ├── main.py
    ├── movies.py
    ├── recall.py
    ├── requirements.txt
    ├── shows.py
    ├── stats.py
    ├── storage.py
    ├── tests/
    ├── helpers/
    │   ├── audit.py
    │   ├── cleanup_remote_orphans.py
    │   ├── fix_broken_symlinks.py
    │   ├── fix_hot_tags.py
    │   ├── fix_missing_symlinks.py
    │   ├── fix_recalled_mtimes.py
    │   └── fix_series_cold_tags.py
    └── venv/                    # local Python environment
```
