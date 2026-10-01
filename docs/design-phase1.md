# Phase 1 Design: Inventory Crawler

Status per section:

| Section | Topic | Status |
|---|---|---|
| 1 | Components & Windows share details | Approved 2026-09-25 ("let's start implementing") |
| 2 | Inventory columns & state database layout | Decided during implementation (defaults, see below); **built** |
| 3 | How the scan runs, error handling, testing | Decided during implementation (defaults, see below); **built** |

**Implementation status (2026-09-25):** Phase 1 built and tested. 33 automated tests pass;
verified on real files, including two hard kills (process force-killed) followed by
successful resumes.

---

## Phase overview

| Phase | What it does | Depends on |
|---|---|---|
| 1. Inventory crawler | Walks each source and records path, size, owner, file system dates, real file type, fingerprint and access errors. Checkpointed and throttled. | Nothing |
| 2. Staleness & duplicates | Pure analysis of the Phase 1 output: age groups, stale or archive candidates, duplicates by hash. No further reads from the server. | Phase 1 |
| 3. Content extraction | Pulls text and embedded dates from documents, PDFs, spreadsheets and images, with size caps | Phase 1 |
| 4. Content classification | Sensitivity and business-topic rules on Phase 3 text | Phase 3 |

---

## Section 1: Components (approved 2026-09-25)

> As built, the connector is `sources/filesystem.py` (not `smb.py`): the same code
> handles UNC shares and local disks. Stage orchestration lives in `engine.py`;
> config loading and validation in `config.py`. Embedded-date lookup moved to
> Phase 3 (content extraction) to keep Phase 1 fast.

```
fscan/
  cli.py          scan | estimate | status | export | resume — the commands you run
  sources/
    base.py       Source interface: list_dir(path) → entries, open_read(path) → stream
    smb.py        Windows/UNC connector (first; Linux and cloud connectors added later)
  state.py        SQLite state database: folder queue, file manifest, statuses, batched saves
  walker.py       Folder listing, resumable from the queue in the state database
  worker.py       Per-file work: real file type from content, fingerprint, embedded-date lookup
  throttle.py     Worker thread limit + MB/s cap + optional working-hours pause
  progress.py     Live progress bar, time-remaining calculation, progress.json writer
  estimate.py     Count pass + speed test (~500 sampled files) → estimate, asks you to confirm
  export.py       State database → inventory.parquet / inventory.csv, errors.csv
config.yaml       Sources, exclusions, size caps, thread and throughput limits, working hours
```

Each file has one job. The rest of the pipeline only uses a source through `list_dir` and
`open_read`, so adding S3 or Linux later means writing one new file.

### Windows share details

- **Fast listing.** `os.scandir` returns size and dates as part of each folder listing: one network request per folder instead of one per file.
- **Creation date.** Windows shares report a real creation time. It is still reset when a file is copied, so Phase 3 compares it with the date stored inside the file.
- **Long paths.** Paths longer than 260 characters are handled with the `\\?\UNC\` prefix, so deep folders don't fail silently.
- **Last-accessed time.** A read-only account can't stop the server updating it when content is read. The script records the original value from the folder listing before opening any file. The count pass and the metadata-only work never open files.
- **Owner lookup** is a separate request per file and roughly doubles listing time. Off by default; can be turned on in `config.yaml`.

---

## Operational design (agreed 2026-09-25)

### Resume after a crash
- A local SQLite state database stores every folder and file found, each with a status: `pending`, `done`, `error` or `skipped`.
- Folders are processed from a queue kept in the database, so listing also resumes mid-way.
- Results are saved in batches (~500 files or every few seconds). A crash loses at most one batch, which is simply redone.
- On restart: files marked `done` are skipped. A file is reprocessed if its size or modified date changed since it was recorded. Failed files are retried a limited number of times, then left as `error` with the reason.
- Ctrl+C finishes the current batch, saves and exits.
- CSV/Parquet exports are produced from the state database at the end, or at any time for a partial view.

### Time estimate before starting
1. **Count pass:** list folders only, without opening files, to get the total file count and size (resumable, shows progress).
2. **Speed test:** process ~500 randomly chosen files to measure files/s and MB/s.
3. **Estimate:** e.g. *"4.2M files, 11.8 TB → estimated 9–14 hours at the current speed"*, then ask for confirmation.

### Progress and remaining time during the run
```
Files 1,204,331 / 4,200,000 (28.7%) | 3.1 TB / 11.8 TB | 212 files/s | Errors: 37 | Elapsed 2h41m | Remaining ~6h50m
```
- Remaining time is recalculated from recent speed, not the overall average. Remaining files and remaining bytes are counted separately.
- `progress.json` is refreshed every few seconds, so it can be checked from another terminal or fed into a dashboard.
- Logs: a run log, plus a separate error log listing each failed file and the reason.

### Duplicate detection
- Default: quick fingerprint (size + first/last 1 MB) for every file; full SHA-256 only when sizes match.

---

## Section 2: Inventory columns & state database (as built)

### State database (`output/state.sqlite`)
| Table | Purpose |
|---|---|
| `meta` | Run id, stage flags (`listing_done`, `estimate_confirmed`), listing time, estimate |
| `dirs` | Folder queue: `path, source, status (pending/listed/error/skipped), attempts, error` |
| `files` | One row per file: listing metadata + results + `status (pending/done/error/skipped)`, `attempts`, `error`, `seen_run` |

- SQLite in WAL mode, so `fscan status` can read while a scan is running.
- Only the main thread writes. Workers return results, which are saved in batches (500 files or 5 s).
- A folder's children are saved in the same transaction that marks the folder `listed`, so a folder is never half-listed.
- On re-listing (`--rescan`), a file whose size and modified time are unchanged keeps its results **and its original pre-scan access time**. A changed file is reset to `pending`.

### Exported inventory (`inventory.csv` / `inventory.parquet`)
| Column | Meaning |
|---|---|
| `source, path, folder, name, extension` | Where the file is |
| `size_bytes` | Size |
| `created_utc, modified_utc` | File system creation / last-modified time (UTC) |
| `accessed_utc_prescan` | Last-accessed time captured from the folder listing, before the scan opened the file |
| `is_hidden, is_system, is_readonly` | Windows attributes |
| `detected_type, type_group, mime` | Real type from the file's bytes (e.g. `docx` / `document`) |
| `ext_mismatch` | True when the extension disagrees with the real type (e.g. an .exe renamed to .pdf) |
| `quick_fp` | Quick fingerprint (size + first/last 1 MB) |
| `sha256` | Full content hash: always for files ≤ 2 MB; for larger files only when a quick-fingerprint collision needed confirming |
| `dup_count` | Number of files with identical content (1 = unique; empty files are never counted as duplicates) |
| `status, error` | `done` / `error` / `skipped` and the reason |

Other outputs: `errors.csv` (failed folders and files with reasons), `summary.json` (files and bytes per type group),
`progress.json` (live status), `logs/run.log`.

Type groups: document, spreadsheet, presentation, image, audio, video, archive, email, database,
data, config, log, code, web, executable, font, disk_image, text, empty, unknown.

---

## Section 3: How the scan runs, error handling, testing (as built)

### Stages (each resumable)
1. **Listing (count pass)**: parallel folder listing; no file is opened. Shows folders/files found, rate, elapsed. (Remaining time can't be known while listing, because the total isn't known yet.)
2. **Speed test + estimate**: processes a random sample (default 500), prints the estimate block, asks `Proceed? [y/N]`. Declining changes nothing; the sample's results are kept.
3. **Content**: real type + quick fingerprint for every pending file, with live progress and remaining time.
4. **Duplicates**: full SHA-256 only for files whose quick fingerprint collides. It checks for a stop request between 4 MB blocks, so Ctrl+C is quick even on huge files.
5. **Export**: writes the files above (via temp files, then an atomic rename).

### Resume rules
- Run the same `scan` command again. Listing continues from the folder queue; files already `done` are never re-read.
- Once the estimate has been confirmed, it isn't asked again on resume.
- Ctrl+C: finish the files in progress, save, exit. A second Ctrl+C forces quit (at most one batch is redone).
- A hard crash or kill loses at most the unsaved batch; those files are simply processed again.

### Error handling
- An unreadable file (access denied, locked, vanished) → `status=error` with the reason; retried on the next run up to `max_attempts` (3), then left as an error.
- An unlistable folder → same retry rule; listed in `errors.csv`.
- Any unexpected error while processing one file is caught, so one bad file never stops the scan.
- Symlinks and junctions are recorded as `skipped / link not followed`. Other reparse points (deduplicated or cloud-tiered files) are scanned normally.
- Config guardrail: the scan refuses to start if `output_dir` is inside a source.

### Testing
- `tests/`: 33 tests covering detection, the connector, walker resume, the worker, throttle, schedule, progress/ETA, config guardrails, and end-to-end runs: full scan, a simulated crash mid-run + resume with no rework, declining the estimate, and **no changes to source files** (size, mtime and content compared before and after).
- Manual check 2026-09-25: scanned 2,934 real files; force-killed the process twice (during the speed test, then mid-content-stage) and resumed to completion.

### Measured behaviour notes
- On a local disk: ~5,000 files/s for content, ~4,700 files/s for listing. Over SMB, expect far less; the speed test measures it.
- The time estimate uses per-file time from the random sample; the range is 0.8x–1.5x to allow for network variance.
