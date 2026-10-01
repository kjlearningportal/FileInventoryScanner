"""Command line entry point.

    python -m fscan scan   -c config.yaml [--yes] [--estimate-only] [--rescan]
    python -m fscan status -c config.yaml
    python -m fscan export -c config.yaml
    python -m fscan report -c config.yaml

Running `scan` again after a crash or Ctrl+C resumes where it stopped.
"""
import argparse
import json
import logging
import os
import signal
import sys
import threading

from .config import ConfigError, load_config
from .export import export
from .progress import fmt_bytes, fmt_duration
from .sources.filesystem import FileSystemSource
from .state import State


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fscan", description="Read-only file inventory scanner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, text in [("scan", "scan (or resume) the configured sources"),
                       ("status", "show progress of a running or stopped scan"),
                       ("export", "write CSV/Parquet from what has been scanned so far"),
                       ("rescan-content", "re-read file content with the current rules (after editing rules/*.yaml)"),
                       ("report", "write the staleness / duplicates / risk Excel report (reads saved results only)")]:
        p = sub.add_parser(name, help=text)
        p.add_argument("-c", "--config", default="config.yaml")
        if name in ("scan", "rescan-content"):
            p.add_argument("--yes", action="store_true", help="skip the confirmation after the estimate")
        if name == "scan":
            p.add_argument("--estimate-only", action="store_true", help="stop after showing the time estimate")
            p.add_argument("--rescan", action="store_true",
                           help="start a new full pass; unchanged files keep their results")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (OSError, ConfigError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    if args.cmd == "status":
        return _status(cfg)

    _setup_logging(cfg.log_file)
    state = State(cfg.state_db)
    try:
        if args.cmd == "export":
            r = export(state, cfg.output_dir)
            print(f"Exported {r['rows']:,} rows to {r['csv']} and {r['findings']:,} findings to {r['findings_csv']}")
            return 0
        if args.cmd == "report":
            from .report import build_report
            path = build_report(state, cfg)
            print(f"Report written to {path}")
            return 0
        if args.cmd == "rescan-content":
            n = state.requeue_all_content()
            print(f"{n:,} files queued to be re-read with the current rules.")
            args.rescan, args.estimate_only = False, False
        return _scan(cfg, state, args)
    finally:
        state.close()


def _scan(cfg, state, args) -> int:
    from .engine import Scanner

    sources = {s.name: FileSystemSource(s.name, s.root) for s in cfg.sources}
    for s in sources.values():
        if not os.path.isdir(s.root):
            print(f"Source '{s.name}' is not reachable: {s.root}", file=sys.stderr)
            return 2
    if args.rescan:
        state.start_new_run()

    stop = threading.Event()

    def on_sigint(signum, frame):
        if stop.is_set():
            raise KeyboardInterrupt  # second Ctrl+C: force quit (last batch may be redone)
        stop.set()
        sys.stderr.write("\nStopping after the current batch... (press Ctrl+C again to force quit)\n")

    signal.signal(signal.SIGINT, on_sigint)
    result = Scanner(cfg, state, sources, stop_event=stop).run(
        assume_yes=args.yes, estimate_only=args.estimate_only)
    return 0 if result in ("complete", "estimated", "declined") else 1


def _status(cfg) -> int:
    if not os.path.exists(cfg.progress_json):
        print("No scan has been started for this config yet.")
        return 0
    with open(cfg.progress_json, encoding="utf-8") as f:
        p = json.load(f)
    print(f"Stage        : {p['stage']}   (updated {p['updated']})")
    print(f"Progress     : {p['line']}")
    if p.get("eta_seconds") is not None:
        print(f"Remaining    : ~{fmt_duration(p['eta_seconds'])}")
    if os.path.exists(cfg.state_db):
        state = State(cfg.state_db)
        c = state.counts()
        cc = state.content_counts()
        state.close()
        print(f"Folders      : {c['dirs_listed']:,} listed, {c['dirs_pending']:,} queued, {c['dirs_error']:,} errors")
        print(f"Files        : {c['files_done']:,} done, {c['files_pending']:,} pending, "
              f"{c['files_error']:,} errors, {c['files_skipped']:,} skipped")
        print(f"Data done    : {fmt_bytes(c['bytes_done'])} of "
              f"{fmt_bytes(c['bytes_done'] + c['bytes_pending'] + c['bytes_error'])}")
        if cc["pending"] + cc["done"] + cc["error"]:
            print(f"Content read : {cc['done']:,} done, {cc['pending']:,} pending, {cc['error']:,} errors "
                  "(sensitive data + topics)")
    return 0


def _setup_logging(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    logging.basicConfig(filename=path, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
