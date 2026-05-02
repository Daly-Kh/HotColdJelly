import os
import logging
import datetime
from config import LOG_BASE_DIR

_run_start = None
_log_path  = None


def setup(mode="archive"):
    """
    Create a dated log folder and log file for this run.

    Structure:
    /logs/
    └── 2026-03-27/
        └── archive_2026-03-27_14-32-00.log
    """
    global _run_start, _log_path

    _run_start = datetime.datetime.now()
    date_str   = _run_start.strftime("%Y-%m-%d")
    time_str   = _run_start.strftime("%H-%M-%S")
    log_dir    = os.path.join(LOG_BASE_DIR, date_str)
    os.makedirs(log_dir, exist_ok=True)

    _log_path = os.path.join(log_dir, f"{mode}_{date_str}_{time_str}.log")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in root.handlers[:]:
        root.removeHandler(h)

    fh = logging.FileHandler(_log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    root.addHandler(fh)

    # Also log to stdout so Docker logs work
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    root.addHandler(sh)

    logging.info(f"Run started: {_run_start.strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Log: {_log_path}")


def run_start():
    return _run_start


def log_path():
    return _log_path