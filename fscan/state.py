"""SQLite state database: the checkpoint that makes every stage resumable.

Only the main thread touches the database. Workers return results; the main
thread saves them in batches, so a crash loses at most one batch.
"""
import os
import sqlite3
import time
from typing import Iterable, Optional

from .sources.base import Entry

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS dirs (
    path     TEXT PRIMARY KEY,
    source   TEXT NOT NULL,
    status   TEXT NOT NULL DEFAULT 'pending',   -- pending | listed | error | skipped
    attempts INTEGER NOT NULL DEFAULT 0,
    error    TEXT
);
CREATE INDEX IF NOT EXISTS dirs_status ON dirs(status);

CREATE TABLE IF NOT EXISTS files (
    path          TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    dir           TEXT NOT NULL,
    name          TEXT NOT NULL,
    ext           TEXT,
    size          INTEGER NOT NULL,
    created       REAL,
    modified      REAL,
    accessed      REAL,          -- pre-scan value from the folder listing
    attributes    INTEGER,
    status        TEXT NOT NULL DEFAULT 'pending',   -- pending | done | error | skipped
    attempts      INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    detected_type TEXT,
    type_group    TEXT,
    mime          TEXT,
    ext_mismatch  INTEGER,
    quick_fp      TEXT,
    fp_is_full    INTEGER,       -- 1 when the whole file was read for quick_fp
    sha256        TEXT,
    processed     REAL,
    seen_run      INTEGER
);
CREATE INDEX IF NOT EXISTS files_status ON files(status);
CREATE INDEX IF NOT EXISTS files_quick_fp ON files(quick_fp);

-- Stage 5: sensitive data + topics, one row per readable file
CREATE TABLE IF NOT EXISTS content (
    path             TEXT PRIMARY KEY,
    status           TEXT NOT NULL DEFAULT 'pending',   -- pending | done | error
    attempts         INTEGER NOT NULL DEFAULT 0,
    error            TEXT,
    source_processed REAL,         -- files.processed when queued; a change re-queues the file
    content_status   TEXT,         -- ok | partial | unreadable
    sensitivity      TEXT,
    pii_types        TEXT,
    pii_counts       TEXT,
    has_credentials  INTEGER,
    topics           TEXT,
    topic_scores     TEXT,
    text_chars       INTEGER,
    pages            INTEGER,
    ocr_pages        INTEGER,
    note             TEXT,
    processed        REAL
);
CREATE INDEX IF NOT EXISTS content_status_idx ON content(status);

-- Masked findings only. Raw values are never written anywhere.
CREATE TABLE IF NOT EXISTS findings (
    path           TEXT NOT NULL,
    inner_path     TEXT,
    data_type      TEXT NOT NULL,
    masked_value   TEXT NOT NULL,
    confidence     TEXT NOT NULL,
    occurrences    INTEGER NOT NULL,
    first_location TEXT
);
CREATE INDEX IF NOT EXISTS findings_path ON findings(path);
"""

_UPSERT_FILE = """
INSERT INTO files (path, source, dir, name, ext, size, created, modified, accessed,
                   attributes, status, error, seen_run)
VALUES (:path, :source, :dir, :name, :ext, :size, :created, :modified, :accessed,
        :attributes, :status, :error, :run)
ON CONFLICT(path) DO UPDATE SET
    seen_run = excluded.seen_run,
    -- Unchanged file: keep results and the original pre-scan access time
    -- (our own earlier read may have bumped it on the server).
    status   = CASE WHEN files.size = excluded.size AND files.modified = excluded.modified
                    THEN files.status ELSE excluded.status END,
    attempts = CASE WHEN files.size = excluded.size AND files.modified = excluded.modified
                    THEN files.attempts ELSE 0 END,
    accessed = CASE WHEN files.size = excluded.size AND files.modified = excluded.modified
                    THEN files.accessed ELSE excluded.accessed END,
    sha256   = CASE WHEN files.size = excluded.size AND files.modified = excluded.modified
                    THEN files.sha256 ELSE NULL END,
    size = excluded.size, created = excluded.created, modified = excluded.modified,
    attributes = excluded.attributes
"""


class State:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(_SCHEMA)
        if self.get("run_id") is None:
            self.set("run_id", "1")

    def close(self):
        self.db.close()

    # ---- meta -------------------------------------------------------------
    def get(self, key: str) -> Optional[str]:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: str):
        self.db.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    @property
    def run_id(self) -> int:
        return int(self.get("run_id"))

    def start_new_run(self):
        """Full re-scan: list every folder again; unchanged files keep results."""
        with self.tx():
            self.set("run_id", str(self.run_id + 1))
            self.db.execute("UPDATE dirs SET status='pending', attempts=0, error=NULL "
                            "WHERE status != 'skipped'")
            self.db.execute("DELETE FROM meta WHERE key NOT IN ('run_id')")

    def tx(self):
        return _Tx(self.db)

    # ---- folders ----------------------------------------------------------
    def seed_roots(self, roots: dict):
        with self.tx():
            for source, root in roots.items():
                self.db.execute("INSERT OR IGNORE INTO dirs(path, source) VALUES(?, ?)", (root, source))

    def pending_dirs(self, limit: int, exclude: Iterable[str] = ()):
        excl = set(exclude)
        rows = self.db.execute("SELECT path, source FROM dirs WHERE status='pending' LIMIT ?",
                               (limit + len(excl),)).fetchall()
        return [r for r in rows if r["path"] not in excl][:limit]

    def save_listing(self, dir_path: str, source: str, entries: list,
                     is_excluded_dir, is_excluded_file):
        """Save one folder's children and mark it listed, in one transaction."""
        run = self.run_id
        with self.tx():
            for e in entries:
                if e.is_dir or (e.is_link and _is_dir_link(e)):
                    status, error = "pending", None
                    if e.is_link:
                        status, error = "skipped", "link not followed"
                    elif is_excluded_dir(e.name):
                        status, error = "skipped", "excluded by config"
                    self.db.execute(
                        "INSERT INTO dirs(path, source, status, error) VALUES(?, ?, ?, ?) "
                        "ON CONFLICT(path) DO NOTHING", (e.path, source, status, error))
                    continue
                status, error = "pending", None
                if e.is_link:
                    status, error = "skipped", "link not followed"
                elif is_excluded_file(e.name):
                    status, error = "skipped", "excluded by config"
                self.db.execute(_UPSERT_FILE, _file_params(e, source, dir_path, status, error, run))
            self.db.execute("UPDATE dirs SET status='listed', error=NULL WHERE path=?", (dir_path,))

    def dir_failed(self, path: str, error: str, max_attempts: int):
        self.db.execute(
            "UPDATE dirs SET attempts=attempts+1, error=?, "
            "status=CASE WHEN attempts+1 >= ? THEN 'error' ELSE 'pending' END WHERE path=?",
            (error, max_attempts, path))

    # ---- files ------------------------------------------------------------
    def reset_retryable_errors(self, max_attempts: int):
        with self.tx():
            for table in ("files", "dirs", "content"):
                self.db.execute(f"UPDATE {table} SET status='pending' WHERE status='error' AND attempts < ?",
                                (max_attempts,))

    # ---- content (stage 5) ------------------------------------------------
    def queue_content(self, readable_types, text_groups) -> int:
        """Queue every scanned, readable file; re-queue files whose content changed."""
        t = ",".join("?" * len(readable_types))
        g = ",".join("?" * len(text_groups))
        with self.tx():
            cur = self.db.execute(
                f"INSERT INTO content(path, source_processed) "
                f"SELECT path, processed FROM files WHERE status='done' AND seen_run=? "
                f"AND (detected_type IN ({t}) OR type_group IN ({g})) "
                f"ON CONFLICT(path) DO UPDATE SET status='pending', attempts=0, error=NULL, "
                f"source_processed=excluded.source_processed "
                f"WHERE content.source_processed IS NOT excluded.source_processed",
                (self.run_id, *readable_types, *text_groups))
        return cur.rowcount

    _CONTENT_ROW = ("SELECT c.rowid, c.path, f.source, f.name, f.ext, f.size, f.detected_type, f.type_group "
                    "FROM content c JOIN files f ON f.path = c.path ")

    def pending_content(self, after_rowid: int, limit: int):
        return self.db.execute(self._CONTENT_ROW + "WHERE c.status='pending' AND c.rowid > ? "
                               "ORDER BY c.rowid LIMIT ?", (after_rowid, limit)).fetchall()

    def sample_content(self, n: int):
        return self.db.execute(self._CONTENT_ROW + "WHERE c.status='pending' ORDER BY random() LIMIT ?",
                               (n,)).fetchall()

    def save_content_results(self, results: list):
        now = time.time()
        with self.tx():
            for r in results:
                if r["status"] != "done":
                    self.db.execute("UPDATE content SET status='error', attempts=attempts+1, error=?, "
                                    "processed=? WHERE path=?", (r["error"], now, r["path"]))
                    continue
                self.db.execute("DELETE FROM findings WHERE path=?", (r["path"],))
                self.db.executemany(
                    "INSERT INTO findings(path, inner_path, data_type, masked_value, confidence, "
                    "occurrences, first_location) VALUES(?, ?, ?, ?, ?, ?, ?)",
                    [(r["path"], x["inner_path"], x["data_type"], x["masked_value"], x["confidence"],
                      x["occurrences"], x["first_location"]) for x in r["findings"]])
                self.db.execute(
                    "UPDATE content SET status='done', error=NULL, content_status=?, sensitivity=?, "
                    "pii_types=?, pii_counts=?, has_credentials=?, topics=?, topic_scores=?, text_chars=?, "
                    "pages=?, ocr_pages=?, note=?, processed=? WHERE path=?",
                    (r["content_status"], r["sensitivity"], r["pii_types"], r["pii_counts"],
                     int(r["has_credentials"]), r["topics"], r["topic_scores"], r["text_chars"],
                     r["pages"], r["ocr_pages"], r["note"], now, r["path"]))

    def requeue_all_content(self) -> int:
        """After rules change: re-read every file's content. Findings are replaced as files finish."""
        with self.tx():
            return self.db.execute("UPDATE content SET status='pending', attempts=0, error=NULL").rowcount

    def content_counts(self) -> dict:
        out = {"pending": 0, "done": 0, "error": 0, "bytes_pending": 0, "bytes_done": 0, "bytes_error": 0}
        for r in self.db.execute("SELECT c.status, COUNT(*) n, COALESCE(SUM(f.size),0) b FROM content c "
                                 "JOIN files f ON f.path=c.path GROUP BY c.status"):
            out[r["status"]] = r["n"]
            out[f"bytes_{r['status']}"] = r["b"]
        return out

    def pending_files(self, after_rowid: int, limit: int):
        return self.db.execute(
            "SELECT rowid, path, source, ext, size FROM files "
            "WHERE status='pending' AND rowid > ? ORDER BY rowid LIMIT ?",
            (after_rowid, limit)).fetchall()

    def sample_pending_files(self, n: int):
        return self.db.execute(
            "SELECT rowid, path, source, ext, size FROM files WHERE status='pending' "
            "ORDER BY random() LIMIT ?", (n,)).fetchall()

    def save_file_results(self, results: list):
        now = time.time()
        with self.tx():
            for r in results:
                if r["status"] == "done":
                    self.db.execute(
                        "UPDATE files SET status='done', error=NULL, detected_type=?, type_group=?, "
                        "mime=?, ext_mismatch=?, quick_fp=?, fp_is_full=?, sha256=?, processed=? "
                        "WHERE path=?",
                        (r["detected_type"], r["type_group"], r["mime"], int(r["ext_mismatch"]),
                         r["quick_fp"], int(r["fp_is_full"]), r["sha256"], now, r["path"]))
                else:
                    self.db.execute(
                        "UPDATE files SET status='error', attempts=attempts+1, error=?, processed=? "
                        "WHERE path=?", (r["error"], now, r["path"]))

    def duplicate_candidates(self, after_rowid: int, limit: int):
        """Files whose quick fingerprint matches another's but aren't fully hashed yet."""
        return self.db.execute(
            "SELECT rowid, path, source, size FROM files "
            "WHERE status='done' AND sha256 IS NULL AND rowid > ? AND quick_fp IN ("
            "  SELECT quick_fp FROM files WHERE status='done' AND quick_fp IS NOT NULL "
            "  GROUP BY quick_fp HAVING COUNT(*) > 1) "
            "ORDER BY rowid LIMIT ?", (after_rowid, limit)).fetchall()

    def duplicate_candidate_totals(self):
        row = self.db.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(size), 0) b FROM files "
            "WHERE status='done' AND sha256 IS NULL AND quick_fp IN ("
            "  SELECT quick_fp FROM files WHERE status='done' AND quick_fp IS NOT NULL "
            "  GROUP BY quick_fp HAVING COUNT(*) > 1)").fetchone()
        return row["n"], row["b"]

    def save_hashes(self, results: list):
        with self.tx():
            for r in results:
                if r.get("sha256"):
                    self.db.execute("UPDATE files SET sha256=? WHERE path=?", (r["sha256"], r["path"]))
                else:
                    self.db.execute("UPDATE files SET error=? WHERE path=?", (r["error"], r["path"]))

    # ---- totals -----------------------------------------------------------
    def counts(self) -> dict:
        out = {"dirs_pending": 0, "dirs_listed": 0, "dirs_error": 0, "dirs_skipped": 0}
        for r in self.db.execute("SELECT status, COUNT(*) n FROM dirs GROUP BY status"):
            out[f"dirs_{r['status']}"] = r["n"]
        for s in ("pending", "done", "error", "skipped"):
            out[f"files_{s}"] = 0
            out[f"bytes_{s}"] = 0
        for r in self.db.execute("SELECT status, COUNT(*) n, COALESCE(SUM(size),0) b "
                                 "FROM files GROUP BY status"):
            out[f"files_{r['status']}"] = r["n"]
            out[f"bytes_{r['status']}"] = r["b"]
        return out


def _is_dir_link(e: Entry) -> bool:
    # A junction/symlink to a folder: FILE_ATTRIBUTE_DIRECTORY (0x10) is set.
    return bool(e.attributes & 0x10)


def _file_params(e: Entry, source, dir_path, status, error, run):
    return {
        "path": e.path, "source": source, "dir": dir_path, "name": e.name,
        "ext": os.path.splitext(e.name)[1].lower(), "size": e.size,
        "created": e.created, "modified": e.modified, "accessed": e.accessed,
        "attributes": e.attributes, "status": status, "error": error, "run": run,
    }


class _Tx:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN")
        return self.db

    def __exit__(self, exc_type, *_):
        self.db.execute("ROLLBACK" if exc_type else "COMMIT")
