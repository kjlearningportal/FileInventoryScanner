# Phases 3 + 4 Design: Content Extraction, Sensitivity & Topics

**Status:** Approved 2026-09-25. Being built; see plan-phase3-4.md.

## What the user asked for (2026-09-25)

| Topic | Answer |
|---|---|
| Sensitive data types | India IDs, US IDs, financial, contact/personal, credentials/secrets (all) |
| Formats to read | Office + text, PDF text layer, emails (.msg/.eml **and .pst**), inside ZIP archives |
| OCR | Yes, English (Tesseract 5.4 already installed at `C:\Program Files\Tesseract-OCR`) |
| Output detail | **One row per finding, masked value**, with location in the file |
| Business topics | Built **now**, together: HR/Payroll, Finance/Accounts, Legal/Contracts, Sales/Clients |
| Company-specific IDs | None for now |
| Large files | **Read everything** (no size cap) |
| File sensitivity level | Rule table (editable YAML) |

## Feasibility checks (spike, 2026-09-25)

- Windows builds are available for pypdfium2, pytesseract, extract-msg, python-docx, xlrd and olefile.
- Tesseract 5.4.0 is installed, with the `eng` language.
- **PST:** `libpff-python` has no Windows wheel for Python 3.12 (only a 2021 build for Python 3.9). Building from source failed because Visual Studio 2022 is **missing the Windows SDK** (`windows.h`). Options are in "PST decision" below.

## How it fits the existing scanner

A new **Stage 5: content** runs after Phase 1, using the same state DB, the same resume rules,
progress display, throttling, Ctrl+C handling and time estimate. It only processes files whose
`type_group` is readable (document, spreadsheet, presentation, email, archive, text, data,
config, log, code, web, image). Files that change are re-read on `--rescan`, as in Phase 1.

```
fscan/
  extract/               one module per format; each yields text chunks + location
    text.py              txt/csv/json/xml/log/code: streamed, encoding detection
    office.py            docx/xlsx/pptx (streamed XML), xls (xlrd), doc/ppt (best effort)
    pdf.py               text layer per page (pypdfium2); pages without text → OCR
    ocr.py               Tesseract on images and scanned PDF pages
    email.py             .eml (stdlib), .msg (extract-msg): body + attachments (recursive)
    archive.py           .zip members (recursive), with zip-bomb protection
    pst.py               .pst/.ost via pypff, only when the library is available
  detect_pii/
    patterns.py          regexes + context keywords per data type
    validators.py        Verhoeff (Aadhaar), Luhn (cards), mod-97 (IBAN), GSTIN checksum, SSN rules
    mask.py              masking per type
  classify/
    sensitivity.py       file level from the rule table
    topics.py            keyword/path scoring against topics.yaml
  content_stage.py       stage runner (reuses engine pool, progress, checkpointing)
rules/
  sensitivity.yaml       levels and thresholds (editable)
  topics.yaml            topics + keywords (editable)
```

## Extraction

- **Streaming, "read everything":** text is processed in chunks, so a 5 GB log never has to fit in memory. Overlap between chunks keeps a number split across two chunks from being missed.
- **Location** recorded per chunk: `page 3`, `Sheet1!C14`, `slide 5`, `line 1204`, `attachment: payslip.pdf > page 1`, `zip: hr.zip > 2024/salary.xlsx > Sheet1!B2`, `pst: Inbox/2023 > "Offer letter" > attachment offer.docx`.
- **OCR:** images (png/jpg/tiff/bmp), and PDF pages with no text layer, are rendered at 300 dpi and OCR'd. OCR pages are counted separately in the estimate and progress, since each takes 1–5 s.
- **Nested content:** email attachments and ZIP members are extracted **in memory**, or to a private temp folder on this PC that is deleted right after. Nesting depth limit: 3.
- **Safety limits** (these protect the scanner and don't reduce coverage): a zip bomb (expansion > 100x or > 10 GB unpacked) is stopped and flagged.
- **Can't read:** password-protected or encrypted files, and corrupt files, are flagged in `content_note` (e.g. `encrypted`, `corrupt`, `pst reader unavailable`). They are not treated as errors that retry forever.
- **Legacy .doc / .ppt** (pre-2007 binary formats) are read on a best-effort basis (text runs recovered from the file); the note says so.

## Sensitive data detection

Each finding gets a **confidence** level:
- `high`: the checksum is valid, or the format is unique (e.g. an AWS key prefix).
- `medium`: the format matches and a context keyword is nearby (e.g. "SSN", "passport", "A/c No").
- `low`: the format matches only. Kept in findings, but not counted toward the file's level.

| Type | Rule | Confidence | Masked as |
|---|---|---|---|
| PAN | `AAAAA9999A`, 4th character is a valid holder type | high | `ABCP*****F` |
| Aadhaar | 12 digits, first digit 2–9, **Verhoeff** check | high | `XXXX XXXX 1234` |
| GSTIN | 15 characters, state code + embedded PAN + checksum | high | `27ABCP*****1Z5` |
| Indian passport | `A1234567` + keyword | medium | `A12****7` |
| Voter ID (EPIC) | `ABC1234567` + keyword | medium | `ABC****567` |
| Driving licence (IN) | state code + digits + keyword | medium | `MH12*********34` |
| IFSC | `AAAA0XXXXXX` | high | shown in full (not personal) |
| Bank account | 9–18 digits + keyword (A/c, account no) | medium | `XXXXXXXX4321` |
| UPI ID | `name@bank-handle` (known handles) | high | `ra***@okhdfc` |
| Card number | 13–19 digits, **Luhn** + known card prefix | high | `XXXX-XXXX-XXXX-1234` |
| IBAN | country format + **mod-97** | high | `GB82 **** **** 5432` |
| SWIFT/BIC | 8/11 characters + keyword | medium | shown in full |
| US SSN | `###-##-####`, valid area/group rules | high with dashes, else medium + keyword | `XXX-XX-6789` |
| US ITIN | `9##-[7/8]#-####` | high | `XXX-XX-6789` |
| US EIN | `##-#######` + keyword | medium | `XX-XXX4567` |
| US passport | 9 characters + keyword | medium | `*****4567` |
| Email | RFC-style | high | `r***@company.com` |
| Phone (IN) | `+91` / `0` + `[6-9]` + 9 digits | high | `+91 98*****210` |
| Phone (US/intl) | NANP rules / E.164 | medium | `+1 415***2671` |
| Date of birth | a date next to "DOB / date of birth" | medium | `**/**/1985` |
| Private key | `-----BEGIN ... PRIVATE KEY-----` | high | `[private key]` |
| Cloud/API keys | AWS `AKIA…`, GitHub `ghp_…`, Slack `xox…`, Azure storage key/connection string, Google `AIza…`, JWT | high | first 4 characters + `****` |
| Password in text | `password/pwd/passwd = …` or `:` assignment | medium | `[password]` (no characters shown) |
| DB connection string | contains `Password=` / `pwd=` | high | `[connection string]` |

**Findings output:** one row per **unique value per file**, with `occurrences` and
`first_location`. The raw value is used in memory only to de-duplicate and is never written anywhere.

## File sensitivity level (rules/sensitivity.yaml)

| Level | Rule (medium/high confidence findings only) |
|---|---|
| **HIGH** | any Aadhaar, SSN, ITIN, card, bank account, IBAN, private key, API key, password, connection string; or ≥ 10 PAN/passport/voter ID/driving licence |
| **MEDIUM** | 1–9 PAN/passport/voter ID/driving licence/GSTIN/EIN; or ≥ 50 emails/phones; or any date of birth |
| **LOW** | a few emails/phones only |
| **NONE** | nothing found |
| **UNKNOWN** | couldn't be read (encrypted, corrupt, PST reader unavailable) |

## Business topics (rules/topics.yaml)

Four starting topics with editable keyword lists: **HR/Payroll, Finance/Accounts, Legal/Contracts,
Sales/Clients**. Score = keyword hits in the **folder path and file name (weight 3)** + hits in the
**content** (weight 1, counting each distinct keyword once per 1,000 words, so long files don't dominate).
A file gets every topic scoring at or above the threshold (default 5), sorted by score, or `unclassified`.

## New output

**Inventory:** new columns
`content_status, sensitivity, pii_types, pii_counts, has_credentials, topics, topic_scores,
text_chars, pages, ocr_pages, content_note`

**findings.csv / findings.parquet:**
`path, inner_path, data_type, masked_value, confidence, occurrences, first_location`

## PST decision (needs user choice)

| Option | Effort | Notes |
|---|---|---|
| **A. Install the "Windows 11 SDK" component in Visual Studio Installer, then compile the PST reader** | ~15 min, one checkbox, ~2 GB download | Recommended. Uses the latest libpff. |
| B. Use the 2021 build with a separate Python 3.9 helper process | Python 3.9 install + extra code | Older library version; more moving parts |
| C. Build everything else now; PST files are flagged `pst reader unavailable` until A is done | None now | The PST module switches on automatically once the library imports |

## Time estimate additions

The speed test also samples readable files for the content stage. The estimate block gains lines
for content extraction, the number of OCR pages (estimated from image and scanned-PDF counts), and PST mailboxes.

## Testing

- Validators are tested against known-valid and known-invalid numbers (Verhoeff, Luhn, mod-97, GSTIN).
- Each extractor gets small generated sample files (docx, xlsx, pdf with a text layer, a scanned-style
  image PDF, msg/eml with attachments, nested zip, zip bomb).
- A check that no raw value appears anywhere in the outputs: plant known fake IDs, then grep every output file.
- End-to-end run with an interrupt and resume, as in Phase 1.

---

## As built (2026-09-25): differences from the design above

| Area | Design said | Built | Why |
|---|---|---|---|
| Aadhaar confidence | high | **high** when spaced `XXXX XXXX XXXX` or a keyword is near; otherwise **medium** | Any 12-digit number has a 1-in-10 chance of passing Verhoeff; still counts toward HIGH |
| `low` confidence | kept but not counted | **not produced** | Keyword-required types (passport, voter ID, EIN, bank account, 10-digit phone) are simply not reported without their keyword; otherwise they'd flood the findings |
| Card numbers | Luhn + prefix | Luhn + prefix + **correct length per brand**; a Maestro-prefix number next to "account" (and no card keyword) is a bank account, not a card | A real HDFC-style 14-digit account passed Luhn with prefix `50` in testing |
| Spreadsheet / CSV context | none | Each cell is scanned as `Header: value`, so the column header works as the keyword | Keyword rules would otherwise never fire in tables |
| Topic content score | ≤1 per keyword per 1,000 words | `min(hits, 3)` per keyword (editable `max_hits_per_keyword`) | Simpler, same effect: long files can't win on repetition |
| Findings | one row per finding | one row per **unique value per file part**, with `occurrences`; `pii_counts` = distinct values across the file; at most 100,000 rows per file (`findings_truncated` note) | Keeps findings usable for 1M-row CSVs |
| Error messages | not specified | For library errors only the **error type** is stored, never its message | Messages can quote file content |
| Parallelism | not specified | Content stage runs in **worker processes** (`content.workers`) | Regex scanning is CPU-bound |
| Worker crashes | not specified | A crashed worker is replaced; its files are marked for retry; the scan continues | Native PDF/Office crashes happen on malformed files |
| Orphaned workers | not specified | Workers exit on their own when the main process disappears | Found in testing: a hard kill left 4 workers running |
| Rule changes | not specified | New command `rescan-content` re-reads all content with the current rules | Needed after editing rules/*.yaml |
| Unsupported formats | not specified | 7z, rar, cab, heic, psd, vsdx, mbox → `unsupported_format` (sensitivity UNKNOWN) | No pure-Python readers; can be added later |

### Verification

- 117 automated tests: validators against published valid numbers, one positive and one negative case per data type, every extractor on generated samples (incl. **real Tesseract OCR** of a scanned PDF and an image), nested email → zip → file, zip bomb, encrypted member, depth limit, sensitivity and topic rules, crash of a worker process, stop + resume inside stage 5, `rescan-content`, and **a check that no planted raw value appears in any output file, log or the state DB**.
- Manual run on a demo share (440 files): estimate, hard kill during stage 5, **no orphaned workers**, resume, correct findings for csv/docx/xlsx/pptx/pdf/scanned pdf/eml+zip/config/pst.

### Not verified yet

- `.msg`: logic tested with stand-in objects only (no real .msg file to test with).
- `.pst`: code written against the libpff API but **untested**, because the library isn't installed (Windows SDK missing).
- Legacy `.doc` / `.ppt` / `.xls`: no sample files generated; best-effort text recovery.
- Speed on a real network share: measured only on local disk.
