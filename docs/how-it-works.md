# How it works

## Storage tiers

| Tier | Location | Purpose |
|------|----------|---------|
| Hot | Local fast storage (HDD, SSD, NAS) | Active media, full-speed access |
| Cold | Any rclone-supported backend | Long-term storage for unwatched media |

Cold storage is accessed through **rclone**, which supports Google Drive, Amazon S3, Backblaze B2, SFTP, local paths, and many more. You configure your backend once in `rclone.conf` and point `RCLONE_REMOTE` at it — HotColdJelly doesn't care what's behind it.

Cold storage must also be accessible as a **local mount** (WebDAV, NFS, FUSE, etc.) so that Jellyfin can stream from it via symlinks without needing to recall first. The mount path is configured via `COLD_PATH`.

## Archive

Runs on a schedule or manually. For each file in the hot directories:

1. Fetch play history from Jellyfin for all configured users
2. Classify the file against the configured thresholds (played, never watched, abandoned, etc.)
3. If it qualifies:
   - Upload via rclone to cold backend
   - Delete the local file
   - Create a symlink at the same path pointing to the cold mount
   - Tag the item in Jellyfin as cold

Jellyfin paths never change — the symlink keeps everything working.

See [configuration.md](configuration.md) for the threshold variables.

## Recall

Triggered by a Jellyfin `PlaybackStart` webhook when a cold file is played:

1. Detect that the path is a symlink (cold)
2. Download the file from the remote via rclone
3. Atomically swap the symlink for the real file
4. Strip cold tags in Jellyfin
5. Delete the file from the cold backend

Two modes (set via `RECALL_BLOCKING`):

| Mode | Behaviour | Best for |
|------|-----------|---------|
| `false` (background) | Symlink stays while downloading; player streams via mount meanwhile | Slow remotes (cloud storage) |
| `true` (blocking) | Symlink removed first; download must complete before playback starts | Fast remotes (local NAS) |

## Deletion cleanup

Triggered by a Jellyfin `ItemDeleted` webhook when an item is removed from the library:

| Jellyfin type | Action |
|---------------|--------|
| Movie | Purge entire movie folder from cold backend |
| Episode | Delete the single episode file from cold backend |
| Season | Purge the season folder from cold backend |
| Series | Purge the entire show folder from cold backend |

Path is resolved from the listener's in-memory items cache (populated at startup, refreshed hourly).

## Jellyfin tagging

Items are tagged so users can see what's cold at a glance:

| State | Tag | Tagline / Overview suffix |
|-------|-----|--------------------------|
| Fully cold | `cold-storage` | ` — ❄ Cold Storage Media` |
| Partially cold (some episodes hot, some cold) | `partial-cold-storage` | ` — ❄ Partial Cold Storage` |
| Hot | *(none)* | *(none)* |
