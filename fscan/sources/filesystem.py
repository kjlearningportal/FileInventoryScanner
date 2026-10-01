"""Local disk / Windows share (UNC) source.

Uses os.scandir so size and dates arrive with the folder listing: one network
round trip per folder rather than one per file.

NTFS names can hold unpaired UTF-16 surrogates (left by Mac/Linux clients or
old apps). They are not valid Unicode, so SQLite, CSV and Parquet reject them.
Each one is written as "<U+DXXX>" in paths we hand out and turned back into the
real character before touching the disk. '<' is not allowed in Windows names,
so the marker cannot clash with a real name.
"""
import os
import re
from typing import BinaryIO, Iterator

from .base import Entry

_UNC_PREFIX = "\\\\?\\UNC\\"
_LONG_PREFIX = "\\\\?\\"
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_SURROGATE_MARK = re.compile(r"<U\+(D[89A-F][0-9A-F]{2})>")


def escape_name(name: str) -> str:
    return _LONE_SURROGATE.sub(lambda m: f"<U+{ord(m.group()):04X}>", name)


def unescape_path(path: str) -> str:
    return _SURROGATE_MARK.sub(lambda m: chr(int(m.group(1), 16)), path)


def long_path(path: str) -> str:
    """Add the \\\\?\\ prefix so paths over 260 characters work on Windows."""
    if os.name != "nt" or path.startswith(_LONG_PREFIX):
        return path
    if path.startswith("\\\\"):
        return _UNC_PREFIX + path[2:]
    return _LONG_PREFIX + os.path.abspath(path)


def display_path(path: str) -> str:
    if path.startswith(_UNC_PREFIX):
        return "\\\\" + path[len(_UNC_PREFIX):]
    if path.startswith(_LONG_PREFIX):
        return path[len(_LONG_PREFIX):]
    return path


class FileSystemSource:
    def __init__(self, name: str, root: str):
        self.name = name
        self.root = root if root.startswith("\\\\") else os.path.abspath(root)

    def list_dir(self, path: str) -> Iterator[Entry]:
        with os.scandir(long_path(unescape_path(path))) as it:
            for de in it:
                yield self._entry(path, de)

    def open_read(self, path: str) -> BinaryIO:
        # Read-only by construction: the scanner never opens anything for write.
        return open(long_path(unescape_path(path)), "rb")

    @staticmethod
    def _entry(parent: str, de: os.DirEntry) -> Entry:
        st = de.stat(follow_symlinks=False)
        # Only symlinks and junctions count as links. Other reparse points
        # (dedup'd or cloud-tiered files) are real files and must be scanned.
        is_link = de.is_symlink() or (hasattr(de, "is_junction") and de.is_junction())
        is_dir = not is_link and de.is_dir(follow_symlinks=False)
        created = getattr(st, "st_birthtime", None)
        if created is None and os.name == "nt":
            created = st.st_ctime  # creation time on Windows before 3.12
        name = escape_name(de.name)
        return Entry(
            path=os.path.join(parent, name),
            name=name,
            is_dir=is_dir,
            is_link=is_link,
            size=0 if is_dir else st.st_size,
            created=created,
            modified=st.st_mtime,
            accessed=st.st_atime,
            attributes=getattr(st, "st_file_attributes", 0),
        )
