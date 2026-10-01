"""A worker process dying (e.g. a native crash inside the PDF library) must not stop the scan."""
import io
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor

from fscan.config import build_config
from fscan.engine import Scanner
from fscan.progress import Progress


def crashy_job(row):
    if row["path"] == "bad":
        os._exit(1)  # simulate a hard crash in native code
    return {"path": row["path"], "status": "done", "bytes_read": 1}


def test_crashed_worker_is_replaced_and_other_files_finish(tmp_path):
    (tmp_path / "share").mkdir()
    cfg = build_config({"sources": [{"name": "s", "root": str(tmp_path / "share")}],
                        "output_dir": str(tmp_path / "out"), "batch_seconds": 0.01})
    scanner = Scanner(cfg, state=None, sources={}, stream=io.StringIO())
    rows = [{"path": p, "size": 1} for p in ["a", "b", "bad", "c", "d", "e", "f", "g"]]
    saved = []
    ctx = multiprocessing.get_context("spawn")
    progress = Progress("test", total_files=len(rows), stream=io.StringIO())

    scanner._run_pool(rows, crashy_job, saved.extend, progress,
                      make_executor=lambda: ProcessPoolExecutor(1, mp_context=ctx), workers=1)

    by_path = {r["path"]: r["status"] for r in saved}
    assert by_path["bad"] == "error"
    assert set(by_path) == {r["path"] for r in rows}     # every file accounted for
    assert [p for p, st in by_path.items() if st == "done"]  # later files still processed
