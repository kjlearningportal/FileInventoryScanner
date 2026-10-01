"""Source interface: everything the scanner needs from a storage system.

A source only ever *reads*. Adding Linux, S3 or SharePoint later means writing
one new class with these two methods.
"""
from dataclasses import dataclass
from typing import BinaryIO, Iterator, Optional, Protocol


@dataclass(frozen=True)
class Entry:
    path: str                 # display path (no \\?\ prefix)
    name: str
    is_dir: bool
    is_link: bool             # symlink or junction: recorded, never followed
    size: int
    created: Optional[float]  # epoch seconds; None when the source can't tell
    modified: float
    accessed: float           # captured from the listing, before any read
    attributes: int = 0       # Windows file attribute bits (hidden, system, ...)


class Source(Protocol):
    name: str
    root: str

    def list_dir(self, path: str) -> Iterator[Entry]: ...

    def open_read(self, path: str) -> BinaryIO: ...
