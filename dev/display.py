import os
import datetime
from movies import days_until_cold_movie
from shows import days_until_cold_show
from config import (
    MOVIE_PLAYED_COLD_DAYS,
    MOVIE_NEVER_WATCHED_COLD_DAYS,
    MOVIE_ABANDONED_DAYS,
    SHOW_INACTIVITY_COLD_DAYS,
    SHOW_ALL_WATCHED_COLD_DAYS,
)


# ─── Time helpers ──────────────────────────────────────────────────────────────

def _relative_time(dt):
    """
    Return a human-readable relative time string with exact date appended.
    Examples: "22d ago (07/03/26)", "~3mo ago (14/07/25)", "2h 15m ago (29/03/26)"
    """
    now      = datetime.datetime.now()
    diff     = now - dt
    secs     = diff.total_seconds()
    date_str = dt.strftime("%d/%m/%y")

    if secs < 3600:
        mins = int(secs // 60)
        return f"{mins}m ago ({date_str})"
    if secs < 86400:
        hours = int(secs // 3600)
        mins  = int((secs % 3600) // 60)
        return f"{hours}h {mins}m ago ({date_str})"
    if diff.days < 30:
        return f"{diff.days}d ago ({date_str})"
    if diff.days < 365:
        months = diff.days // 30
        return f"~{months}mo ago ({date_str})"
    years  = diff.days // 365
    months = (diff.days % 365) // 30
    if months:
        return f"~{years}y {months}mo ago ({date_str})"
    return f"~{years}y ago ({date_str})"


def _file_age_relative(path):
    """Return relative time string for a file's mtime, or None on error."""
    try:
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        return _relative_time(mtime)
    except Exception:
        return None


def _format_countdown(days_left):
    if days_left == 0:
        return "  ⚑  cold tomorrow"
    if days_left <= 7:
        return f"  ⚑  cold in {days_left}d"
    return f"  ⏳ cold in ~{days_left}d"


def _days_until_cold(lp_str, decision, path, info):
    if "KEEP_HOT" in decision:
        return None
    if isinstance(info, dict) and "show" in info:
        return days_until_cold_show(info)
    return days_until_cold_movie(info, path)


# ─── Entry renderers ───────────────────────────────────────────────────────────

def _render_movie(lp_str, size_gb, label, path, decision, info, show_countdown):
    verdict  = "COLD ❄" if decision.startswith("COLD") else "STAY ✓"
    reason   = decision.split("|", 1)[1].strip() if "|" in decision else decision

    # File age from mtime
    age_str  = _file_age_relative(path) or "unknown"

    # Jellyfin status
    if info is None:
        jf_status = "not in library"
        lp_line   = ""
    else:
        if info.get("in_progress"):
            jf_status = "in progress"
        elif info.get("played"):
            jf_status = "watched"
        else:
            jf_status = "not watched"

        lp = info.get("last_played")
        if lp:
            lp_str_fmt = _relative_time(lp)
            # Warn when Jellyfin has a play date but doesn't mark the item as played/in-progress
            if not info.get("played") and not info.get("in_progress"):
                lp_line = f"    last played: {lp_str_fmt}  ⚠ play date recorded but not marked as watched"
            else:
                lp_line = f"    last played: {lp_str_fmt}"
        else:
            lp_line = "    last played: never"

    countdown = ""
    if show_countdown:
        days_left = _days_until_cold(lp_str, decision, path, info)
        if days_left is not None:
            countdown = _format_countdown(days_left)

    print(f"  {label}")
    print(f"    {size_gb:.1f}GB  ·  added {age_str}")
    print(f"    jellyfin: {jf_status}{countdown}")
    print(lp_line)
    print(f"    {verdict}  ·  {reason}")
    print()
    return size_gb


def _render_show(lp_str, size_gb, label, path, decision, info, show_countdown):
    verdict  = "COLD ❄" if decision.startswith("COLD") else "STAY ✓"
    reason   = decision.split("|", 1)[1].strip() if "|" in decision else decision

    total       = info.get("total", 0)
    cold        = info.get("cold", 0)
    total_all   = total + cold
    played      = info.get("played", 0)
    in_prog     = info.get("in_progress", 0)
    unwatched   = total - played - in_prog
    src         = info.get("last_activity_src", "")
    last_act    = _relative_time(info["last_activity"])

    # Storage line
    if cold == 0:
        storage_str = f"{total_all}/{total_all} hot"
    elif total == 0:
        storage_str = f"0/{total_all} hot  ·  {cold}/{total_all} cold ❄  (fully on cold)"
    else:
        storage_str = f"{total}/{total_all} hot  ·  {cold}/{total_all} cold ❄"

    countdown = ""
    if show_countdown:
        days_left = _days_until_cold(lp_str, decision, path, info)
        if days_left is not None:
            countdown = _format_countdown(days_left)

    print(f"  {label}")
    print(f"    {size_gb:.1f}GB  ·  {total_all} eps  ·  {played} watched  ·  {in_prog} in progress  ·  {unwatched} unwatched")
    print(f"    storage: {storage_str}")
    print(f"    last activity: {last_act}  ({src}){countdown}")
    print(f"    {verdict}  ·  {reason}")
    print()
    return size_gb


# ─── Public display ────────────────────────────────────────────────────────────

def display(entries, show_countdown=False):
    """Print entries sorted by last activity (most recent first)."""
    if not entries:
        print("  (none)")
        return 0.0

    total = 0.0
    for lp_str, size_gb, label, path, decision, info in sorted(
        entries, key=lambda x: x[0], reverse=True
    ):
        is_show = isinstance(info, dict) and "show" in info
        if is_show:
            total += _render_show(lp_str, size_gb, label, path, decision, info, show_countdown)
        else:
            total += _render_movie(lp_str, size_gb, label, path, decision, info, show_countdown)
    return total


def _split_by_type(entries):
    movies = [e for e in entries if not (isinstance(e[5], dict) and "show" in e[5])]
    shows  = [e for e in entries if isinstance(e[5], dict) and "show" in e[5]]
    return movies, shows


def _print_legend():
    print("  Entry format — Movies:")
    print("    Title")
    print("    Size  ·  added AGO (DD/MM/YY)")
    print("    jellyfin: watched | in progress | not watched  [countdown if staying hot]")
    print("    last played: AGO (DD/MM/YY)  or  never")
    print("    COLD ❄ / STAY ✓  ·  Reason")
    print()
    print("  Entry format — Shows:")
    print("    Title")
    print("    Size  ·  N eps  ·  W watched  ·  P in progress  ·  U unwatched")
    print("    storage: X/N hot  ·  Y/N cold ❄")
    print("    last activity: AGO (DD/MM/YY)  (last played | recently added)")
    print("    COLD ❄ / STAY ✓  ·  Reason")
    print()
    print(f"  Archive rules — Movies:")
    print(f"    STAY  not in library (admin-only content — ignored)")
    print(f"    STAY  in progress, last opened <{MOVIE_ABANDONED_DAYS}d ago")
    print(f"    COLD  in progress but abandoned >{MOVIE_ABANDONED_DAYS}d ago")
    print(f"    STAY  never watched, recently added (<{MOVIE_NEVER_WATCHED_COLD_DAYS}d)")
    print(f"    COLD  never watched, added >{MOVIE_NEVER_WATCHED_COLD_DAYS}d ago")
    print(f"    STAY  watched recently (<{MOVIE_PLAYED_COLD_DAYS}d ago)")
    print(f"    COLD  watched >{MOVIE_PLAYED_COLD_DAYS}d ago")
    print(f"    STAY  KEEP_HOT rule (filename pinned in config)")
    print()
    print(f"  Archive rules — Shows (evaluated at show level, not per-episode):")
    print(f"    COLD  all eps watched + last activity >{SHOW_ALL_WATCHED_COLD_DAYS}d ago")
    print(f"    STAY  any activity within last {SHOW_INACTIVITY_COLD_DAYS}d")
    print(f"    COLD  inactive >{SHOW_INACTIVITY_COLD_DAYS}d  (last activity = most recent play, or oldest file mtime)")
    print()
    print("  ⚠  on last played = Jellyfin has a play date but item is not marked as watched")
    print("     (can happen after manually marking as unwatched — Jellyfin keeps the date)")
    print()


def _print_section(title, count, entries, show_countdown):
    print(f"  {title}  ({count})")
    print("  " + "-" * 48)
    return display(entries, show_countdown=show_countdown)


def print_dry_run(to_archive, to_keep):
    sep = "=" * 70

    archive_movies, archive_shows = _split_by_type(to_archive)
    hot_movies,     hot_shows     = _split_by_type(to_keep)

    print(sep)
    print("DRY RUN — no files will be moved")
    print(sep)
    _print_legend()

    print(sep)
    print("WOULD ARCHIVE → cold storage:")
    print(sep)
    print()
    total_cold_movies = _print_section("MOVIES", len(archive_movies), archive_movies, show_countdown=False)
    total_cold_shows  = _print_section("SHOWS",  len(archive_shows),  archive_shows,  show_countdown=False)
    total_cold = total_cold_movies + total_cold_shows
    print(f"  → Total to archive:  {total_cold:.1f}GB"
          f"  ({total_cold_movies:.1f}GB movies  +  {total_cold_shows:.1f}GB shows)")

    print()
    print(sep)
    print("STAYS ON HOT:")
    print(sep)
    print()
    total_hot_movies = _print_section("MOVIES", len(hot_movies), hot_movies, show_countdown=True)
    total_hot_shows  = _print_section("SHOWS",  len(hot_shows),  hot_shows,  show_countdown=True)
    total_hot = total_hot_movies + total_hot_shows
    print(f"  → Total staying hot: {total_hot:.1f}GB"
          f"  ({total_hot_movies:.1f}GB movies  +  {total_hot_shows:.1f}GB shows)")
    print(sep)
