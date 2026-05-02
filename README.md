# HotColdJelly

Automated hot/cold tiered storage for a self-hosted [Jellyfin](https://jellyfin.org) media server.

Media files you haven't watched in a while are moved to cheap cold storage and recalled on-demand when you press play — all without breaking Jellyfin paths or requiring any manual management.

---

## What it does

- **Archives** unwatched files to any [rclone](https://rclone.org)-supported backend (cloud storage, NAS, SFTP, …) based on configurable inactivity thresholds
- **Recalls** cold files automatically when playback starts, swapping the symlink for the real file
- **Cleans up** cold storage when items are deleted from the Jellyfin library
- **Tags** items in Jellyfin so users can see at a glance what's cold

Jellyfin paths never change. A symlink at the original location keeps everything working during and after the transition between hot and cold.

## How it works

```
Hot storage          Cold storage
(fast, local)        (cheap, remote)

/media/movies/Dune/
  Dune.mkv   ──────►  rclone upload  ──►  remote:Cold/movies/Dune/Dune.mkv
       │
       └──  replaced by symlink  ──►  /mnt/cold/movies/Dune/Dune.mkv
                                            ▲
                              Jellyfin streams via local mount
```

On playback, the listener detects the symlink, downloads the file in the background (or blocks, depending on your config), and swaps the symlink for the real file. The cold copy is deleted after a successful recall.

## Cold storage backends

HotColdJelly uses **rclone** as its cold storage abstraction. Any backend rclone supports works out of the box:

- Amazon S3 (and S3-compatible: Wasabi, Cloudflare R2, MinIO…)
- Backblaze B2
- Google Drive
- SFTP / SSH
- Local or network paths (NAS over SMB/NFS)
- And [many more](https://rclone.org/#providers)

Configure your backend once in `rclone.conf`, point `RCLONE_REMOTE` at it — HotColdJelly doesn't care what's behind it.

Cold storage must also be accessible as a **local mount** (WebDAV, NFS, FUSE, etc.) so that Jellyfin can stream from it via symlinks without needing to recall first.

## Components

| Container | Role |
|-----------|------|
| `listener` | Webhook server — handles recall on playback and cleanup on deletion |
| `archive` | Archival runner — scans hot dirs, applies thresholds, uploads to cold |

---

## Documentation

- [How it works](docs/how-it-works.md)
- [Docker setup](docs/docker-setup.md)
- [Cold mount setup](docs/cold-mount-setup.md)
- [Dev setup](docs/dev-setup.md)
- [Webhook setup](docs/webhook-setup.md)
- [Helper scripts](docs/helpers.md)
- [Configuration reference](docs/configuration.md)
- [Infrastructure](docs/infrastructure.md)

## Requirements

- Docker + Docker Compose
- Jellyfin with the [Webhook plugin](https://github.com/jellyfin/jellyfin-plugin-webhook)
- rclone configured with at least one remote
- Cold storage mounted locally (WebDAV, NFS, FUSE, or equivalent)
