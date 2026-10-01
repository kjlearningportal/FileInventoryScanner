import datetime as dt
import hashlib
import io
import json
import threading

import pytest

from fscan.progress import Progress, fmt_bytes, fmt_duration
from fscan.throttle import Schedule, Throttle
from fscan.worker import full_hash, process_file


class FakeSource:
    def __init__(self, files):
        self.files = files

    def open_read(self, path):
        if isinstance(self.files[path], Exception):
            raise self.files[path]
        return io.BytesIO(self.files[path])


def _row(path, data, ext=".bin"):
    return {"path": path, "source": "s", "ext": ext, "size": len(data)}


# ---- worker -----------------------------------------------------------------
def test_small_file_fingerprint_is_full_sha256():
    data = b"%PDF-1.4 small"
    r = process_file(FakeSource({"p": data}), _row("p", data, ".pdf"), chunk=1024)
    assert r["status"] == "done" and r["detected_type"] == "pdf"
    assert r["fp_is_full"] and r["sha256"] == hashlib.sha256(data).hexdigest()


def test_large_file_quick_fp_ignores_middle():
    a = b"A" * 5000 + b"X" * 100 + b"Z" * 5000
    b = b"A" * 5000 + b"Y" * 100 + b"Z" * 5000
    src = FakeSource({"a": a, "b": b})
    ra = process_file(src, _row("a", a), chunk=1024)
    rb = process_file(src, _row("b", b), chunk=1024)
    assert not ra["fp_is_full"] and ra["sha256"] is None
    assert ra["quick_fp"] == rb["quick_fp"]  # same size, head and tail
    stop = threading.Event()
    assert full_hash(src, "a", stop) != full_hash(src, "b", stop)


def test_unreadable_file_returns_error_not_exception():
    r = process_file(FakeSource({"p": PermissionError("Access is denied")}), _row("p", b"x"), chunk=1024)
    assert r["status"] == "error" and "PermissionError" in r["error"]


def test_empty_file():
    r = process_file(FakeSource({"p": b""}), _row("p", b""), chunk=1024)
    assert r["detected_type"] == "empty" and r["fp_is_full"]


def test_full_hash_stops_when_asked():
    stop = threading.Event()
    stop.set()
    assert full_hash(FakeSource({"p": b"x" * 10}), "p", stop) is None


# ---- throttle ---------------------------------------------------------------
def test_throttle_sleeps_to_hold_rate():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    t = Throttle(bytes_per_sec=100, clock=lambda: now[0], sleep=sleep)
    t.consume(100)  # first second's budget
    t.consume(100)  # must wait ~1s
    assert sum(slept) == pytest.approx(1.0, abs=0.01)


def test_unlimited_throttle_never_sleeps():
    t = Throttle(bytes_per_sec=0, sleep=lambda s: pytest.fail("slept"))
    t.consume(10**9)


def test_schedule_overnight_window():
    s = Schedule("19:00-07:00")
    assert s.allowed(dt.datetime(2026, 1, 1, 22, 0))
    assert s.allowed(dt.datetime(2026, 1, 1, 6, 59))
    assert not s.allowed(dt.datetime(2026, 1, 1, 12, 0))
    assert Schedule(None).allowed(dt.datetime(2026, 1, 1, 12, 0))


# ---- progress ---------------------------------------------------------------
def test_formatting():
    assert fmt_bytes(1536) == "1.5 KB"
    assert fmt_bytes(3.1 * 1024**4) == "3.1 TB"
    assert fmt_duration(3 * 3600 + 5 * 60) == "3h05m"
    assert fmt_duration(65) == "1m05s"


def test_eta_uses_recent_rate_and_writes_json(tmp_path):
    now = [0.0]
    out = io.StringIO()
    p = Progress("process", total_files=1000, total_bytes=10_000,
                 json_path=str(tmp_path / "progress.json"), stream=out, clock=lambda: now[0])
    for _ in range(10):  # 10 files/s for 10s
        now[0] += 1
        p.add(files=10, bytes=100)
    assert p.eta_seconds() == pytest.approx(90, rel=0.05)  # 900 left at 10/s
    p.tick(force=True)
    line = out.getvalue()
    assert "100 / 1,000" in line and "Remaining ~1m30s" in line
    data = json.loads((tmp_path / "progress.json").read_text())
    assert data["stage"] == "process" and data["files_done"] == 100
    assert data["eta_seconds"] == pytest.approx(90, rel=0.05)


def test_listing_progress_has_no_eta(tmp_path):
    out = io.StringIO()
    p = Progress("listing", json_path=str(tmp_path / "p.json"), stream=out)
    p.add(dirs=3, files=50, bytes=2048)
    p.tick(force=True)
    assert p.eta_seconds() is None
    assert "3 folders" in out.getvalue() and "50 files" in out.getvalue()
