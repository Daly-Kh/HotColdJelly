# Dev setup

For running scripts locally without Docker — useful for one-off operations, audits, and helper scripts.

## Prerequisites

- Python 3.9+
- rclone installed and configured
- Cold storage accessible via local mount

## 1. Create the virtual environment

```bash
cd dev
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Configure dev/.env

Copy the example file and fill in your values:

```bash
cp dev/.env.example dev/.env
```

Then edit `dev/.env` — at minimum set:

| Variable | Where to find it |
| -------- | ---------------- |
| `JELLYFIN_URL` | URL of your Jellyfin instance |
| `JELLYFIN_API_KEY` | Jellyfin Dashboard → API Keys |
| `JELLYFIN_USER_IDS` | Dashboard → Users → click user → copy ID from the URL |
| `HOT_DIRS` | Comma-separated absolute paths to your hot media directories |
| `COLD_PATH` | Local mount point where cold storage is accessible |
| `RCLONE_REMOTE` | Remote and path as configured in `rclone.conf` |

`dev/.env` is gitignored — it never gets committed. `dev/.env.example` is the committed template; keep it updated if you add new config fields.

## 3. Run scripts

All helpers run from the `dev/` directory with the venv active:

```bash
cd dev
source venv/bin/activate

python3 helpers/audit.py
python3 helpers/audit.py --skip-remote
python3 helpers/fix_broken_symlinks.py
python3 helpers/fix_broken_symlinks.py --apply
```

See [helpers.md](helpers.md) for the full list.

## Running the listener locally

```bash
cd dev
source venv/bin/activate
python3 listener.py
```

The listener starts on port 5001 and accepts the same Jellyfin webhooks as the Docker version.

## Running the archive locally

```bash
cd dev
source venv/bin/activate
python3 main.py
```

Set `MAX_GB` in `dev/.env` conservatively when testing — the archive will upload and delete real files.

## Tip: dry run mode

Set `RUN_MODE=dry_run` in `dev/.env` (or the equivalent env var when using Docker) to see what the archive would do without touching any files.
