# Decision Log

Newest last. Each entry: what was decided, and why.

| # | Date | Decision | Reason / notes |
|---|---|---|---|
| D1 | 2026-09-25 | Treat as an **architectural** project: design → spec → plan → build | New tool with multiple subsystems; must not touch production data by accident |
| D2 | 2026-09-25 | Split into **4 phases**: 1 inventory crawler, 2 staleness & duplicates, 3 content extraction, 4 content classification | Scope covers mixed sources and four category types; too large for one script |
| D3 | 2026-09-25 | Sources are **mixed** long-term; use **pluggable source connectors** with a common interface | User has multiple/unknown storage types |
| D4 | 2026-09-25 | Categorize by **file type, sensitivity, staleness/retention, business topic** (all four) | User selection |
| D5 | 2026-09-25 | Scale target: **100K–5M files, 1–20 TB** | Requires parallelism, throttling, checkpointing |
| D6 | 2026-09-25 | Stack: **Python**; results kept in a **SQLite** state DB, exported to **CSV/Parquet** | Best libraries for content detection; easy to load into pandas, Excel or Power BI |
| D7 | 2026-09-25 | **Progress display** with live bar, files/bytes done, rate, errors, elapsed, **remaining time** | User requirement |
| D8 | 2026-09-25 | **Checkpoint and resume**: if the script breaks, it restarts from where it stopped | User requirement |
| D9 | 2026-09-25 | **Estimate time before starting** (count pass + speed test), then ask for confirmation | User requirement |
| D10 | 2026-09-25 | Classification stays **rule-based and local**. No file content is sent to an LLM or any outside service (guardrail #3 confirmed) | User decision: "stay rule based" |
| D11 | 2026-09-25 | First source: **Windows share (UNC path) read from the user's PC** | User selection |
| D12 | 2026-09-25 | Duplicate detection: **quick fingerprint** (size + first/last 1 MB) for every file; **full SHA-256 only when sizes match** | Full hashing of 20 TB ≈ 55 h at 100 MB/s; this cuts ~90% |
| D13 | 2026-09-25 | Project folder: **D:\FileInventoryScanner**; all details saved here | User instruction |
| D14 | 2026-09-25 | Section 1 approved; user said to **start implementing**. Sections 2–3 were decided with defaults during the build and documented in design-phase1.md | User instruction; skipped the separate written-spec and plan review |
| D15 | 2026-09-25 | Local **git repo, no remote**; Python 3.12 in project `.venv` (pyyaml, pyarrow, pytest) | User: "keep it local" |
| D16 | 2026-09-25 | One connector (`filesystem.py`) serves both UNC shares and local disks | Same Windows APIs; less code |
| D17 | 2026-09-25 | Embedded dates (Office/PDF/EXIF) moved to **Phase 3** | Keeps Phase 1 reads to ≤ 2 MB per file |
| D18 | 2026-09-25 | Only symlinks/junctions are treated as links; other reparse points (dedup, cloud-tiered) are scanned | Otherwise Windows dedup volumes would silently lose files |
| D19 | 2026-09-25 | On re-scan, unchanged files keep their **original pre-scan access time** | The scan's own read may have bumped it on the server |
| D20 | 2026-09-25 | Duplicate confirmation uses **quick-fingerprint collisions** (stricter than same-size) | Fewer full hashes than the same-size rule, same correctness |
| D21 | 2026-09-25 | Empty files are never reported as duplicates | They would form one huge meaningless group |
| D22 | 2026-09-25 | Owner lookup **not built** in Phase 1 | Was optional/off by default; added to open questions (Q8) |
| D23 | 2026-09-25 | Sensitive data to detect: **India IDs, US IDs, financial, contact/personal, credentials** | User selection |
| D24 | 2026-09-25 | Read Office + text, PDF text layer, emails (.msg/.eml **and .pst**), ZIP contents | User selection |
| D25 | 2026-09-25 | **OCR included**, English only (Tesseract 5.4 already installed) | User selection |
| D26 | 2026-09-25 | Findings stored **one row per finding, masked**, with location; raw values never written | User chose "per finding, masked" after the guardrail trade-off was explained |
| D27 | 2026-09-25 | Business topics built **now**: HR/Payroll, Finance/Accounts, Legal/Contracts, Sales/Clients | User selection |
| D28 | 2026-09-25 | No company-specific identifiers for now | User selection |
| D29 | 2026-09-25 | **Read everything** (no size cap); only zip-bomb protection applies | User selection; the zip-bomb guard protects the scanner rather than limiting coverage |
| D30 | 2026-09-25 | File sensitivity level from an editable **rule table** (HIGH/MEDIUM/LOW/NONE/UNKNOWN) | User selection |
| D31 | 2026-09-25 | PST cannot be read via Outlook (it writes to the PST); needs libpff, which requires the Windows SDK to compile | Spike result |
| D32 | 2026-09-25 | Phases 3+4 design **approved**; PST route **A + C**: build now, PSTs flagged `pst reader unavailable` until the Windows SDK is installed and libpff is compiled | User: "go ahead and implement it" (the PST route wasn't chosen explicitly; the recommended option was applied) |
| D33 | 2026-09-25 | Plain 12-digit Aadhaar-valid numbers without keyword/spacing are **medium** confidence (still count toward HIGH) | ~10% of random 12-digit numbers pass Verhoeff |
| D34 | 2026-09-25 | Keyword-required types are not reported without their keyword; no `low` confidence findings | Avoids flooding findings with false positives |
| D35 | 2026-09-25 | Card detection checks brand length; Maestro-prefix numbers next to "account" are bank accounts | Real false positive found in testing (HDFC-style account) |
| D36 | 2026-09-25 | Findings are one row per unique value per file part (+ occurrences); max 100k rows per file | Usable output for very large files |
| D37 | 2026-09-25 | Library error messages are never stored, only the error type | Messages can contain file content |
| D38 | 2026-09-25 | Content stage uses worker processes; crashed workers are replaced; workers exit if the main process dies | CPU-bound work; native crashes; orphans observed in testing |
| D39 | 2026-09-25 | Added `rescan-content` command | Re-apply edited rules without re-listing |
| D40 | 2026-09-25 | Phase 2 age groups: **< 6 months / 6–12 months / 1–3 years / 3–5 years / > 5 years**, measured from **last modified** | User selection |
| D41 | 2026-09-25 | Phase 2 report: **Excel workbook**, sections staleness by folder (**3 levels**), duplicates & wasted space, stale + sensitive, file types & growth | User selection |
| D42 | 2026-09-25 | Duplicates: groups and wasted space only, **no "keep this copy" suggestion** | User selection |
| D43 | 2026-09-25 | Report produced **on demand only** (`fscan report`) | User selection |
| D44 | 2026-09-26 | Phase 2 design approved; "Stale sensitive" = HIGH/MEDIUM files not modified for >= 1 year | User: "yes go ahead" (the >= 1 year threshold was flagged and not changed) |
| D45 | 2026-09-26 | Report dates use the local time zone; largest/duplicate file sizes shown in MB | Correct "as of" date near midnight; readable sizes |
