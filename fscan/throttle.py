"""Keep load on the file server low: a shared read-rate cap and scan hours."""
import datetime as dt
import threading
import time
from typing import Optional


class Throttle:
    """Token bucket shared by all worker threads. bytes_per_sec=0 means no cap."""

    def __init__(self, bytes_per_sec: float, clock=time.monotonic, sleep=time.sleep):
        self.rate = bytes_per_sec
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.Lock()
        self.allowance = bytes_per_sec
        self.last = clock()

    def consume(self, n: int):
        if self.rate <= 0 or n <= 0:
            return
        with self.lock:
            now = self.clock()
            self.allowance = min(self.rate, self.allowance + (now - self.last) * self.rate)
            self.last = now
            self.allowance -= n
            wait = -self.allowance / self.rate if self.allowance < 0 else 0
        if wait > 0:
            self.sleep(wait)


class Schedule:
    """Allowed scan window, e.g. '19:00-07:00' (may cross midnight). None = any time."""

    def __init__(self, window: Optional[str]):
        self.window = window
        if window:
            start, end = window.split("-")
            self.start = dt.time.fromisoformat(start.strip())
            self.end = dt.time.fromisoformat(end.strip())

    def allowed(self, now: Optional[dt.datetime] = None) -> bool:
        if not self.window:
            return True
        t = (now or dt.datetime.now()).time()
        if self.start <= self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end

    def wait_until_allowed(self, stop: threading.Event, on_wait=lambda: None):
        while not self.allowed() and not stop.is_set():
            on_wait()
            stop.wait(30)
