import os
import time

import pytest

from fscan.sources.filesystem import FileSystemSource, display_path, long_path


@pytest.mark.skipif(os.name != "nt", reason="Windows path rules")
def test_long_path_prefixes_unc_and_drive_paths():
    assert long_path(r"\\server\share\a.txt") == r"\\?\UNC\server\share\a.txt"
    assert long_path(r"C:\data\a.txt") == r"\\?\C:\data\a.txt"
    assert long_path(r"\\?\C:\data") == r"\\?\C:\data"


@pytest.mark.skipif(os.name != "nt", reason="Windows path rules")
def test_display_path_strips_prefix():
    assert display_path(r"\\?\UNC\server\share\a") == r"\\server\share\a"
    assert display_path(r"\\?\C:\data") == r"C:\data"


def test_list_dir_returns_files_and_dirs_with_dates(tmp_path):
    (tmp_path / "sub").mkdir()
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello")
    src = FileSystemSource("t", str(tmp_path))

    entries = {e.name: e for e in src.list_dir(str(tmp_path))}

    assert set(entries) == {"sub", "a.txt"}
    assert entries["sub"].is_dir and not entries["a.txt"].is_dir
    a = entries["a.txt"]
    assert a.size == 5
    assert a.path == os.path.join(str(tmp_path), "a.txt")
    assert abs(a.modified - time.time()) < 120
    assert a.created is not None and a.accessed > 0
    assert not a.is_link


def test_open_read_is_binary_and_readonly(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"\x00\x01")
    src = FileSystemSource("t", str(tmp_path))
    with src.open_read(str(f)) as fh:
        assert fh.read() == b"\x00\x01"
        assert fh.mode == "rb"


def test_symlinks_are_marked_as_links(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")
    src = FileSystemSource("t", str(tmp_path))
    entries = {e.name: e for e in src.list_dir(str(tmp_path))}
    assert entries["link"].is_link
    assert not entries["real"].is_link


def _make_lone_surrogate_file(tmp_path):
    """NTFS allows names with unpaired UTF-16 surrogates; they are not valid Unicode."""
    name = "bad\udf52name.txt"
    try:
        with open(os.path.join(str(tmp_path), name), "wb") as fh:
            fh.write(b"data")
    except (OSError, UnicodeError):
        pytest.skip("file system does not allow lone surrogates in names")
    return name


def test_lone_surrogate_names_are_escaped_and_still_openable(tmp_path):
    _make_lone_surrogate_file(tmp_path)
    src = FileSystemSource("t", str(tmp_path))

    (e,) = list(src.list_dir(str(tmp_path)))

    assert e.name == "bad<U+DF52>name.txt"
    e.path.encode("utf-8")  # storable in SQLite / CSV
    with src.open_read(e.path) as fh:
        assert fh.read() == b"data"


def test_lone_surrogate_folder_can_be_listed(tmp_path):
    sub = tmp_path / "dir\udf52x"
    try:
        sub.mkdir()
    except (OSError, UnicodeError):
        pytest.skip("file system does not allow lone surrogates in names")
    (sub / "a.txt").write_bytes(b"x")
    src = FileSystemSource("t", str(tmp_path))

    (d,) = list(src.list_dir(str(tmp_path)))
    (child,) = list(src.list_dir(d.path))

    assert d.name == "dir<U+DF52>x"
    assert child.path == os.path.join(d.path, "a.txt")
