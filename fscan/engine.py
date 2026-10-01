"""Runs the scan stages in order. Every stage is resumable from the state DB.

  1. listing     walk folders (count pass), no file is opened
  2. estimate    process a random sample, project total time, ask to proceed
  3. process     per file: real type + quick fingerprint
  4. duplicates  full SHA-256 only for files whose quick fingerprint collides
  5. content     read text: sensitive data (masked findings) + business topics
     export      CSV / Parquet / findings / errors / summary
"""
import logging
import multiprocessing
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool

from .content_stage import content_job, worker_init, worker_payload
from .export import export
from .extract import READABLE_TYPES, TEXT_GROUPS, pst_available
from .extract.ocr import find_tesseract
from .progress import Progress, fmt_bytes, fmt_duration
from .throttle import Schedule, Throttle
from .walker import Walker
from .worker import full_hash, process_file

log = logging.getLogger(__name__)


class Scanner:
    def __init__(self, cfg, state, sources: dict, stop_event=None, stream=None, confirm=None):
        self.cfg = cfg
        self.state = state
        self.sources = sources
        self.stop = stop_event or threading.Event()
        self.stream = stream or sys.stderr
        self.confirm = confirm or _ask_yes_no
        self.throttle = Throttle(cfg.max_read_mb_per_sec * 1024 * 1024)
        self.schedule = Schedule(cfg.scan_hours)
        self.after_batch = lambda: None  # test hook
        self.tesseract = find_tesseract(cfg.content.tesseract_cmd) if cfg.content.ocr else None
        self._mp_stop = None
        self.stage = None

    def say(self, msg: str):
        self.stream.write(msg + "\n")
        self.stream.flush()
        log.info(msg)

    # ---------------------------------------------------------------- stages
    def run(self, assume_yes: bool = False, estimate_only: bool = False) -> str:
        self.state.reset_retryable_errors(self.cfg.max_attempts)

        if not self._listing():
            return self._stopped()

        if self.state.get("estimate_confirmed") != "1":
            answer = self._estimate(assume_yes, estimate_only)
            if answer != "go":
                return answer
            self.state.set("estimate_confirmed", "1")

        if not self._process():
            return self._stopped()
        if not self._duplicates():
            return self._stopped()
        if not self._content():
            return self._stopped()

        result = export(self.state, self.cfg.output_dir)
        self._write_final_status("complete")
        self.say(f"Scan complete. {result['rows']:,} files exported to {result['csv']} and {result['parquet']}")
        return "complete"

    def _stopped(self) -> str:
        self._write_final_status("stopped")
        self.say("Stopped cleanly. Progress is saved; run the same command again to resume.")
        return "stopped"

    def _listing(self) -> bool:
        self.stage = "listing"
        c = self.state.counts()
        if self.state.get("listing_done") == "1" and c["dirs_pending"] == 0:
            return True
        self.say("Stage 1/5 - listing folders (count pass; no files are opened)")
        p = self._progress("listing")
        p.dirs = c["dirs_listed"]
        p.files = sum(c[f"files_{s}"] for s in ("pending", "done", "error", "skipped"))
        p.bytes = sum(c[f"bytes_{s}"] for s in ("pending", "done", "error", "skipped"))
        started = time.monotonic()
        Walker(self.state, self.sources, self.cfg.exclude_dirs, self.cfg.exclude_files,
               workers=self.cfg.list_workers, max_attempts=self.cfg.max_attempts,
               stop_event=self.stop, progress=p).run()
        p.close()
        if self.stop.is_set():
            return False
        took = float(self.state.get("listing_seconds") or 0) + time.monotonic() - started
        self.state.set("listing_seconds", str(took))
        self.state.set("listing_done", "1")
        return True

    def _estimate(self, assume_yes: bool, estimate_only: bool) -> str:
        self.stage = "estimate"
        c = self.state.counts()
        self.say("Stage 2/5 - speed test on a random sample")
        sample = self.state.sample_pending_files(self.cfg.sample_size)
        p = self._progress("speed test", total_files=len(sample), total_bytes=sum(r["size"] for r in sample))
        started = time.monotonic()
        stats = self._run_pool(sample, self._process_job, self.state.save_file_results, p)
        elapsed = time.monotonic() - started
        p.close()
        if self.stop.is_set():
            return self._stopped()

        remaining = self.state.counts()["files_pending"]
        per_file = elapsed / max(1, len(sample))
        main_est = remaining * per_file
        cand_n, cand_bytes = self._same_size_large_files()
        byte_rate = (self.cfg.max_read_mb_per_sec * 1024 * 1024) or (stats["bytes_read"] / max(elapsed, 1e-6))
        dup_est = cand_bytes / byte_rate if cand_bytes and byte_rate else 0

        content_lines, content_est = self._content_estimate(remaining)
        if self.stop.is_set():
            return self._stopped()

        total_files = sum(c[f"files_{s}"] for s in ("pending", "done", "error", "skipped"))
        total_bytes = sum(c[f"bytes_{s}"] for s in ("pending", "done", "error", "skipped"))
        total_est = main_est + dup_est + content_est
        lines = [
            "",
            "================ TIME ESTIMATE ================",
            f"Inventory     : {total_files:,} files, {fmt_bytes(total_bytes)} in {c['dirs_listed']:,} folders "
            f"(listing took {fmt_duration(float(self.state.get('listing_seconds') or 0))})",
            f"Skipped       : {c['files_skipped']:,} files (excluded or links) | folder errors: {c['dirs_error']:,}",
            f"Speed test    : {len(sample):,} files in {elapsed:.1f}s = {len(sample) / max(elapsed, 1e-6):,.1f} files/s "
            f"with {self.cfg.workers} workers",
            f"File types    : ~{fmt_duration(main_est)} for {remaining:,} files "
            f"(likely range {fmt_duration(main_est * 0.8)} - {fmt_duration(main_est * 1.5)})",
            f"Duplicates    : up to ~{fmt_duration(dup_est)} extra to fully hash {cand_n:,} same-size large files "
            f"({fmt_bytes(cand_bytes)}, worst case)",
            *content_lines,
            f"Read cap      : {str(self.cfg.max_read_mb_per_sec) + ' MB/s' if self.cfg.max_read_mb_per_sec else 'none'}"
            f" | scan hours: {self.cfg.scan_hours or 'any time'}",
            "-----------------------------------------------",
            f"TOTAL         : ~{fmt_duration(total_est)} "
            f"(likely range {fmt_duration(total_est * 0.8)} - {fmt_duration(total_est * 1.5)})",
            "===============================================",
        ]
        self.say("\n".join(lines))
        self.state.set("estimate_seconds", str(total_est))
        if estimate_only:
            return "estimated"
        if not assume_yes and not self.confirm("Proceed with the full scan? [y/N] "):
            self.say("Not started. Run the command again whenever you are ready; nothing is lost.")
            return "declined"
        return "go"

    def _process(self) -> bool:
        self.stage = "types"
        c = self.state.counts()
        total = c["files_pending"] + c["files_done"] + c["files_error"]
        if c["files_pending"] == 0:
            return True
        self.say("Stage 3/5 - identifying file types and fingerprints")
        p = self._progress("content", total_files=total,
                           total_bytes=c["bytes_pending"] + c["bytes_done"] + c["bytes_error"],
                           done_files=c["files_done"] + c["files_error"],
                           done_bytes=c["bytes_done"] + c["bytes_error"], errors=c["files_error"])
        self._run_pool(self._pages(self.state.pending_files), self._process_job,
                       self.state.save_file_results, p)
        p.close()
        return not self.stop.is_set()

    def _duplicates(self) -> bool:
        self.stage = "duplicates"
        n, b = self.state.duplicate_candidate_totals()
        if n == 0:
            return True
        self.say(f"Stage 4/5 - confirming duplicates: full hash of {n:,} files ({fmt_bytes(b)})")
        p = self._progress("duplicates", total_files=n, total_bytes=b)
        self._run_pool(self._pages(self.state.duplicate_candidates), self._hash_job,
                       self.state.save_hashes, p)
        p.close()
        return not self.stop.is_set()

    def _content(self) -> bool:
        self.stage = "content"
        if not self.cfg.content.enabled:
            return True
        self.state.queue_content(sorted(READABLE_TYPES), sorted(TEXT_GROUPS))
        cc = self.state.content_counts()
        if cc["pending"] == 0:
            return True
        total = cc["pending"] + cc["done"] + cc["error"]
        self.say(f"Stage 5/5 - reading file content: sensitive data and topics ({cc['pending']:,} files to read)")
        if self.cfg.content.ocr and not self.tesseract:
            self.say("  Note: Tesseract not found - scanned pages and images will be flagged 'needs_ocr'")
        p = self._progress("sensitive data", total_files=total,
                           total_bytes=cc["bytes_pending"] + cc["bytes_done"] + cc["bytes_error"],
                           done_files=cc["done"] + cc["error"], done_bytes=cc["bytes_done"] + cc["bytes_error"],
                           errors=cc["error"])
        self._run_pool(self._pages(self.state.pending_content), content_job, self.state.save_content_results, p,
                       make_executor=self._content_executor, workers=self.cfg.content.workers)
        p.close()
        return not self.stop.is_set()

    def _content_estimate(self, files_pending: int):
        """Speed-test the content stage on a sample; returns (estimate lines, seconds)."""
        cfg = self.cfg.content
        if not cfg.enabled:
            return ["Sensitive data: disabled in config"], 0
        self.state.queue_content(sorted(READABLE_TYPES), sorted(TEXT_GROUPS))
        c, cc = self.state.counts(), self.state.content_counts()
        readable_ratio = (cc["pending"] + cc["done"] + cc["error"]) / max(1, c["files_done"])
        sample = self.state.sample_content(cfg.sample_size)
        lines = []
        est = 0.0
        if sample:
            self.say(f"Stage 2/5 - content speed test on {len(sample):,} readable files")
            p = self._progress("content test", total_files=len(sample), total_bytes=sum(r["size"] for r in sample))
            started = time.monotonic()
            stats = self._run_pool(sample, content_job, self.state.save_content_results, p,
                                   make_executor=self._content_executor, workers=cfg.workers)
            elapsed = time.monotonic() - started
            p.close()
            done = max(1, stats["files"])
            to_read = readable_ratio * files_pending + self.state.content_counts()["pending"]
            est = to_read * elapsed / done
            ocr_expected = stats["ocr_pages"] / done * (to_read + done)
            lines.append(f"Sensitive data: ~{fmt_duration(est)} to read ~{to_read:,.0f} files "
                         f"(sample: {done:,} files in {elapsed:.1f}s with {cfg.workers} processes)")
            ocr_state = ("Tesseract ready" if self.tesseract else
                         "Tesseract NOT found: these will be flagged 'needs_ocr'") if cfg.ocr else "OCR off"
            lines.append(f"  OCR         : ~{ocr_expected:,.0f} scanned pages/images expected ({ocr_state})")
        else:
            lines.append("Sensitive data: no readable files in the sample")
        row = self.state.db.execute("SELECT COUNT(*) n, COALESCE(SUM(size),0) b FROM files "
                                    "WHERE ext IN ('.pst','.ost') AND status != 'skipped'").fetchone()
        if row["n"]:
            how = ("included; large mailboxes can take an hour or more each" if pst_available()
                   else "reader NOT installed: flagged 'pst_reader_unavailable', not read")
            lines.append(f"  Mailboxes   : {row['n']:,} PST/OST files ({fmt_bytes(row['b'])}) - {how}")
        return lines, est

    def _content_executor(self):
        ctx = multiprocessing.get_context("spawn")
        self._mp_stop = ctx.Event()
        payload = worker_payload(self.cfg, self.tesseract)
        return ProcessPoolExecutor(self.cfg.content.workers, mp_context=ctx,
                                   initializer=worker_init, initargs=(payload, self._mp_stop))

    # --------------------------------------------------------------- helpers
    def _process_job(self, row):
        return process_file(self.sources[row["source"]], row, self.cfg.chunk_bytes, self.throttle)

    def _hash_job(self, row):
        try:
            sha = full_hash(self.sources[row["source"]], row["path"], self.stop, self.throttle)
            if sha is None:
                return None  # interrupted part-way: stays a candidate, redone on resume
            return {"path": row["path"], "status": "done", "sha256": sha, "bytes_read": row["size"]}
        except Exception as e:
            return {"path": row["path"], "status": "error", "bytes_read": 0,
                    "error": f"full hash failed: {type(e).__name__}: {e}"}

    @staticmethod
    def _pages(fetch, size: int = 2000):
        """Iterate rows page by page, by rowid, so saved rows are never re-fetched."""
        last = 0
        while True:
            rows = fetch(last, size)
            if not rows:
                return
            yield from rows
            last = rows[-1]["rowid"]

    def _run_pool(self, rows, job, save, progress, make_executor=None, workers=None) -> dict:
        """Run `job` over `rows` in a pool, saving results in batches. Handles stop,
        scan hours, and (for process pools) a worker crashing on a malformed file."""
        workers = workers or self.cfg.workers
        make_executor = make_executor or (lambda: ThreadPoolExecutor(workers, thread_name_prefix="scan"))
        stats = {"bytes_read": 0, "files": 0, "ocr_pages": 0}
        buffer, inflight = [], {}
        last_flush = time.monotonic()
        it, exhausted = iter(rows), False
        crashed = []

        def collect(fut, row):
            try:
                r = fut.result()
            except BrokenProcessPool:
                crashed.append(row["path"])
                r = {"path": row["path"], "status": "error", "bytes_read": 0,
                     "error": "worker process crashed while this file (or one read alongside it) was open"}
            if r is None:
                return
            buffer.append(r)
            stats["bytes_read"] += r.get("bytes_read", 0)
            stats["files"] += 1
            stats["ocr_pages"] += r.get("ocr_pages", 0) or 0
            failed = r["status"] == "error"
            progress.add(files=1, bytes=row["size"], errors=int(failed))
            if failed:
                log.warning("%s: %s", r["path"], r["error"])

        def flush():
            nonlocal buffer, last_flush
            if buffer:
                save(buffer)
                buffer = []
                self.after_batch()
            last_flush = time.monotonic()

        pool = make_executor()
        try:
            while not self.stop.is_set():
                if not self.schedule.allowed():
                    flush()
                    progress.note = f"paused: outside scan hours {self.cfg.scan_hours}"
                    self.schedule.wait_until_allowed(self.stop, on_wait=lambda: progress.tick(force=True))
                    progress.note = ""
                    continue
                while not exhausted and len(inflight) < workers * 4:
                    row = next(it, None)
                    if row is None:
                        exhausted = True
                        break
                    row = dict(row)
                    inflight[pool.submit(job, row)] = row
                if not inflight:
                    break
                done, _ = wait(inflight, timeout=1.0, return_when=FIRST_COMPLETED)
                for fut in done:
                    collect(fut, inflight.pop(fut))
                if crashed:
                    pool, it = self._replace_broken_pool(pool, make_executor, inflight, collect, crashed, it)
                    exhausted = False
                if len(buffer) >= self.cfg.batch_size or time.monotonic() - last_flush >= self.cfg.batch_seconds:
                    flush()
                progress.tick()

            # Stop requested: tell workers, drop queued work, let running files finish, save.
            if self._mp_stop is not None:
                self._mp_stop.set()
            for fut in list(inflight):
                if fut.cancel():
                    inflight.pop(fut)
            for fut, row in inflight.items():
                collect(fut, row)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            self._mp_stop = None
        flush()
        return stats

    @staticmethod
    def _replace_broken_pool(pool, make_executor, inflight, collect, crashed, it):
        """A worker died (e.g. a PDF crashed the PDF library). Files that were running are
        marked as errors (retried next run); files not yet started go to a fresh pool."""
        for fut in [f for f in inflight if f.done()]:
            collect(fut, inflight.pop(fut))
        log.error("worker process crashed; %d file(s) marked for retry: %s", len(crashed), crashed[:5])
        crashed.clear()
        pool.shutdown(wait=False, cancel_futures=True)
        waiting = list(inflight.values())
        inflight.clear()
        return make_executor(), _chain(waiting, it)

    def _same_size_large_files(self):
        big = 2 * self.cfg.chunk_bytes
        row = self.state.db.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(size), 0) b FROM files "
            "WHERE status IN ('pending','done') AND size > ? AND size IN ("
            "  SELECT size FROM files WHERE status IN ('pending','done') AND size > ? "
            "  GROUP BY size HAVING COUNT(*) > 1)", (big, big)).fetchone()
        return row["n"], row["b"]

    def _progress(self, stage, **kw) -> Progress:
        return Progress(stage, json_path=self.cfg.progress_json, stream=self.stream, **kw)

    def _write_final_status(self, stage):
        c = self.state.counts()
        p = self._progress(stage, total_files=c["files_pending"] + c["files_done"] + c["files_error"],
                           done_files=c["files_done"] + c["files_error"], errors=c["files_error"])
        p.write_json()


def _ask_yes_no(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _chain(first, rest):
    yield from first
    yield from rest
