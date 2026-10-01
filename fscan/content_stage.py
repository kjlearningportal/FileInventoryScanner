"""Stage 5 worker: read a file's text, find sensitive data, score topics.

Runs in separate processes (text scanning is CPU-bound). Each process loads the
rules once in worker_init. Workers only read; results go back to the main process,
which saves them, so the same resume rules apply as in the other stages.
"""
import os
import multiprocessing
import signal
import threading

from .classify.sensitivity import SensitivityRules
from .classify.topics import TopicRules, TopicScorer
from .extract import Context, Interrupted, Unreadable, extract, throttled
from .extract.ocr import Ocr
from .pii.aggregate import Aggregator
from .pii.patterns import scan_text
from .sources.filesystem import FileSystemSource
from .throttle import Throttle

_W = {}


def worker_payload(cfg, tesseract_cmd):
    c = cfg.content
    return {
        "sources": [(s.name, s.root) for s in cfg.sources],
        "rate": cfg.max_read_mb_per_sec * 1024 * 1024 / max(1, c.workers),
        "tesseract": tesseract_cmd, "lang": c.ocr_lang, "ocr_timeout": c.ocr_timeout,
        "max_depth": c.max_depth,
        "sensitivity": os.path.join(c.rules_dir, "sensitivity.yaml"),
        "topics": os.path.join(c.rules_dir, "topics.yaml"),
    }


def _exit_when_parent_dies(interval: float = 2.0):
    """Windows doesn't end child processes when the parent is killed (crash, Task Manager,
    closed window). Without this, workers would linger as orphans."""
    parent = multiprocessing.parent_process()
    while parent is not None:
        parent.join(interval)
        if not parent.is_alive():
            os._exit(0)


def worker_init(payload, stop):
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the main process handles Ctrl+C
    threading.Thread(target=_exit_when_parent_dies, daemon=True, name="parent-watch").start()
    _W.clear()
    _W.update(
        sources={n: FileSystemSource(n, r) for n, r in payload["sources"]},
        throttle=Throttle(payload["rate"]),
        ocr=Ocr(payload["tesseract"], payload["lang"], payload["ocr_timeout"]) if payload["tesseract"] else None,
        max_depth=payload["max_depth"], stop=stop,
        sens=SensitivityRules.load(payload["sensitivity"]),
        topics=TopicRules.load(payload["topics"]),
    )


def content_job(row: dict):
    ctx = Context(ocr=_W["ocr"], stop=_W["stop"], max_depth=_W["max_depth"])
    agg = Aggregator()
    topics = TopicScorer(_W["topics"], row["path"])
    chars, status, bytes_read = 0, "ok", 0
    try:
        with _W["sources"][row["source"]].open_read(row["path"]) as raw:
            f, counter = throttled(raw, _W["throttle"])
            try:
                for chunk in extract(f, row["name"], ctx, detected_type=row["detected_type"],
                                     type_group=row["type_group"]):
                    ctx.check_stop()
                    chars += len(chunk.text)
                    topics.add_text(chunk.text)
                    for hit in scan_text(chunk.text):
                        agg.add(chunk.inner_path, hit, chunk.locate(hit.start))
            finally:
                bytes_read = counter.bytes_read
    except Interrupted:
        return None  # left pending; redone on resume
    except Unreadable as e:
        ctx.note(e.note)
        status = "unreadable" if chars == 0 else "partial"
    except OSError as e:  # network / permission problem: retried on the next run
        return {"path": row["path"], "status": "error", "bytes_read": 0,
                "error": f"{type(e).__name__}: {e.strerror or ''}"[:300]}
    except Exception as e:  # library choked on the file: record the kind of error only,
        ctx.note(f"corrupt: {type(e).__name__}")  # never its message (may quote file content)
        status = "unreadable" if chars == 0 else "partial"
    if status == "ok" and ctx.partial:
        status = "partial"
    if agg.truncated:
        ctx.note("findings_truncated", partial=False)

    counts = agg.counts()
    topic_list, scores = topics.result()
    return {
        "path": row["path"], "status": "done", "bytes_read": bytes_read,
        "content_status": status,
        "sensitivity": _W["sens"].level(counts, unreadable=(status == "unreadable")),
        "pii_types": ";".join(sorted(counts, key=lambda t: -counts[t])),
        "pii_counts": ";".join(f"{t}={n}" for t, n in sorted(counts.items(), key=lambda kv: -kv[1])),
        "has_credentials": agg.has_credentials(),
        "topics": ";".join(topic_list),
        "topic_scores": ";".join(f"{t}={s:g}" for t, s in sorted(scores.items(), key=lambda kv: -kv[1])),
        "text_chars": chars, "pages": ctx.pages, "ocr_pages": ctx.ocr_pages,
        "note": ";".join(sorted(ctx.notes)),
        "findings": agg.findings(),
    }
