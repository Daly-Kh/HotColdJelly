import os
import datetime
from config import EXTS, SHOW_INACTIVITY_COLD_DAYS, SHOW_ALL_WATCHED_COLD_DAYS
from storage import oldest_file_age_days, show_size_gb


def get_show_status(hot_dir, items_map):
    """
    Compute watch status at the show level.

    Last activity:
    - Most recent LastPlayedDate if any episode watched
    - Oldest file mtime (added date) if never watched

    Admin-only episodes excluded from counts.

    Returns: { show_path → info_dict }
    """
    shows = {}

    for show in os.listdir(hot_dir):
        show_path = os.path.join(hot_dir, show)
        if not os.path.isdir(show_path):
            continue

        total       = 0
        cold        = 0
        played      = 0
        in_progress = 0
        latest_lp   = None

        for season in os.listdir(show_path):
            season_path = os.path.join(show_path, season)
            if not os.path.isdir(season_path):
                continue

            for fname in os.listdir(season_path):
                if os.path.splitext(fname)[1].lower() not in EXTS:
                    continue
                src  = os.path.join(season_path, fname)
                if os.path.islink(src):
                    cold += 1
                    # Still track play history from cold episodes
                    info = items_map.get(src)
                    if info is not None:
                        lp = info.get("last_played")
                        if lp and (latest_lp is None or lp > latest_lp):
                            latest_lp = lp
                    continue
                info = items_map.get(src)
                if info is None:
                    continue  # admin-only

                total += 1

                if info["played"]:
                    played += 1
                    lp = info["last_played"]
                    if lp and (latest_lp is None or lp > latest_lp):
                        latest_lp = lp
                elif info["in_progress"]:
                    in_progress += 1
                    lp = info["last_played"]
                    if lp and (latest_lp is None or lp > latest_lp):
                        latest_lp = lp

        # Skip fully-cold shows — nothing left to archive
        if total == 0 and cold == 0:
            continue
        if total == 0:
            continue

        if latest_lp is not None:
            last_activity     = latest_lp
            last_activity_src = "last played"
        else:
            age = oldest_file_age_days(show_path)
            if age is None:
                # No hot files and no play history — treat as never touched
                last_activity     = datetime.datetime.min
                last_activity_src = "recently added"
            else:
                last_activity     = datetime.datetime.now() - datetime.timedelta(days=age)
                last_activity_src = "recently added"

        shows[show_path] = {
            "show":              show,
            "show_path":         show_path,
            "total":             total,
            "cold":              cold,
            "played":            played,
            "in_progress":       in_progress,
            "last_activity":     last_activity,
            "last_activity_src": last_activity_src,
            "all_watched":       played == total and total > 0,
        }

    return shows


def show_cold_decision(info):
    """
    Three rules:
    1. All watched + last activity > SHOW_ALL_WATCHED_COLD_DAYS → COLD
    2. Any activity within SHOW_INACTIVITY_COLD_DAYS            → STAY
    3. Inactive > SHOW_INACTIVITY_COLD_DAYS                     → COLD

    Returns (should_archive: bool, reason: str)
    """
    now           = datetime.datetime.now()
    activity_days = (now - info["last_activity"]).days

    if info["all_watched"] and activity_days > SHOW_ALL_WATCHED_COLD_DAYS:
        return True, f"all {info['total']} eps watched, last activity {activity_days}d ago"

    if activity_days <= SHOW_INACTIVITY_COLD_DAYS:
        return False, f"active {activity_days}d ago ({info['last_activity_src']})"

    return True, f"inactive {activity_days}d ({info['last_activity_src']})"


def days_until_cold_show(info):
    """Estimate days until a show becomes a cold candidate."""
    activity_days = (datetime.datetime.now() - info["last_activity"]).days

    if info["all_watched"]:
        return max(0, SHOW_ALL_WATCHED_COLD_DAYS - activity_days)
    return max(0, SHOW_INACTIVITY_COLD_DAYS - activity_days)