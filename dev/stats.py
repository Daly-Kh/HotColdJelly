import datetime
import logging
import os
from logger import log_path


class ArchiveStats:
    """
    Tracks and displays stats for a single archive run.
    """

    def __init__(self):
        self.start_time     = datetime.datetime.now()
        self.files_archived = 0
        self.files_skipped  = 0
        self.files_failed   = 0
        self.gb_archived    = 0.0
        self.gb_failed      = 0.0
        self.failed_list    = []
        self._file_times    = []  # (fname, gb, elapsed_sec)

    def record_archived(self, fname, gb, elapsed_sec):
        self.files_archived += 1
        self.gb_archived    += gb
        self._file_times.append((fname, gb, elapsed_sec))

    def record_skipped(self):
        self.files_skipped += 1

    def record_failed(self, path, reason, gb=0.0):
        self.files_failed += 1
        self.gb_failed    += gb
        self.failed_list.append((path, reason))

    # ── Calculations ───────────────────────────────────────────────────────────

    def avg_speed_mbps(self):
        total_gb  = sum(gb  for _, gb,  _ in self._file_times)
        total_sec = sum(sec for _, _,  sec in self._file_times)
        if total_sec == 0:
            return 0.0
        return (total_gb * 1024) / total_sec

    def elapsed(self):
        return datetime.datetime.now() - self.start_time

    def elapsed_str(self):
        return self._fmt_sec(int(self.elapsed().total_seconds()))

    def _fmt_sec(self, secs):
        if secs >= 3600:
            return f"{secs//3600}h {(secs%3600)//60}m {secs%60}s"
        elif secs >= 60:
            return f"{secs//60}m {secs%60}s"
        return f"{secs}s"

    def _speed_str(self, gb, elapsed_sec):
        if elapsed_sec == 0:
            return "—"
        return f"{(gb * 1024) / elapsed_sec:.1f} MB/s"

    # ── Display ────────────────────────────────────────────────────────────────

    def print_file_done(self, fname, gb, elapsed_sec):
        """Print per-file stats right after a successful transfer."""
        print(f"  ✓ {fname}")
        print(f"    {gb:.2f} GB  |  {self._fmt_sec(int(elapsed_sec))}  |  "
              f"{self._speed_str(gb, elapsed_sec)}")
        print(f"    Run: {self.gb_archived:.2f} GB total  |  "
              f"avg {self.avg_speed_mbps():.1f} MB/s  |  "
              f"elapsed {self.elapsed_str()}")

    def print_summary(self, interrupted=False):
        """Print full summary at end of run."""
        status = "INTERRUPTED" if interrupted else "COMPLETE"
        sep    = "─" * 60

        lines = [
            f"\n{sep}",
            f"  Archive {status}",
            sep,
            f"  Started   {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"  Finished  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"  Duration  {self.elapsed_str()}",
            sep,
            f"  Archived  {self.gb_archived:.2f} GB  ({self.files_archived} files)",
            f"  Avg speed {self.avg_speed_mbps():.1f} MB/s",
        ]

        if self.files_skipped:
            lines.append(f"  Skipped   {self.files_skipped} files (already on cold)")
        if self.files_failed:
            lines.append(f"  Failed    {self.files_failed} files ({self.gb_failed:.2f} GB)")
            for path, reason in self.failed_list:
                lines.append(f"    ✗ {os.path.basename(path)} — {reason}")

        lines += [sep, f"  Log: {log_path()}", sep, ""]

        print("\n".join(lines))

        logging.info(
            f"Archive {status} | duration: {self.elapsed_str()} | "
            f"archived: {self.gb_archived:.2f}GB ({self.files_archived} files) | "
            f"avg speed: {self.avg_speed_mbps():.1f} MB/s | "
            f"skipped: {self.files_skipped} | failed: {self.files_failed}"
        )