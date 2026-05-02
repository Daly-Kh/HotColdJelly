# Webhook setup

HotColdJelly's listener reacts to two Jellyfin events in real time: playback start (triggers recall) and item deletion (triggers cold cleanup).

## 1. Install the Jellyfin webhook plugin

1. In Jellyfin, go to **Dashboard → Plugins → Catalog**
2. Find **Webhook** and install it
3. Restart Jellyfin

## 2. Add a webhook destination

Go to **Dashboard → Plugins → Webhook** and add a new destination:

| Field | Value |
|-------|-------|
| Name | HotColdJelly (or anything) |
| URL | `http://<host>:5001/webhook` |
| Notification type | `PlaybackStart`, `ItemDeleted` |
| Item type | (leave blank — listener filters internally) |
| Send all properties | ✓ enabled |

Replace `<host>` with the IP or hostname of the machine running the listener. If Jellyfin and the listener are on the same host, use `host.docker.internal` (Docker) or `localhost` (dev mode).

## 3. Test the connection

Jellyfin has a **Test** button on each webhook destination. The listener should log:

```
Webhook received: NotificationType='Test' ItemType=''
```

## 4. Verify playback recall

Play a cold item (one with the `cold-storage` tag). The listener should log:

```
RECALL START: MovieName.mkv
RECALL DONE: MovieName.mkv — downloaded via rclone
```

## 5. Verify deletion cleanup

Delete an item from the Jellyfin library (admins only). The listener should log:

```
Webhook received: NotificationType='ItemDeleted' ItemType='Movie'
DELETE remote: remote:path/to/file.mkv
```

## Webhook payload

Jellyfin sends a JSON payload. The listener reads:

| Field | Used for |
|-------|---------|
| `NotificationType` | Route to `PlaybackStart` or `ItemDeleted` handler |
| `ItemType` | Distinguish Episode / Season / Series / Movie on deletion |
| `ItemId` | Look up the item path via Jellyfin API or in-memory cache |
| `SeriesName` | Locate the show folder in cache when deleting Season/Series |
| `SeasonNumber` | Locate the season subfolder when deleting a Season |

`Path` is intentionally not used from the payload — Jellyfin omits it in `ItemDeleted` events because the item is already removed from the database by the time the webhook fires.
