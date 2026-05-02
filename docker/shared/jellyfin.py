import os
import logging
import datetime
import requests
from config import JELLYFIN, HEADERS, USER_IDS, EXTS

COLD_SUFFIX          = " — ❄ Cold Storage Media"
COLD_TAG             = "cold-storage"
PARTIAL_COLD_SUFFIX  = " — ❄ Partial Cold Storage"
PARTIAL_COLD_TAG     = "partial-cold-storage"
STRIP_FIELDS         = ["Trickplay", "UserData", "People", "Studios", "GenreItems", "ArtistItems"]


def fetch_items_for_user(user_id):
    """
    Fetch all movies and episodes with UserData for a specific user.
    Paginates in batches of 500.
    Returns: { path → { id, played, last_played, in_progress } }
    """
    items_map = {}
    batch     = 500
    start     = 0

    while True:
        r    = requests.get(f"{JELLYFIN}/Items", headers=HEADERS, params={
            "recursive":        True,
            "includeItemTypes": "Movie,Episode",
            "fields":           "Path",
            "enableUserData":   True,
            "userId":           user_id,
            "limit":            batch,
            "startIndex":       start,
        })
        data  = r.json()
        items = data.get("Items", [])

        for item in items:
            path = item.get("Path", "")
            iid  = item.get("Id", "")
            ud   = item.get("UserData", {})
            if not path or not iid:
                continue

            last_played = None
            lp_str      = ud.get("LastPlayedDate")
            if lp_str:
                try:
                    last_played = datetime.datetime.fromisoformat(lp_str[:19])
                except Exception:
                    pass

            items_map[path] = {
                "id":          iid,
                "played":      ud.get("Played", False),
                "last_played": last_played,
                "in_progress": (
                    ud.get("PlaybackPositionTicks", 0) > 0
                    and not ud.get("Played", False)
                ),
            }

        start += batch
        if start >= data.get("TotalRecordCount", 0):
            break

    return items_map


def get_all_items():
    """
    Fetch and merge play history across all users.
    - played      = True if ANY user has Played=true
    - last_played = most recent LastPlayedDate across all users
    - in_progress = True if ANY user has position > 0 and not fully played
    Items not visible to any user are excluded (admin-only content).
    Returns: { path → { id, played, last_played, in_progress } }
    """
    merged = {}

    for user_id in USER_IDS:
        logging.info(f"Fetching items for user {user_id}")
        for path, info in fetch_items_for_user(user_id).items():
            if path not in merged:
                merged[path] = dict(info)
            else:
                merged[path]["played"]      = merged[path]["played"] or info["played"]
                merged[path]["in_progress"] = merged[path]["in_progress"] or info["in_progress"]
                lp_new = info["last_played"]
                lp_cur = merged[path]["last_played"]
                if lp_new and (lp_cur is None or lp_new > lp_cur):
                    merged[path]["last_played"] = lp_new

    logging.info(f"Merged library: {len(merged)} items across {len(USER_IDS)} users")
    return merged


def get_item_path(item_id):
    """
    Fetch the file path for a single item directly from Jellyfin.
    Returns path string or None if item not found (already deleted).
    """
    r = requests.get(
        f"{JELLYFIN}/Users/{USER_IDS[0]}/Items/{item_id}",
        headers=HEADERS,
        params={"fields": "Path"},
        timeout=10,
    )
    if r.status_code != 200:
        return None
    return r.json().get("Path") or None


def _get_item_for_update(item_id):
    """
    Fetch full item data for update.
    Strips fields that cause Jellyfin deserialization errors on POST.
    Returns item dict or None on failure.
    """
    r = requests.get(
        f"{JELLYFIN}/Users/{USER_IDS[0]}/Items/{item_id}",
        headers=HEADERS
    )
    if r.status_code != 200:
        logging.warning(f"Could not GET item {item_id}: {r.status_code}")
        return None

    item = r.json()
    for field in STRIP_FIELDS:
        item.pop(field, None)
    if "ProviderIds" not in item:
        item["ProviderIds"] = {}
    return item


def _post_item(item_id, item):
    """POST updated item back to Jellyfin. Returns True on success."""
    r = requests.post(
        f"{JELLYFIN}/Items/{item_id}",
        headers={**HEADERS, "Content-Type": "application/json"},
        json=item
    )
    if r.status_code in (200, 204):
        return True
    logging.warning(f"Failed to update item {item_id}: {r.status_code} {r.text}")
    return False


def mark_as_cold(item_id):
    """
    Mark a Jellyfin item as fully cold.
    Removes any partial-cold markers first (upgrade: partial → full cold).
    """
    if not item_id:
        return False
    item = _get_item_for_update(item_id)
    if item is None:
        return False

    # Remove partial-cold markers first
    item["Tags"] = [t for t in item.get("Tags", []) if t != PARTIAL_COLD_TAG]
    taglines = item.get("Taglines", [])
    if taglines:
        item["Taglines"] = [taglines[0].replace(PARTIAL_COLD_SUFFIX, "")] + taglines[1:]
    overview = item.get("Overview", "")
    if PARTIAL_COLD_SUFFIX in overview:
        item["Overview"] = overview.replace(PARTIAL_COLD_SUFFIX, "")

    # Apply cold markers
    taglines = item.get("Taglines", [])
    original = taglines[0] if taglines else ""
    if COLD_SUFFIX not in original:
        item["Taglines"] = [original + COLD_SUFFIX] + taglines[1:]
    overview = item.get("Overview", "")
    if COLD_SUFFIX not in overview:
        item["Overview"] = overview + COLD_SUFFIX
    if COLD_TAG not in item.get("Tags", []):
        item.setdefault("Tags", []).append(COLD_TAG)

    success = _post_item(item_id, item)
    if success:
        logging.info(f"Marked as cold: {item_id}")
    return success


def mark_as_partial_cold(item_id):
    """
    Mark a Jellyfin item as partially cold (season/series with mixed hot+cold episodes).
    Removes full cold markers first (downgrade: full → partial cold).
    Only meaningful for Season and Series items.
    """
    if not item_id:
        return False
    item = _get_item_for_update(item_id)
    if item is None:
        return False

    # Remove full cold markers first
    item["Tags"] = [t for t in item.get("Tags", []) if t != COLD_TAG]
    taglines = item.get("Taglines", [])
    if taglines:
        item["Taglines"] = [taglines[0].replace(COLD_SUFFIX, "")] + taglines[1:]
    overview = item.get("Overview", "")
    if COLD_SUFFIX in overview:
        item["Overview"] = overview.replace(COLD_SUFFIX, "")

    # Apply partial-cold markers
    taglines = item.get("Taglines", [])
    original = taglines[0] if taglines else ""
    if PARTIAL_COLD_SUFFIX not in original:
        item["Taglines"] = [original + PARTIAL_COLD_SUFFIX] + taglines[1:]
    overview = item.get("Overview", "")
    if PARTIAL_COLD_SUFFIX not in overview:
        item["Overview"] = overview + PARTIAL_COLD_SUFFIX
    if PARTIAL_COLD_TAG not in item.get("Tags", []):
        item.setdefault("Tags", []).append(PARTIAL_COLD_TAG)

    success = _post_item(item_id, item)
    if success:
        logging.info(f"Marked as partial cold: {item_id}")
    return success


def mark_as_hot(item_id):
    """
    Remove all cold/partial-cold markers from a Jellyfin item.
    Safe to call multiple times.
    """
    if not item_id:
        return False
    item = _get_item_for_update(item_id)
    if item is None:
        return False

    # Remove both cold and partial-cold tags
    item["Tags"] = [t for t in item.get("Tags", []) if t not in (COLD_TAG, PARTIAL_COLD_TAG)]

    # Remove both suffixes from tagline
    taglines = item.get("Taglines", [])
    if taglines:
        cleaned = taglines[0].replace(COLD_SUFFIX, "").replace(PARTIAL_COLD_SUFFIX, "")
        item["Taglines"] = [cleaned] + taglines[1:]

    # Remove both suffixes from overview
    overview = item.get("Overview", "")
    item["Overview"] = overview.replace(COLD_SUFFIX, "").replace(PARTIAL_COLD_SUFFIX, "")

    success = _post_item(item_id, item)
    if success:
        logging.info(f"Marked as hot OK: {item_id}")
    else:
        logging.warning(f"mark_as_hot POST failed: {item_id}")
    return success

def _count_season_episodes(season_path):
    """Return (hot_count, cold_count) of video files in a season folder."""
    hot = cold = 0
    for fname in os.listdir(season_path):
        if os.path.splitext(fname)[1].lower() not in EXTS:
            continue
        fpath = os.path.join(season_path, fname)
        if os.path.islink(fpath):
            cold += 1
        elif os.path.isfile(fpath):
            hot += 1
    return hot, cold


def get_episode_parents(item_id):
    """
    Given an episode item_id, return its parent Series ID, Season ID and season path.
    Returns (series_id, season_id, season_path) — any can be None on failure.
    """
    r = requests.get(
        f"{JELLYFIN}/Users/{USER_IDS[0]}/Items/{item_id}",
        headers=HEADERS
    )
    if r.status_code != 200:
        return None, None, None

    item        = r.json()
    series_id   = item.get("SeriesId")
    season_id   = item.get("SeasonId")
    path        = item.get("Path", "")
    season_path = os.path.dirname(path) if path else None

    return series_id, season_id, season_path


def mark_recently_played(item_id):
    """
    Mark an item as played now for all users so the next archive run
    doesn't immediately re-flag it as cold after recall.
    Called after every successful recall alongside mark_as_hot.
    """
    if not item_id:
        return
    for user_id in USER_IDS:
        try:
            requests.post(
                f"{JELLYFIN}/Users/{user_id}/PlayedItems/{item_id}",
                headers=HEADERS,
                timeout=10,
            )
        except Exception as e:
            logging.warning(f"Could not mark as recently played for {item_id}: {e}")


def update_season_series_hot_status(item_id):
    """
    After an episode is recalled, update Season and Series tags to reflect
    the actual disk state:
      all hot            → mark_as_hot
      some cold/some hot → mark_as_partial_cold
      all cold           → mark_as_cold  (shouldn't happen during recall)
    Checks season first, then walks the whole show folder for the series.
    """
    series_id, season_id, season_path = get_episode_parents(item_id)

    if not season_path or not os.path.isdir(season_path):
        logging.warning(f"Could not determine season path for {item_id}")
        return

    # ── Season ────────────────────────────────────────────────────────────────
    if season_id:
        hot, cold = _count_season_episodes(season_path)
        if cold == 0:
            mark_as_hot(season_id)
            logging.info(f"Season fully hot: {season_id}")
        elif hot == 0:
            mark_as_cold(season_id)
            logging.info(f"Season fully cold: {season_id}")
        else:
            mark_as_partial_cold(season_id)
            logging.info(f"Season partial cold ({cold}c/{hot}h): {season_id}")

    # ── Series ────────────────────────────────────────────────────────────────
    if series_id:
        show_path  = os.path.dirname(season_path)
        total_hot  = total_cold = 0
        for entry in os.listdir(show_path):
            sp = os.path.join(show_path, entry)
            if os.path.isdir(sp):
                h, c       = _count_season_episodes(sp)
                total_hot  += h
                total_cold += c
        if total_cold == 0:
            mark_as_hot(series_id)
            logging.info(f"Series fully hot: {series_id}")
        elif total_hot == 0:
            mark_as_cold(series_id)
            logging.info(f"Series fully cold: {series_id}")
        else:
            mark_as_partial_cold(series_id)
            logging.info(f"Series partial cold ({total_cold}c/{total_hot}h): {series_id}")