import os
import threading

from fscan.sources.filesystem import FileSystemSource
from fscan.state import State
from fscan.walker import Walker


def _tree(root):
    for d in ["a", "a/b", "c", "$RECYCLE.BIN"]:
        (root / d).mkdir(parents=True)
    for f in ["1.txt", "a/2.txt", "a/b/3.txt", "c/4.txt", "c/~$lock.docx", "$RECYCLE.BIN/x.txt"]:
        (root / f).write_bytes(b"data")


def _walker(state, root, **kw):
    src = FileSystemSource("share", str(root))
    return Walker(state, {"share": src}, exclude_dirs=["$RECYCLE.BIN"],
                  exclude_files=["~$*"], workers=2, **kw)


def test_walk_lists_everything_and_honours_excludes(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _tree(root)
    state = State(str(tmp_path / "s.db"))
    _walker(state, root).run()

    files = {os.path.basename(r["path"]): r["status"] for r in state.db.execute("SELECT path, status FROM files")}
    assert files == {"1.txt": "pending", "2.txt": "pending", "3.txt": "pending",
                     "4.txt": "pending", "~$lock.docx": "skipped"}
    assert state.counts()["dirs_pending"] == 0
    skipped = state.db.execute("SELECT status FROM dirs WHERE path LIKE '%RECYCLE%'").fetchone()
    assert skipped["status"] == "skipped"


def test_walk_resumes_after_interrupt_without_duplicates(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _tree(root)
    db = str(tmp_path / "s.db")

    stop = threading.Event()
    state = State(db)
    w = _walker(state, root, stop_event=stop)
    w.on_dir_listed = lambda: stop.set()  # "crash" after the first folder
    w.run()
    first = state.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    assert state.counts()["dirs_pending"] > 0
    state.close()

    state = State(db)  # new process picks up the queue
    _walker(state, root).run()
    total = state.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    assert first < total == 5
    assert state.counts()["dirs_pending"] == 0


def test_rescan_resets_changed_files_only(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "same.txt").write_bytes(b"x")
    (root / "changes.txt").write_bytes(b"x")
    state = State(str(tmp_path / "s.db"))
    _walker(state, root).run()
    state.db.execute("UPDATE files SET status='done', accessed=1.0")
    state.db.commit()

    (root / "changes.txt").write_bytes(b"xyz")
    state.start_new_run()
    _walker(state, root).run()

    rows = {os.path.basename(r["path"]): r for r in state.db.execute("SELECT * FROM files")}
    assert rows["same.txt"]["status"] == "done"
    assert rows["same.txt"]["accessed"] == 1.0  # pre-scan value kept
    assert rows["changes.txt"]["status"] == "pending"
    assert rows["changes.txt"]["size"] == 3
