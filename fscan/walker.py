"""Folder listing (the count pass), resumable from the queue in the state DB.

Folders are listed in parallel threads (listing is network-latency bound);
only the main thread writes to the database. Each folder's children are saved
in the same transaction that marks it listed, so a crash never half-lists one.
"""
import fnmatch
import logging
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable, Optional

log = logging.getLogger(__name__)


class Walker:
    def __init__(self, state, sources: dict, exclude_dirs=(), exclude_files=(),
                 workers: int = 8, max_attempts: int = 3,
                 stop_event: Optional[threading.Event] = None, progress=None):
        self.state = state
        self.sources = sources
        self.exclude_dirs = [p.lower() for p in exclude_dirs]
        self.exclude_files = [p.lower() for p in exclude_files]
        self.workers = workers
        self.max_attempts = max_attempts
        self.stop = stop_event or threading.Event()
        self.progress = progress
        self.on_dir_listed: Callable[[], None] = lambda: None

    def _excluded_dir(self, name: str) -> bool:
        return any(fnmatch.fnmatch(name.lower(), p) for p in self.exclude_dirs)

    def _excluded_file(self, name: str) -> bool:
        return any(fnmatch.fnmatch(name.lower(), p) for p in self.exclude_files)

    def _list(self, path: str, source: str):
        return list(self.sources[source].list_dir(path))

    def run(self):
        self.state.seed_roots({name: s.root for name, s in self.sources.items()})
        inflight = {}
        with ThreadPoolExecutor(self.workers, thread_name_prefix="list") as pool:
            while not self.stop.is_set():
                room = self.workers * 2 - len(inflight)
                if room > 0:
                    busy = {p for p, _ in inflight.values()}
                    for row in self.state.pending_dirs(room, exclude=busy):
                        fut = pool.submit(self._list, row["path"], row["source"])
                        inflight[fut] = (row["path"], row["source"])
                if not inflight:
                    break  # queue empty and nothing running: listing complete
                done, _ = wait(inflight, timeout=1.0, return_when=FIRST_COMPLETED)
                for fut in done:
                    path, source = inflight.pop(fut)
                    self._save(fut, path, source)
                    if self.stop.is_set():
                        break
                if self.progress:
                    self.progress.tick()
            # Stopping: drop queued listings; running ones finish but aren't saved,
            # so those folders stay 'pending' and are listed again on resume.
            for fut in inflight:
                fut.cancel()

    def _save(self, fut, path, source):
        try:
            entries = fut.result()
        except OSError as e:
            log.warning("cannot list %s: %s", path, e)
            self.state.dir_failed(path, f"{type(e).__name__}: {e}", self.max_attempts)
            if self.progress:
                self.progress.add(errors=1)
            return
        self.state.save_listing(path, source, entries, self._excluded_dir, self._excluded_file)
        if self.progress:
            files = [e for e in entries if not e.is_dir]
            self.progress.add(dirs=1, files=len(files), bytes=sum(e.size for e in files))
        self.on_dir_listed()
