# Requirements & Guardrails

## Goal

As a data analyst, go through every file hosted on a server and categorize each one by:
- **Content** (what the file actually is and what it contains)
- **Creation date**
- **Last modification date**

The result is an inventory table that can be analysed in pandas, Excel or Power BI.

## Functional requirements

| ID | Requirement |
|---|---|
| R1 | Walk every file on the configured sources (Windows share first; Linux and cloud later) |
| R2 | Record path, size, file system created / modified / last-accessed dates, and extension |
| R3 | Detect the **real file type from content** (leading bytes), not the extension; flag mismatches |
| R4 | Read **embedded dates** (Office properties, PDF metadata, photo EXIF) and flag cases where they disagree with the file system dates |
| R5 | Categorize by **file type, sensitivity, staleness/retention, business topic**, using rules only |
| R6 | Find **duplicate files** using a quick fingerprint, confirmed with a full hash when sizes match |
| R7 | **Before starting:** show an approximate total time (count pass + speed test) and ask for confirmation |
| R8 | **During the run:** live progress (files, bytes, rate, errors, elapsed, **remaining time**) plus a `progress.json` status file |
| R9 | **Resume:** if the script breaks, it restarts from where it stopped, re-listing nothing and reprocessing nothing already done |
| R10 | Clean pause with Ctrl+C: finish the current batch, save, exit |
| R11 | Export to CSV/Parquet, plus a separate error report |

## Guardrails

1. **Read-only.** Files are never moved, renamed, deleted or tagged. Run under a service account that only has read permission.
2. **Last-accessed time is preserved in the record.** The original last-accessed time is captured from the folder listing *before* any file is opened. (With a read-only account the server may still update it on read, unless last-accessed updates are turned off on the server.)
3. **Content stays local.** Nothing is sent to an LLM or any outside service. By default the inventory stores only metadata, category and hash, never text excerpts.
4. **Assume sensitive data.** PII and credentials are flagged with a count and a confidence level. The values found are never stored.
5. **Low load on the server.** Limited worker threads, a MB/s cap, and an optional working-hours pause.
6. **Limits.** Content inspection is capped by size (read the first N MB). Symlinks and junctions are not followed.
7. **Tolerates errors.** Access-denied or locked files are logged and skipped. The scan saves checkpoints and can resume.
8. **Output kept separate.** Results and the state database are stored on the local PC (this project folder), never on the share being scanned.
9. **Dry run first.** A small sample folder is scanned and reviewed before the full scan.

## Rule-based classification (Phase 4 preview)

- **Sensitivity:** pattern rules plus validity checks (Luhn check digits for card numbers; PAN, Aadhaar, SSN, email, phone, IBAN, API-key and password formats). Stored as a count and a confidence level per type.
- **Business topic:** a keyword and folder-path rulebook in YAML maintained by the user, e.g. `finance: [invoice, GL, ledger, /Finance/]`. Each file gets a score per topic; anything below the threshold is labelled `unclassified`.
- **Staleness:** age groups from the modified and accessed dates, plus duplicate status.
