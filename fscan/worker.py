"""Per-file work, run in worker threads. Never writes to the source.

process_file reads at most `chunk` bytes from the start and `chunk` from the
end of a file: enough to detect the real type and build a quick fingerprint.
Files no larger than 2 x chunk are read whole, so their fingerprint is a true
SHA-256 of the content.
"""
import hashlib
import threading

from .detect import detect

CHUNK = 1024 * 1024
_HASH_BLOCK = 4 * 1024 * 1024


class _NoThrottle:
    def consume(self, n):
        pass


def process_file(source, row, chunk: int = CHUNK, throttle=None) -> dict:
    throttle = throttle or _NoThrottle()
    path, size = row["path"], row["size"]
    try:
        head = tail = b""
        with source.open_read(path) as f:
            head = f.read(min(size, chunk))
            throttle.consume(len(head))
            if size > 2 * chunk:
                f.seek(size - chunk)
                tail = f.read(chunk)
            elif size > chunk:
                tail = f.read()
            throttle.consume(len(tail))
        d = detect(head, tail, row["ext"], size)
        fp_is_full = size <= 2 * chunk
        if fp_is_full:
            quick = sha = hashlib.sha256(head + tail).hexdigest()
        else:
            h = hashlib.sha256(size.to_bytes(8, "little"))
            h.update(head)
            h.update(tail)
            quick, sha = h.hexdigest(), None
        return {"path": path, "status": "done", "bytes_read": len(head) + len(tail),
                "detected_type": d.type, "type_group": d.group, "mime": d.mime,
                "ext_mismatch": d.ext_mismatch, "quick_fp": quick,
                "fp_is_full": fp_is_full, "sha256": sha}
    except Exception as e:  # one bad file must never stop the scan
        return {"path": path, "status": "error", "bytes_read": 0,
                "error": f"{type(e).__name__}: {e}"}


def full_hash(source, path: str, stop: threading.Event, throttle=None):
    """SHA-256 of the whole file; None if stopped part-way (redone on resume)."""
    throttle = throttle or _NoThrottle()
    h = hashlib.sha256()
    with source.open_read(path) as f:
        while True:
            if stop.is_set():
                return None
            block = f.read(_HASH_BLOCK)
            if not block:
                return h.hexdigest()
            throttle.consume(len(block))
            h.update(block)
