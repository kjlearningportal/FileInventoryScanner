# Phase 2 Design: Staleness, Duplicates & Risk Report

**Status:** Approved and built 2026-09-26.

## What the user asked for (2026-09-25)

| Topic | Answer |
|---|---|
| Age groups | **Active < 6 months · Recent 6–12 months · Aging 1–3 years · Stale 3–5 years · Archive candidate > 5 years** |
| Date that decides age | **Last modified** (last-accessed shown alongside, with a reliability check) |
| Sections | Staleness by folder · Duplicates & wasted space · Risk: stale + sensitive · File types & growth |
| Format | **Excel workbook** |
| Folder depth | **3 levels** below the share root |
| Which duplicate to keep | **No suggestion**: groups and wasted space only |
| When | **On demand only**: `fscan report -c config.yaml` |

## How it works

- A new command, `python -m fscan report -c config.yaml`, writes `output\report_YYYY-MM-DD.xlsx`.
- It **only reads the state database**. No file on the share is touched, so it runs in seconds to minutes and can be run at any time, including mid-scan for a partial view.
- Age is measured from the report date ("as of" date, shown on the Summary sheet).
- It covers all files listed in the current scan run. Files skipped by config (temp files, links) are excluded and counted on the Summary sheet.
- Built for millions of files: SQL aggregation plus streaming; the workbook is written in openpyxl's low-memory mode.

## Workbook sheets

| Sheet | One row per | Columns |
|---|---|---|
| **Summary** | – | As-of date; totals (files, GB); files and GB per age group with %; sensitivity × age-group matrix; duplicate groups, copies and wasted GB; last-accessed reliability check result; definitions of each group and column |
| **Age by folder** | folder, 3 levels below the root | source, folder, files, GB, then files and GB per age group, % stale (≥ 3 years), oldest and newest modified. Sorted by GB. |
| **Duplicate groups** | set of identical files | group #, copies, size each, **wasted GB** = size × (copies − 1), file name, one example path. Sorted by wasted GB. |
| **Duplicate files** | file in a duplicate group | group #, path, folder, size, created, modified, age group, sensitivity |
| **Stale sensitive** | HIGH/MEDIUM file not modified for ≥ 1 year | path, sensitivity, data types found (counts), has credentials, topics, modified, age (years), age group, size, duplicate copies. Sorted HIGH first, then oldest. |
| **File types** | type group, then top 50 extensions | files, GB, % of total GB, average size, oldest/newest modified, % stale |
| **Largest files** | top 100 files by size | path, size, type, modified, age group, sensitivity |
| **Growth by year** | year | files and GB **created** that year, files and GB **last modified** that year |

All sheets: bold header row, frozen header, filters on, readable column widths, sizes in GB (2 decimals), dates as `YYYY-MM-DD`.

## Checks and caveats built into the Summary

- **Last-accessed reliability:** if fewer than 1% of files show an access date later than their modified date, the server likely doesn't record access times. The Summary says so, and last-accessed should not be used for decisions.
- **Created dates:** copying or restoring files resets the created date, so "Growth by year" also shows the modified year. A note explains this.
- **Excel's 1,048,576-row limit:** if a sheet (e.g. Duplicate files) exceeds it, rows are cut at the limit, sorted so the biggest items come first, and the Summary says the sheet was truncated.
- **Partial scans:** if the scan isn't finished, the Summary shows "partial: N files still pending", and duplicates/sensitivity reflect only what has been processed.

## Files

| File | Purpose |
|---|---|
| `fscan/report.py` | Builds the workbook from the state DB |
| `fscan/cli.py` | Adds the `report` command |
| `tests/test_report.py` | Synthetic state DB with known ages, duplicates and sensitivity → checks every sheet's numbers |

## Testing

- Age-group boundaries (exactly 6 months, 1, 3 and 5 years).
- Folder grouping at 3 levels, including files that sit less than 3 levels deep.
- Duplicate wasted space arithmetic; empty files excluded.
- The stale-sensitive list includes only HIGH/MEDIUM files ≥ 1 year old.
- Last-accessed check: both "recorded" and "not recorded" cases.
- Row-limit truncation (with a small limit set for the test).
- No source file is opened (the report runs with the share unreachable).

---

## As built (2026-09-26)

Built as designed: `fscan/report.py`, the `report` command in `fscan/cli.py`, and `tests/test_report.py`.

Small differences:
- **"As of" date and all dates use the PC's local time zone** (IST), not UTC. A report run just after midnight was showing the previous day.
- **Largest files** and **Duplicate files** show sizes in **MB** (GB rounds small files to 0); folder, type and summary figures stay in GB.
- The code is split into one streaming pass that collects aggregates (`_Stats`) plus one writer per sheet, so memory stays bounded for millions of files.

### Verification
- 10 tests: age-group boundaries (exactly 6 months / 1 / 3 / 5 years, future dates, missing dates), 3-level folder grouping including files less than 3 levels deep, duplicate wasted-space arithmetic with empty files excluded, stale-sensitive filter and order, largest files, growth by created and modified year, file types, Summary text, the last-accessed check both ways, and row-limit truncation with a warning.
- The report is built with the share **unreachable** in the tests, confirming it reads only the state DB.
- Manual run on the demo share (444 files spread across 2015–2026 with planted duplicates): age totals, sensitivity × age, 4 duplicate groups (incl. a copied payroll CSV), and 8 stale sensitive files were all correct.
