"""Live progress line, remaining-time estimate and progress.json status file.

Remaining time uses the rate over the last `window` seconds, not the overall
average, so it adapts when the scan moves from tiny files to large ones or the
network slows down.
"""
import collections
import json
import os
import sys
import time
from typing import Optional


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


class Progress:
    def __init__(self, stage: str, total_files: Optional[int] = None, total_bytes: Optional[int] = None,
                 done_files: int = 0, done_bytes: int = 0, errors: int = 0,
                 json_path: Optional[str] = None, stream=None, clock=time.monotonic,
                 window: float = 60.0, note: str = ""):
        self.stage = stage
        self.total_files = total_files
        self.total_bytes = total_bytes
        self.files = done_files
        self.bytes = done_bytes
        self.dirs = 0
        self.errors = errors
        self.json_path = json_path
        self.stream = stream or sys.stderr
        self.clock = clock
        self.window = window
        self.note = note
        self.start = clock()
        self.samples = collections.deque([(self.start, 0)])  # (time, files this session)
        self.session_files = 0
        self._last_render = self._last_json = -1e9

    def add(self, files: int = 0, bytes: int = 0, errors: int = 0, dirs: int = 0):
        self.files += files
        self.bytes += bytes
        self.errors += errors
        self.dirs += dirs
        self.session_files += files
        now = self.clock()
        self.samples.append((now, self.session_files))
        while len(self.samples) > 2 and now - self.samples[0][0] > self.window:
            self.samples.popleft()

    def rate(self) -> float:
        (t0, f0), (t1, f1) = self.samples[0], self.samples[-1]
        return (f1 - f0) / (t1 - t0) if t1 > t0 else 0.0

    def eta_seconds(self) -> Optional[float]:
        if self.total_files is None:
            return None
        r = self.rate()
        if r <= 0:
            return None
        return max(0, self.total_files - self.files) / r

    def render(self) -> str:
        elapsed = fmt_duration(self.clock() - self.start)
        if self.total_files is None:  # listing: total not known yet
            return (f"[{self.stage}] {self.dirs:,} folders | {self.files:,} files found "
                    f"({fmt_bytes(self.bytes)}) | {self.rate():,.0f} files/s | "
                    f"Errors: {self.errors} | Elapsed {elapsed}")
        pct = 100 * self.files / self.total_files if self.total_files else 100
        size = f" | {fmt_bytes(self.bytes)} / {fmt_bytes(self.total_bytes)}" if self.total_bytes else ""
        note = f" | {self.note}" if self.note else ""
        return (f"[{self.stage}] Files {self.files:,} / {self.total_files:,} ({pct:.1f}%){size} | "
                f"{self.rate():,.0f} files/s | Errors: {self.errors} | Elapsed {elapsed} | "
                f"Remaining ~{fmt_duration(self.eta_seconds())}{note}")

    def tick(self, force: bool = False):
        now = self.clock()
        if force or now - self._last_render >= 1.0:
            self._last_render = now
            self.stream.write("\r" + self.render().ljust(150)[:200])
            self.stream.flush()
        if self.json_path and (force or now - self._last_json >= 5.0):
            self._last_json = now
            self.write_json()

    def write_json(self):
        data = {
            "stage": self.stage, "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "folders_listed": self.dirs, "files_done": self.files, "files_total": self.total_files,
            "bytes_done": self.bytes, "bytes_total": self.total_bytes, "errors": self.errors,
            "files_per_sec": round(self.rate(), 1),
            "elapsed_seconds": round(self.clock() - self.start),
            "eta_seconds": None if self.eta_seconds() is None else round(self.eta_seconds()),
            "note": self.note, "line": self.render(),
        }
        tmp = self.json_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.json_path)  # atomic: readers never see a half file

    def close(self):
        self.tick(force=True)
        self.stream.write("\n")
        self.stream.flush()
