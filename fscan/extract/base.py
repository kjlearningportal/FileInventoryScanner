"""Shared pieces for extractors: chunks with locations, context, limits, readers."""
import bisect
import io
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

SPOOL_MAX = 64 * 1024 * 1024  # nested files above this spill to a private temp file (auto-deleted)


class Interrupted(Exception):
    """Stop requested; the file is left pending and redone on resume."""


class Unreadable(Exception):
    """The file (or nested part) cannot be read, e.g. encrypted. `note` explains why."""

    def __init__(self, note: str):
        super().__init__(note)
        self.note = note


@dataclass
class Chunk:
    text: str
    inner_path: str
    locate: Callable[[int], str]


@dataclass
class Context:
    ocr: Optional[object] = None
    stop: threading.Event = field(default_factory=threading.Event)
    max_depth: int = 3
    unpack_budget: int = 10 * 1024 ** 3      # total bytes unpacked from archives per top-level file
    bomb_ratio: float = 100.0                # expansion ratio that marks a zip bomb...
    bomb_min_size: int = 100 * 1024 ** 2     # ...for members at least this large
    ocr_min_side: int = 100                  # skip icons / tiny images
    notes: set = field(default_factory=set)
    partial: bool = False                    # some part couldn't be read
    pages: int = 0
    ocr_pages: int = 0

    def note(self, text: str, partial: bool = True):
        self.notes.add(text)
        if partial:
            self.partial = True

    def check_stop(self):
        if self.stop.is_set():
            raise Interrupted()


def fixed(label: str) -> Callable[[int], str]:
    return lambda pos: label


class LineLocator:
    """Maps a position in a text chunk to 'line N'."""

    def __init__(self, text: str, first_line: int, prefix: str = "line"):
        self.text, self.first, self.prefix = text, first_line, prefix

    def __call__(self, pos: int) -> str:
        return f"{self.prefix} {self.first + self.text.count(chr(10), 0, pos)}"


class OffsetLocator:
    """Maps a position to the label of the segment (cell, paragraph) containing it."""

    def __init__(self):
        self.starts, self.labels = [], []

    def add(self, start: int, label: str):
        self.starts.append(start)
        self.labels.append(label)

    def __call__(self, pos: int) -> str:
        i = bisect.bisect_right(self.starts, pos) - 1
        return self.labels[max(i, 0)] if self.labels else ""


def join_inner(inner: str, name: str) -> str:
    return f"{inner} > {name}" if inner else name


def spool(stream, ctx: Context, limit: Optional[int] = None):
    """Copy a stream into memory (or a private temp file if large). Enforces the unpack budget."""
    out = tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX)
    total = 0
    while True:
        ctx.check_stop()
        block = stream.read(1024 * 1024)
        if not block:
            break
        total += len(block)
        ctx.unpack_budget -= len(block)
        if ctx.unpack_budget < 0 or (limit is not None and total > limit):
            out.close()
            raise Unreadable("zip_bomb_or_unpack_limit")
        out.write(block)
    out.seek(0)
    return out


class ThrottledRaw(io.RawIOBase):
    """Read-only wrapper that charges every read against the shared rate cap."""

    def __init__(self, f, throttle=None):
        self.f, self.throttle, self.bytes_read = f, throttle, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def readinto(self, b):
        data = self.f.read(len(b))
        n = len(data)
        b[:n] = data
        self.bytes_read += n
        if self.throttle:
            self.throttle.consume(n)
        return n

    def seek(self, offset, whence=io.SEEK_SET):
        return self.f.seek(offset, whence)

    def tell(self):
        return self.f.tell()


def throttled(f, throttle=None):
    raw = ThrottledRaw(f, throttle)
    return io.BufferedReader(raw, buffer_size=1024 * 1024), raw
