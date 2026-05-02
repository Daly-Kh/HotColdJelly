# Docker setup

## Prerequisites

- Docker and Docker Compose
- rclone installed and configured on the host (`rclone.conf` with your remote)
- Cold storage mounted locally (WebDAV, NFS, FUSE, etc.) at the path you'll set as `COLD_PATH`
- Jellyfin running and accessible from the Docker host

## 1. Configure environment

Copy the example file and fill in your values:

```bash
cp docker/.env.example docker/.env
```

Key variables to set:

| Variable | Description |
|----------|-------------|
| `JELLYFIN_URL` | Full URL to your Jellyfin instance |
| `JELLYFIN_API_KEY` | API key from Jellyfin Dashboard → API Keys |
| `JELLYFIN_USER_IDS` | Comma-separated user IDs whose play history drives archival |
| `HOT_DIRS` | Comma-separated absolute paths to hot media directories |
| `COLD_PATH` | Local mount point where cold storage is accessible |
| `RCLONE_REMOTE` | `remote:path` as configured in `rclone.conf` |
| `HOST_MEDIA_VOLUME` | Host path to the root of your media drive |
| `COLD_MOUNT` | Host path to the cold storage mount (same as `COLD_PATH`) |
| `RCLONE_CONFIG` | Host path to the directory containing `rclone.conf` |
| `LOGS_VOLUME` | Host path where logs should be written |

See [configuration.md](configuration.md) for all variables and their defaults.

## 2. Volume mounts

The `docker-compose.yml` binds four host paths into each container. The paths inside the container must match what `HOT_DIRS` and `COLD_PATH` are set to:

```yaml
volumes:
  - ${HOST_MEDIA_VOLUME}:/mnt/media      # hot storage root
  - ${COLD_MOUNT}:/mnt/cold              # cold mount
  - ${RCLONE_CONFIG}:/root/.config/rclone:ro
  - ${LOGS_VOLUME}:/logs
```

Adjust the container-side mount targets in `docker-compose.yml` to match your `HOT_DIRS` and `COLD_PATH` values if they differ.

## 3. Build and start

```bash
cd docker
docker compose up -d --build
```

This starts two containers:
- `hot-cold-jelly_listener` — webhook server on port 5001
- `hot-cold-jelly_archive` — scheduled archival runner

## Common commands

```bash
# View live logs
docker logs -f hot-cold-jelly_listener
docker logs -f hot-cold-jelly_archive

# Restart a container
docker compose restart hot-cold-jelly-listener
docker compose restart hot-cold-jelly-archive

# Rebuild after code changes
docker compose up -d --build

# Stop everything
docker compose down
```

## Archive schedule

The archive container runs on a cron inside the container. It does not expose a port. To trigger a manual run:

```bash
docker exec hot-cold-jelly_archive python3 main.py
```

## Networking

The listener needs to reach Jellyfin. If Jellyfin runs on the same Docker host (not in a container), add `extra_hosts` so the container can resolve the host:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

This is already present in the default `docker-compose.yml`. Use `host.docker.internal` in `JELLYFIN_URL` if needed.
