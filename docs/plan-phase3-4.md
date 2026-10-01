# Build Plan: Phases 3 + 4 (content, sensitivity, topics)

Design: [design-phase3-4.md](design-phase3-4.md), approved 2026-09-25.
Method: test-first, each task committed when its tests pass.

**Status: all 8 tasks done (2026-09-25).** See the as-built section in design-phase3-4.md.

| # | Task | Files | Test |
|---|---|---|---|
| 1 | Checksum validators: Verhoeff, Luhn + card prefixes, IBAN mod-97, GSTIN, SSN/ITIN rules | `fscan/pii/validators.py` | known valid / invalid numbers |
| 2 | Detection rules, context keywords, overlap handling, masking | `fscan/pii/patterns.py`, `fscan/pii/mask.py` | one positive + one negative case per type; masked output never contains the full value |
| 3 | Extraction framework: chunks with locations, throttled reader, nesting, zip-bomb guard | `fscan/extract/base.py` | locator maps a match position to line / cell / page |
| 4 | Extractors: text, rtf, docx, xlsx, pptx, xls, legacy doc/ppt, pdf, ocr, eml, msg, zip/tar/gz, pst (optional) | `fscan/extract/*.py` | generated sample files per format, incl. scanned PDF, nested zip in email, zip bomb, encrypted zip |
| 5 | Sensitivity levels + topic scoring from YAML rules | `fscan/classify/*.py`, `rules/*.yaml` | rule-table cases; topic scoring cases |
| 6 | Content stage: state tables, process pool, resume, estimate lines, progress | `fscan/state.py`, `fscan/content_stage.py`, `fscan/engine.py`, `fscan/config.py` | interrupted content stage resumes without rework |
| 7 | Export: new inventory columns, findings.csv/parquet, summary | `fscan/export.py` | end-to-end on planted fake data; **no raw value in any output file or the state DB** |
| 8 | Docs: how-to-run, design as-built, decisions | `docs/*` | none |

Decision recorded: PST route **A + C**. Build now with PST files flagged `pst reader unavailable`;
PST reading switches on automatically once `pypff` can be imported (after the Windows SDK is installed and libpff is compiled).
