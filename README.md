# File Inventory Scanner (fscan)

Read-only Python tool that inventories every file on one or more servers and categorizes each
file by content, creation date and last modification date.

**Status:** All four phases built and tested: inventory, staleness/duplicates/risk report, content extraction, sensitive data & topics. PST reading waits on the Windows SDK (see open-questions Q9).
**Started:** 2026-09-25
**Owner:** Himanshu Kumar

## Quick start

```powershell
cd D:\FileInventoryScanner
copy config.example.yaml config.yaml      # then set the share path under 'sources'

.venv\Scripts\python -m fscan scan -c config.yaml --estimate-only   # list + time estimate only
.venv\Scripts\python -m fscan scan -c config.yaml                    # full scan (asks before starting)
```

| Command | What it does |
|---|---|
| `scan` | Runs, or **resumes**, the scan: list folders → speed test → time estimate → `Proceed? [y/N]` → content pass → duplicate confirmation → export |
| `scan --estimate-only` | Stops after showing the time estimate |
| `scan --yes` | Skips the confirmation question (for scheduled runs) |
| `scan --rescan` | New full pass later on; unchanged files keep their results |
| `status` | Shows progress and remaining time; works from a second window while a scan runs |
| `export` | Writes CSV/Parquet from whatever has been scanned so far |
| `rescan-content` | Re-reads file content with the current rules (after editing `rules/*.yaml`) |
| `report` | Excel report: staleness by folder, duplicates & wasted space, stale sensitive files, types, growth |

**If the scan stops** (Ctrl+C, crash, reboot, network drop), run the same `scan` command
again. It continues from where it stopped. Ctrl+C once = clean stop after the current batch;
twice = force quit.

**Outputs** (in `output_dir`): `inventory.csv`, `inventory.parquet`, `findings.csv`, `findings.parquet`, `errors.csv`,
`summary.json`, `progress.json`, `logs/run.log`, and `state.sqlite` (the checkpoint; keep it
until the scan is finished).

## Guardrails built in

- Files are only ever opened read-only. Nothing on the share is moved, renamed, changed or tagged.
- File content never leaves this PC; classification is rule-based.
- Sensitive values are always **masked** in findings; raw values are never written to any output, log or the state DB.
- The original last-accessed time is recorded before any file is opened.
- The scan refuses to start if the output folder is inside the share being scanned.
- Read-speed cap (`max_read_mb_per_sec`) and optional scan hours (`scan_hours`) protect the server.
- Symlinks and junctions are not followed. Unreadable files are logged and skipped, then retried on the next run.

## Documents

| File | What it holds |
|---|---|
| [docs/decisions.md](docs/decisions.md) | Every decision made so far, with date and reason |
| [docs/requirements.md](docs/requirements.md) | What the tool must do, plus the guardrails |
| [docs/design-phase1.md](docs/design-phase1.md) | Phase 1 design as built: components, columns, stages, resume, testing |
| [docs/open-questions.md](docs/open-questions.md) | Items still to decide |
| [docs/how-to-run.md](docs/how-to-run.md) | Step-by-step guide to running a scan |
| [docs/design-phase3-4.md](docs/design-phase3-4.md) | Content extraction, sensitivity & topics: design + as-built differences |
| [docs/plan-phase3-4.md](docs/plan-phase3-4.md) | Build plan for phases 3–4 (done) |
| [docs/design-phase2.md](docs/design-phase2.md) | Staleness, duplicates & risk report: design + as built |

## Development

```powershell
.venv\Scripts\python -m pytest -q
```

## Roadmap

1. ~~Phase 1: inventory crawler~~ (done)
2. ~~Phase 2: staleness, duplicates & risk report~~ (done)
3. ~~Phase 3: content extraction~~ (done; embedded Office/PDF dates still to add)
4. ~~Phase 4: rule-based sensitivity & business-topic classification~~ (done)
