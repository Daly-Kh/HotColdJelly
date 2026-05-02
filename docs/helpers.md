# Helper scripts

All helpers live in `dev/helpers/` and run from the `dev/` directory with the venv active:

```bash
cd dev
source venv/bin/activate
```

---

## audit.py — consistency check

The main diagnostic script. Read-only — never modifies anything.

```bash
python3 helpers/audit.py              # full audit (includes remote check via rclone)
python3 helpers/audit.py --skip-remote  # skip the slow rclone listing (checks A–F only)
```

Checks performed:

| Check | Description | Fix script |
|-------|-------------|------------|
| A | Broken symlinks — symlink on disk, file gone from cold remote | `fix_broken_symlinks.py --apply` |
| B | Jellyfin lost a cold file — symlink on disk, not in Jellyfin | Trigger a Jellyfin library rescan |
| C | Missing cold tag — cold on disk, episode has no cold-storage tag | `fix_series_cold_tags.py --apply` |
| D | Stale cold tag — hot on disk, episode still tagged as cold | `fix_hot_tags.py --apply` |
| E | Season tag mismatch — season tag doesn't match actual ep state | `fix_series_cold_tags.py --apply` |
| F | Series tag mismatch — series tag doesn't match actual ep state | `fix_series_cold_tags.py --apply` |
| G | Remote orphans — file on cold remote, deleted from Jellyfin | `cleanup_remote_orphans.py --purge` |

---

## fix_broken_symlinks.py — fix check A

Finds broken cold symlinks (target doesn't resolve on the cold mount), removes them, and strips cold tags in Jellyfin.

```bash
python3 helpers/fix_broken_symlinks.py           # dry run
python3 helpers/fix_broken_symlinks.py --apply   # apply fixes
```

What it does per broken symlink:
1. Remove the dangling symlink from disk
2. Call `mark_as_hot` in Jellyfin for the item
3. Recompute and update the parent season and series tags based on remaining disk state

---

## fix_series_cold_tags.py — fix checks C, E, F

Recomputes cold/partial-cold/hot tags for all seasons and series based on actual disk state.

```bash
python3 helpers/fix_series_cold_tags.py           # dry run
python3 helpers/fix_series_cold_tags.py --apply
```

---

## fix_hot_tags.py — fix check D

Strips `cold-storage` tags from items that are actually hot files on disk.

```bash
python3 helpers/fix_hot_tags.py           # dry run
python3 helpers/fix_hot_tags.py --apply
```

---

## fix_recalled_mtimes.py — fix file timestamps

After recall, rclone sets the file's mtime to match the remote. This script resets mtimes to now so the archive doesn't immediately re-archive freshly recalled files.

```bash
python3 helpers/fix_recalled_mtimes.py           # dry run
python3 helpers/fix_recalled_mtimes.py --apply
```

---

## fix_missing_symlinks.py — recover stranded cold files

Finds files on the cold remote that have no corresponding hot symlink — the state where the archive uploaded a file but the symlink was never created or was deleted, leaving the file stranded on cold with no pointer visible to Jellyfin.

```bash
python3 helpers/fix_missing_symlinks.py           # dry run
python3 helpers/fix_missing_symlinks.py --apply   # apply fixes
```

What it does per missing symlink:

1. Create the symlink at the expected hot path pointing to the cold mount
2. Call `mark_as_cold` in Jellyfin for the item (if found in library)

After running with `--apply`, trigger a Jellyfin library rescan for any items that weren't already in the library.

---

## cleanup_remote_orphans.py — fix check G

Lists all video files on the cold remote via `rclone lsjson`, cross-references with the Jellyfin library and disk symlinks, and finds folders with no matching Jellyfin item.

```bash
python3 helpers/cleanup_remote_orphans.py          # dry run (lists orphans)
python3 helpers/cleanup_remote_orphans.py --purge  # purge orphan folders from remote
```

An orphan is a folder on cold storage where no Jellyfin item and no disk symlink points. This can happen if deletion webhooks were missed or the system was offline during a deletion.
