# How to Run fscan: Step by Step

All commands are for **Windows PowerShell**. Python and the libraries are already installed in
the project's `.venv` folder, so there is nothing to install.

---

## Step 1: Open PowerShell in the project folder

Press **Win**, type `powershell`, and press **Enter**. Then:

```powershell
cd D:\FileInventoryScanner
```

## Step 2: Check you can reach the share

```powershell
Test-Path '\\fileserver\dept'
```

- `True` means you're good.
- `False` means the path is wrong, you're off the VPN or network, or you don't have access. Fix that first.

*Optional: to scan with a separate read-only service account, open PowerShell as that account
for network access only:*
```powershell
runas /netonly /user:DOMAIN\svc_scan powershell
```
*Run the remaining steps in the new window that opens.*

## Step 3: Create your config file (first time only)

```powershell
copy config.example.yaml config.yaml
notepad config.yaml
```

Edit these lines, then save and close:

```yaml
sources:
  - name: dept_share
    root: '\\fileserver\dept'     # your share, kept in SINGLE quotes

output_dir: output                # results go to D:\FileInventoryScanner\output
max_read_mb_per_sec: 50           # agree this with the server owner
scan_hours: null                  # or '19:00-07:00' to scan only overnight
```

> Keep the path in **single quotes**, so the backslashes are read literally.
> To scan several shares, add more `- name: / root:` entries.

## Step 4: Get the time estimate (nothing heavy is run)

```powershell
.venv\Scripts\python -m fscan scan -c config.yaml --estimate-only
```

What happens:
1. **Stage 1, listing:** walks every folder and counts files and total size. No file is opened. You'll see
   `[listing] 12,400 folders | 1,850,000 files found (4.2 TB) | ...`
2. **Stage 2, speed tests:** reads a random sample of 500 files to measure speed, then reads the
   content of 200 readable files (Word, Excel, PDF, emails...) to time the sensitive-data scan.
3. It prints the estimate, then stops:

```
================ TIME ESTIMATE ================
Inventory     : 1,850,000 files, 4.2 TB in 12,400 folders (listing took 38m12s)
File types    : ~6h40m for 1,849,500 files (likely range 5h20m - 10h00m)
Duplicates    : up to ~1h10m extra ...
Sensitive data: ~31h10m to read ~920,000 files (sample: 200 files in 24.4s with 4 processes)
  OCR         : ~41,000 scanned pages/images expected (Tesseract ready)
  Mailboxes   : 57 PST/OST files (310.2 GB) - reader NOT installed: flagged 'pst_reader_unavailable', not read
Read cap      : 50 MB/s | scan hours: any time
-----------------------------------------------
TOTAL         : ~39h00m (likely range 31h12m - 58h30m)
===============================================
```

> Reading content is much slower than cataloguing files, and OCR is the slowest part (1–5 s per page).
> To catalogue first and read content later, set `content: enabled: false`, run the scan, then set it back
> to `true` and run `scan` again. Only the content stage will run.

The listing is saved, so the next step does **not** list everything again.

## Step 5: Run the full scan

```powershell
.venv\Scripts\python -m fscan scan -c config.yaml
```

It shows the estimate again and asks `Proceed with the full scan? [y/N]`. Type **y** and press **Enter**.

You'll then see live progress:

```
Stage 3/5 - identifying file types and fingerprints
[content] Files 1,204,331 / 1,850,000 (65.1%) | 2.9 TB / 4.2 TB | 212 files/s | Errors: 37 | Elapsed 4h02m | Remaining ~2h10m
...
Stage 5/5 - reading file content: sensitive data and topics (920,000 files to read)
[sensitive data] Files 310,442 / 920,000 (33.7%) | 610 GB / 1.9 TB | 9 files/s | Errors: 12 | Elapsed 9h40m | Remaining ~18h50m
```

When it finishes you'll see `Scan complete. N files exported to ...`.

**Leave the PowerShell window open** while it runs. Stop your PC from sleeping during long scans
(Settings → System → Power → Sleep: Never).

## Step 6: Check progress from another window (optional)

Open a second PowerShell window:

```powershell
cd D:\FileInventoryScanner
.venv\Scripts\python -m fscan status -c config.yaml
```

This is safe while the scan is running. It shows the stage, files done, pending and errors, and the remaining time.

## Step 7: Stopping and resuming

| Situation | What to do |
|---|---|
| Stop on purpose | Press **Ctrl+C** once and wait for "Stopped cleanly" |
| Crash, reboot, network drop, window closed | Nothing is needed; progress is saved every few seconds |
| **Resume** | Run the **same command** as Step 5 again. It continues where it stopped and doesn't ask again. |
| Force quit (stuck) | Press **Ctrl+C** twice; at most the last few seconds of work are redone |

## Step 8: Open the results

Everything is in `D:\FileInventoryScanner\output\`:

| File | Open with | Contains |
|---|---|---|
| `inventory.csv` | Excel | One row per file: path, size, dates, real type, duplicates, **sensitivity, data types found, topics** |
| `inventory.parquet` | Power BI, pandas | Same data, faster to load for millions of rows |
| `findings.csv` | Excel | **One row per sensitive value found** (masked), with where it is in the file |
| `findings.parquet` | Power BI, pandas | Same findings, for large result sets |
| `summary.json` | Notepad | Files per sensitivity level, per data type, per topic, per type group |
| `errors.csv` | Excel | Files and folders that couldn't be read, and why |
| `logs\run.log` | Notepad | Detailed log |

```powershell
start output\inventory.csv
```

> Excel handles about 1 million rows. For bigger inventories use the Parquet file in Power BI or pandas.

### Finding sensitive files

In `inventory.csv`, filter on:

| Column | Values |
|---|---|
| `sensitivity` | `HIGH` / `MEDIUM` / `LOW` / `NONE` / `UNKNOWN` (couldn't be read, see `content_note`) |
| `pii_types` / `pii_counts` | e.g. `AADHAAR;BANK_ACCOUNT` / `AADHAAR=300;BANK_ACCOUNT=300` (distinct values) |
| `has_credentials` | `True` = passwords, keys, tokens or connection strings in the file |
| `topics` | `HR/Payroll`, `Finance/Accounts`, `Legal/Contracts`, `Sales/Clients` or `unclassified` |
| `content_status` | `ok`, `partial` (some part unreadable), `unreadable`, `not_applicable` (e.g. .exe, video) |
| `content_note` | why: `encrypted`, `needs_ocr`, `pst_reader_unavailable`, `zip_bomb`, `depth_limit`... |

Then open `findings.csv` and filter `path` for the file to see every value found. Example:

| path | inner_path | data_type | masked_value | confidence | occurrences | first_location |
|---|---|---|---|---|---|---|
| ...\salary_2025.csv | | AADHAAR | XXXX XXXX 4323 | high | 300 | row 2, Aadhaar No |
| ...\offer.eml | attachment: docs.zip > payslip.txt | US_SSN | XXX-XX-1234 | high | 1 | line 1 |
| ...\kyc_scan.pdf | | PAN | ABCP*****F | high | 1 | page 1 (OCR) |

Values are **always masked**. The full value is never written to any output, log or the state database.

### Changing the rules

- `rules\sensitivity.yaml`: which data types make a file HIGH / MEDIUM / LOW.
- `rules\topics.yaml`: topics and their keywords, weights and threshold.

After editing, re-read content with the new rules:
```powershell
.venv\Scripts\python -m fscan rescan-content -c config.yaml
```

To export a **partial** result while a scan is paused or stopped:
```powershell
.venv\Scripts\python -m fscan export -c config.yaml
```

## Step 9: Staleness, duplicates and risk report (Excel)

Run this any time after (or during) a scan. It reads only the saved results, so it doesn't touch the share and is quick:

```powershell
.venv\Scripts\python -m fscan report -c config.yaml
start output\report_2026-09-26.xlsx
```

| Sheet | Use it to |
|---|---|
| **Summary** | See totals per age group, sensitivity by age, wasted GB from duplicates, and warnings (partial scan, last-accessed not recorded, truncated sheets) |
| **Age by folder** | Find folders (3 levels deep) full of old data: sort by `% stale (3y+)` or by the Archive GB column |
| **Duplicate groups** | See which identical files waste the most space (`Wasted GB`) |
| **Duplicate files** | Every copy of each duplicate group, with dates and sensitivity |
| **Stale sensitive** | HIGH/MEDIUM files untouched for 1+ year: the first clean-up / review list |
| **File types** | GB and % stale per type group and top 50 extensions |
| **Largest files** | The 100 biggest files |
| **Growth by year** | Files and GB by year created and year last modified |

Age groups (from **last modified**, counted back from the report date):
**Active** < 6 months · **Recent** 6–12 months · **Aging** 1–3 years · **Stale** 3–5 years · **Archive** > 5 years.

> The report is a list for review. Nothing is moved or deleted, and it doesn't suggest which duplicate to keep.

## Step 10: Re-scan later (e.g. monthly)

```powershell
.venv\Scripts\python -m fscan scan -c config.yaml --rescan
```

Lists everything again, but only reads files that are new or changed since the last scan.

---

## Command reference

| Command | Purpose |
|---|---|
| `scan -c config.yaml` | Run or resume the scan (asks before the heavy part) |
| `scan -c config.yaml --estimate-only` | List + time estimate only |
| `scan -c config.yaml --yes` | Don't ask; for scheduled or unattended runs |
| `scan -c config.yaml --rescan` | New full pass; unchanged files are not re-read |
| `status -c config.yaml` | Progress and remaining time |
| `export -c config.yaml` | Write the CSV/Parquet from what's done so far |
| `report -c config.yaml` | Excel report: staleness, duplicates, stale sensitive files, types, growth |
| `rescan-content -c config.yaml` | Re-read content with edited rules |

Prefix every command with `.venv\Scripts\python -m fscan`.

## Troubleshooting

| Message / symptom | Fix |
|---|---|
| `Source 'x' is not reachable` | Check the path with `Test-Path`, VPN and permissions (Step 2) |
| `Config error: output_dir ... is inside source` | The results folder must not be on the share; keep `output_dir: output` |
| `Config error: unknown config keys` | A setting name is misspelled in `config.yaml` |
| Many `PermissionError` rows in `errors.csv` | Your account can't read those folders; ask for read access, then re-run `scan` to retry them |
| Progress says `paused: outside scan hours` | Working as configured (`scan_hours`); it resumes automatically in the window |
| Very slow | Raise `workers` (e.g. 16) or `max_read_mb_per_sec` if the server owner agrees |
| Start completely fresh | Delete the `output` folder (this discards all saved progress) |
| `content_note` says `needs_ocr` | Tesseract wasn't found. Install it or set `content: tesseract_cmd:` |
| `content_note` says `pst_reader_unavailable` | Install "Windows 11 SDK" in Visual Studio Installer, then ask for libpff to be compiled (see open-questions Q9) |
| Many `worker process crashed` errors | A file crashed the PDF/Office library. It's retried up to 3 times, then listed in `errors.csv`; the scan carries on |
