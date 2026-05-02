import datetime
from config import (
    MOVIE_PLAYED_COLD_DAYS,
    MOVIE_NEVER_WATCHED_COLD_DAYS,
    MOVIE_ABANDONED_DAYS,
)
from storage import file_age_days


def movie_cold_candidate(info, src):
    """
    Decide if a movie should be archived.

    Rules:
    - Not in library for these users  → ignore (None)
    - In progress + abandoned > 60d   → COLD
    - In progress + recent            → STAY
    - Never watched + added <= 30d    → STAY
    - Never watched + added > 30d     → COLD
    - Played + last played <= 14d     → STAY
    - Played + last played > 14d      → COLD

    Returns (should_archive: bool|None, reason: str)
    """
    age = file_age_days(src)
    now = datetime.datetime.now()

    if info is None:
        return None, "not in library for these users"

    if info["in_progress"]:
        lp = info["last_played"]
        if lp is None:
            return False, "in progress (no date recorded)"
        days_ago = (now - lp).days
        if days_ago > MOVIE_ABANDONED_DAYS:
            return True, f"in progress but abandoned {days_ago}d ago"
        return False, f"in progress, last opened {days_ago}d ago"

    if not info["played"]:
        if age <= MOVIE_NEVER_WATCHED_COLD_DAYS:
            return False, f"never watched, recently added ({age}d ago)"
        return True, f"never watched, added {age}d ago"

    lp = info["last_played"]
    if lp is None:
        return True, "played but no date recorded"

    days_ago = (now - lp).days
    if days_ago <= MOVIE_PLAYED_COLD_DAYS:
        return False, f"watched recently ({days_ago}d ago)"
    return True, f"watched {days_ago}d ago"


def days_until_cold_movie(info, src):
    """Estimate days until a movie becomes a cold candidate. None = no countdown."""
    now = datetime.datetime.now()
    age = file_age_days(src)

    if info is None:
        return None
    if info["in_progress"]:
        lp = info["last_played"]
        if lp is None:
            return None
        return max(0, MOVIE_ABANDONED_DAYS - (now - lp).days)
    if not info["played"]:
        return max(0, MOVIE_NEVER_WATCHED_COLD_DAYS - age)
    lp = info["last_played"]
    if lp is None:
        return None
    return max(0, MOVIE_PLAYED_COLD_DAYS - (now - lp).days)