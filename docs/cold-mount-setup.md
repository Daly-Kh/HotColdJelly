# Cold mount setup

HotColdJelly needs cold storage accessible as a **local path** so Jellyfin can stream symlink targets directly. This page covers how to set that up on a Linux host.

## Why a local mount

Symlinks created during archival point to a path inside `COLD_PATH` (e.g. `/mnt/cold/movies/Dune/Dune.mkv`). Jellyfin reads symlink targets at stream time — it doesn't go through rclone. The mount must be up for cold content to be playable.

rclone handles all actual data movement (upload, download, delete). The mount is read-path only.

---

## Option 1: rclone FUSE mount (recommended for cloud backends)

Works with any rclone-supported remote. Requires FUSE on the host.

### Install FUSE

```bash
# Debian / Ubuntu
sudo apt install fuse3

# Fedora / RHEL
sudo dnf install fuse3
```

### Mount

```bash
sudo mkdir -p /mnt/cold

rclone mount myremote:path/to/Cold /mnt/cold \
    --vfs-cache-mode full \
    --allow-other \
    --daemon
```

`--vfs-cache-mode full` enables local caching so seeks and partial reads work correctly for video streaming.

`--allow-other` lets other users (including the Docker daemon) access the mount — requires `user_allow_other` in `/etc/fuse.conf`.

### Verify

```bash
ls /mnt/cold
```

---

## Option 2: NFS mount (for NAS or local network storage)

If your cold storage is a NAS exporting an NFS share:

```bash
sudo apt install nfs-common
sudo mkdir -p /mnt/cold
sudo mount -t nfs 192.168.1.x:/volume1/Cold /mnt/cold
```

---

## Option 3: WebDAV mount

If your cold storage exposes a WebDAV endpoint (or you use `rclone serve webdav`):

```bash
sudo apt install davfs2
sudo mkdir -p /mnt/cold
sudo mount -t davfs http://your-webdav-host:8765 /mnt/cold
```

For `rclone serve webdav` as the WebDAV server:

```bash
rclone serve webdav myremote:path/to/Cold \
    --addr 127.0.0.1:8765 \
    --vfs-cache-mode full \
    --daemon
```

Then mount `http://127.0.0.1:8765`.

---

## Automating with systemd

### rclone FUSE mount unit

Create `/etc/systemd/system/rclone-cold-mount.service`:

```ini
[Unit]
Description=rclone FUSE mount for HotColdJelly cold storage
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/bin/rclone mount myremote:path/to/Cold /mnt/cold \
    --vfs-cache-mode full \
    --allow-other \
    --log-level INFO \
    --log-file /var/log/rclone-cold-mount.log
ExecStop=/bin/fusermount -u /mnt/cold
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable rclone-cold-mount
sudo systemctl start rclone-cold-mount
```

Check status:

```bash
sudo systemctl status rclone-cold-mount
journalctl -u rclone-cold-mount -f
```

### NFS mount via /etc/fstab

```
192.168.1.x:/volume1/Cold  /mnt/cold  nfs  defaults,_netdev,auto  0  0
```

`_netdev` ensures the mount waits for the network before trying.

---

## Docker considerations

The cold mount must be done on the **host** before Docker starts, then bound into the containers:

```yaml
volumes:
  - /mnt/cold:/mnt/cold
```

Or use a `bind` mount with `propagation: shared` if the mount happens after Docker starts:

```yaml
volumes:
  - type: bind
    source: /mnt/cold
    target: /mnt/cold
    bind:
      propagation: shared
```

If the mount goes down, Jellyfin will get I/O errors reading cold symlinks — the listener will still serve webhooks and the archive will still run, but cold content won't be streamable until the mount recovers.

---

## Choosing between options

| Setup | Best for |
|-------|---------|
| rclone FUSE | Cloud remotes (S3, Backblaze, GDrive, SFTP) |
| NFS | NAS on local network — low latency, kernel-level |
| WebDAV | When NFS isn't available or the remote only speaks HTTP |
