import csv
import hashlib
import io
import os

import pytest

from fscan.config import ConfigError, build_config
from fscan.engine import Scanner
from fscan.sources.filesystem import FileSystemSource
from fscan.state import State

CHUNK = 1024


def _tree(root):
    big = b"A" * 3000 + b"M" * 50 + b"Z" * 3000
    near = b"A" * 3000 + b"N" * 50 + b"Z" * 3000  # same size, head, tail; middle differs
    files = {
        "docs/report.pdf": b"%PDF-1.7 report",
        "docs/copy_of_report.pdf": b"%PDF-1.7 report",
        "docs/fake.pdf": b"MZ" + b"\x00" * 100,
        "data/big1.bin": big,
        "data/big2.bin": big,
        "data/near.bin": near,
        "data/table.csv": b"a,b\n1,2\n",
        "data/empty.txt": b"",
        "tmp/~$lock.docx": b"lock",
    }
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return files


def _snapshot(root):
    out = {}
    for dirpath, _, names in os.walk(root):
        for n in names:
            p = os.path.join(dirpath, n)
            st = os.stat(p)
            out[p] = (st.st_size, st.st_mtime_ns, hashlib.sha256(open(p, "rb").read()).hexdigest())
    return out


def _setup(tmp_path, **overrides):
    root = tmp_path / "share"
    root.mkdir()
    _tree(root)
    raw = {"sources": [{"name": "share", "root": str(root)}],
           "output_dir": str(tmp_path / "out"),
           "exclude": {"files": ["~$*"]},
           "workers": 2, "list_workers": 2, "chunk_mb": CHUNK / 1024 / 1024,
           "max_read_mb_per_sec": 0, "batch_size": 2, "batch_seconds": 0.01, "sample_size": 2}
    raw.update(overrides)
    cfg = build_config(raw)
    return root, cfg


def _scanner(cfg, **kw):
    state = State(cfg.state_db)
    sources = {s.name: FileSystemSource(s.name, s.root) for s in cfg.sources}
    return Scanner(cfg, state, sources, stream=io.StringIO(), **kw), state


def _inventory(cfg):
    with open(os.path.join(cfg.output_dir, "inventory.csv"), encoding="utf-8-sig") as f:
        return {os.path.relpath(r["path"], r["path"].split("share")[0] + "share").replace("\\", "/"): r
                for r in csv.DictReader(f)}


def test_full_scan_end_to_end(tmp_path):
    root, cfg = _setup(tmp_path)
    before = _snapshot(root)
    scanner, state = _scanner(cfg)

    assert scanner.run(assume_yes=True) == "complete"

    inv = _inventory(cfg)
    assert len(inv) == 9
    assert inv["docs/report.pdf"]["detected_type"] == "pdf"
    assert inv["docs/fake.pdf"]["type_group"] == "executable"
    assert inv["docs/fake.pdf"]["ext_mismatch"] == "True"
    assert inv["data/table.csv"]["type_group"] == "data"
    assert inv["tmp/~$lock.docx"]["status"] == "skipped"
    # duplicates: small identical files, large identical files; near-copy is NOT a duplicate
    assert inv["docs/report.pdf"]["dup_count"] == "2"
    assert inv["data/big1.bin"]["dup_count"] == "2"
    assert inv["data/near.bin"]["dup_count"] == "1"
    assert inv["data/near.bin"]["sha256"] and inv["data/near.bin"]["sha256"] != inv["data/big1.bin"]["sha256"]
    assert inv["data/empty.txt"]["dup_count"] == "1"
    assert inv["data/big1.bin"]["created_utc"] and inv["data/big1.bin"]["modified_utc"]
    for name in ("inventory.parquet", "errors.csv", "summary.json", "progress.json"):
        assert os.path.exists(os.path.join(cfg.output_dir, name))
    # Guardrail 1: nothing on the source changed
    assert _snapshot(root) == before


def test_interrupted_scan_resumes_without_redoing_work(tmp_path):
    root, cfg = _setup(tmp_path)
    for i in range(60):
        (root / "many" / f"f{i}.txt").parent.mkdir(exist_ok=True)
        (root / "many" / f"f{i}.txt").write_bytes(f"file {i}".encode())
    scanner, state = _scanner(cfg)

    def crash_in_main_pass():  # "crash" after the first batch saved after confirmation
        if state.get("estimate_confirmed") == "1":
            scanner.stop.set()
    scanner.after_batch = crash_in_main_pass
    assert scanner.run(assume_yes=True) == "stopped"
    first = {r["path"]: r["processed"] for r in state.db.execute(
        "SELECT path, processed FROM files WHERE status='done'")}
    assert 0 < len(first) < 68
    state.close()

    scanner, state = _scanner(cfg, confirm=lambda _: pytest.fail("must not ask again on resume"))
    assert scanner.run() == "complete"
    after = {r["path"]: r["processed"] for r in state.db.execute(
        "SELECT path, processed FROM files WHERE status='done'")}
    assert len(after) == 68
    assert all(after[p] == t for p, t in first.items())  # earlier work was not redone


def test_declining_the_estimate_starts_nothing_else(tmp_path):
    root, cfg = _setup(tmp_path)
    scanner, state = _scanner(cfg, confirm=lambda _: False)
    assert scanner.run() == "declined"
    assert state.get("estimate_confirmed") is None
    assert "TIME ESTIMATE" in scanner.stream.getvalue()
    assert state.counts()["files_done"] <= cfg.sample_size


def test_output_inside_source_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="inside source"):
        build_config({"sources": [{"name": "s", "root": str(tmp_path)}],
                      "output_dir": str(tmp_path / "out")})


def test_unknown_config_key_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="unknown config keys"):
        build_config({"sources": [{"name": "s", "root": str(tmp_path / "a")}],
                      "output_dir": str(tmp_path / "b"), "wrokers": 3})
