"""Phase 2: staleness, duplicates and risk report (Excel), built from the state DB only.

No file on the share is opened. Age is measured from the report date, using last modified.
"""
import calendar
import datetime as dt
import heapq
import os
import re
from collections import defaultdict

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# (name, definition). Order matters: youngest first.
AGE_GROUPS = [
    ("Active (<6m)", "modified in the last 6 months"),
    ("Recent (6-12m)", "modified 6 to 12 months ago"),
    ("Aging (1-3y)", "modified 1 to 3 years ago"),
    ("Stale (3-5y)", "modified 3 to 5 years ago"),
    ("Archive (>5y)", "not modified for more than 5 years"),
]
GROUP_NAMES = [g[0] for g in AGE_GROUPS]
STALE_FROM = 3             # "% stale" = groups at or beyond 3 years
RISK_FROM = 2              # stale-sensitive list: groups at or beyond 1 year
EXCEL_MAX_ROWS = 1_048_575
FOLDER_DEPTH = 3
GB = 1024 ** 3
SENSITIVITY_ORDER = ["HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN"]

_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill("solid", fgColor="305496")
_TITLE_FONT = Font(bold=True, size=14)
_SECTION_FONT = Font(bold=True, size=11, color="305496")
_WARN_FONT = Font(bold=True, color="C00000")


# ---- ages ---------------------------------------------------------------------
def _minus_months(d: dt.datetime, months: int) -> dt.datetime:
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    m += 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def age_cutoffs(as_of: dt.datetime):
    """Epoch cut-offs: modified >= cutoff[i] puts a file in group i."""
    return [_minus_months(as_of, m).timestamp() for m in (6, 12, 36, 60)]


def age_group(modified, cutoffs) -> str:
    if modified is None:
        return "Unknown"
    for name, cut in zip(GROUP_NAMES, cutoffs):
        if modified >= cut:
            return name
    return GROUP_NAMES[-1]


def access_time_recorded(n_files: int, n_accessed_after_modified: int) -> bool:
    """Servers with last-access updates off show accessed == modified/created for almost every file."""
    return n_files > 0 and n_accessed_after_modified / n_files >= 0.01


# ---- helpers -------------------------------------------------------------------
def _gb(n):
    return round((n or 0) / GB, 2)


def _mb(n):
    return round((n or 0) / 1024 ** 2, 2)


def _date(epoch):
    return None if epoch is None else dt.datetime.fromtimestamp(epoch).date()


def _folder(path, roots):
    for root in roots:
        if path.lower().startswith(root.lower()):
            rel = path[len(root):].strip("\\/")
            parts = re.split(r"[\\/]", rel)[:-1][:FOLDER_DEPTH]
            sep = "\\" if "\\" in root else "/"
            return root.rstrip("\\/") + (sep + sep.join(parts) if parts else "")
    return os.path.dirname(path)


class _Sheet:
    """Write-only sheet with a styled header, frozen top row, filters, widths and a row cap."""

    def __init__(self, wb, title, columns, widths, max_rows):
        self.ws = wb.create_sheet(title)
        self.title, self.n, self.max_rows, self.truncated = title, 0, max_rows, False
        for i, w in enumerate(widths, start=1):
            self.ws.column_dimensions[get_column_letter(i)].width = w
        self.ws.freeze_panes = "A2"
        self.cols = len(columns)
        self.ws.append([self._cell(c, header=True) for c in columns])

    def _cell(self, v, header=False):
        c = WriteOnlyCell(self.ws, value=v)
        if header:
            c.font, c.fill = _HEADER_FONT, _HEADER_FILL
            c.alignment = Alignment(wrap_text=True, vertical="top")
        elif isinstance(v, dt.date):
            c.number_format = "yyyy-mm-dd"
        return c

    def add(self, row) -> bool:
        if self.n >= self.max_rows:
            self.truncated = True
            return False
        self.ws.append([self._cell(v) for v in row])
        self.n += 1
        return True

    def close(self):
        self.ws.auto_filter.ref = f"A1:{get_column_letter(self.cols)}{self.n + 1}"


# ---- collect -------------------------------------------------------------------
_FILES = """
SELECT f.path, f.name, f.ext, f.size, f.created, f.modified, f.accessed, f.type_group, f.detected_type,
       f.status, c.sensitivity, c.pii_counts, c.has_credentials, c.topics,
       CASE WHEN f.sha256 IS NULL OR f.size = 0 THEN 1
            ELSE COUNT(*) OVER (PARTITION BY f.sha256) END AS dup_count
FROM files f LEFT JOIN content c ON c.path = f.path
WHERE f.seen_run = ? AND f.status != 'skipped'
"""


def _zero():
    return {"files": 0, "bytes": 0}


def _span(d, when):
    if when:
        d["oldest"] = when if d["oldest"] is None else min(d["oldest"], when)
        d["newest"] = when if d["newest"] is None else max(d["newest"], when)


def _count(d, size, stale=None):
    d["files"] += 1
    d["bytes"] += size
    if stale is not None:
        d["stale"] += stale


class _Stats:
    """One streaming pass over every file, keeping only aggregates (memory stays bounded)."""

    def __init__(self, as_of, roots):
        self.as_of, self.roots, self.cutoffs = as_of, roots, age_cutoffs(as_of)
        self.n_files = self.n_bytes = self.n_accessed_after = 0
        self.totals = {g: _zero() for g in GROUP_NAMES + ["Unknown"]}
        self.folders = defaultdict(lambda: {"groups": defaultdict(_zero), "files": 0, "bytes": 0,
                                            "oldest": None, "newest": None})
        self.types = defaultdict(lambda: {"files": 0, "bytes": 0, "oldest": None, "newest": None, "stale": 0})
        self.exts = defaultdict(lambda: {"files": 0, "bytes": 0, "stale": 0})
        self.created_year, self.modified_year = defaultdict(_zero), defaultdict(_zero)
        self.matrix = defaultdict(lambda: defaultdict(int))
        self.largest, self.risk = [], []

    def add(self, r):
        size, mod = r["size"] or 0, r["modified"]
        group = age_group(mod, self.cutoffs)
        gi = GROUP_NAMES.index(group) if group in GROUP_NAMES else -1
        stale = gi >= STALE_FROM
        self.n_files += 1
        self.n_bytes += size
        if r["accessed"] and mod and r["accessed"] > mod + 86400:
            self.n_accessed_after += 1
        _count(self.totals[group], size)

        fo = self.folders[_folder(r["path"], self.roots)]
        _count(fo, size)
        _count(fo["groups"][group], size)
        _span(fo, mod)
        t = self.types[r["type_group"] or "not identified"]
        _count(t, size, stale)
        _span(t, mod)
        _count(self.exts[r["ext"] or "(none)"], size, stale)
        if r["created"]:
            _count(self.created_year[_date(r["created"]).year], size)
        if mod:
            _count(self.modified_year[_date(mod).year], size)

        self.matrix[r["sensitivity"] or "Not scanned"][group] += 1
        self._track_largest((size, r["path"], r["detected_type"], mod, group, r["sensitivity"]))
        if r["sensitivity"] in ("HIGH", "MEDIUM") and gi >= RISK_FROM:
            years = round((self.as_of.timestamp() - mod) / 86400 / 365.25, 1)
            self.risk.append((r["path"], r["sensitivity"], r["pii_counts"], bool(r["has_credentials"]),
                              r["topics"], _date(mod), years, group, _gb(size), r["dup_count"]))

    def _track_largest(self, item):
        if len(self.largest) < 100:
            heapq.heappush(self.largest, item)
        elif item[0] > self.largest[0][0]:
            heapq.heapreplace(self.largest, item)


# ---- sheets --------------------------------------------------------------------
def _sheet_folders(wb, st, max_rows):
    cols = ["Source root", "Folder", "Files", "GB"]
    for g in GROUP_NAMES:
        cols += [f"{g} files", f"{g} GB"]
    cols += ["% stale (3y+)", "Oldest modified", "Newest modified"]
    sh = _Sheet(wb, "Age by folder", cols, [22, 60, 10, 10] + [11] * 10 + [11, 14, 14], max_rows)
    for folder, fo in sorted(st.folders.items(), key=lambda kv: -kv[1]["bytes"]):
        root = next((x for x in st.roots if folder.lower().startswith(x.lower().rstrip("\\/"))), "")
        row = [root, folder, fo["files"], _gb(fo["bytes"])]
        for g in GROUP_NAMES:
            row += [fo["groups"][g]["files"], _gb(fo["groups"][g]["bytes"])]
        stale_files = sum(fo["groups"][g]["files"] for g in GROUP_NAMES[STALE_FROM:])
        row += [round(100 * stale_files / fo["files"], 1), _date(fo["oldest"]), _date(fo["newest"])]
        if not sh.add(row):
            break
    return sh


def _sheets_duplicates(wb, state, cutoffs, max_rows):
    """Returns ([groups sheet, files sheet], number of groups, redundant copies, wasted bytes)."""
    db, run = state.db, state.run_id
    db.execute("DROP TABLE IF EXISTS temp.dupg")
    db.execute("CREATE TEMP TABLE dupg (sha256 TEXT PRIMARY KEY, grp INTEGER)")
    groups = db.execute(
        "SELECT sha256, COUNT(*) n, MAX(size) size, MIN(path) example, MIN(name) name FROM files "
        "WHERE seen_run=? AND status='done' AND sha256 IS NOT NULL AND size > 0 "
        "GROUP BY sha256 HAVING COUNT(*) > 1 ORDER BY MAX(size) * (COUNT(*) - 1) DESC", (run,)).fetchall()
    with state.tx():
        db.executemany("INSERT INTO temp.dupg VALUES(?, ?)", [(g["sha256"], i) for i, g in enumerate(groups, 1)])

    sg = _Sheet(wb, "Duplicate groups", ["Group #", "Copies", "Size each (MB)", "Wasted GB", "File name",
                                         "Example path"], [9, 8, 14, 11, 35, 90], max_rows)
    for i, g in enumerate(groups, start=1):
        if not sg.add([i, g["n"], _mb(g["size"]), _gb(g["size"] * (g["n"] - 1)), g["name"], g["example"]]):
            break

    sf = _Sheet(wb, "Duplicate files", ["Group #", "Path", "Folder", "Size (MB)", "Created", "Modified",
                                        "Age group", "Sensitivity"], [9, 90, 60, 11, 12, 12, 16, 12], max_rows)
    for r in db.execute("SELECT d.grp, f.path, f.dir, f.size, f.created, f.modified, c.sensitivity "
                        "FROM files f JOIN temp.dupg d ON d.sha256 = f.sha256 "
                        "LEFT JOIN content c ON c.path = f.path WHERE f.seen_run=? AND f.status='done' "
                        "ORDER BY d.grp, f.path", (run,)):
        if not sf.add([r["grp"], r["path"], r["dir"], _mb(r["size"]), _date(r["created"]),
                       _date(r["modified"]), age_group(r["modified"], cutoffs), r["sensitivity"]]):
            break
    copies = sum(g["n"] - 1 for g in groups)
    wasted = sum(g["size"] * (g["n"] - 1) for g in groups)
    return [sg, sf], len(groups), copies, wasted


def _sheet_risk(wb, st, max_rows):
    rank = {"HIGH": 0, "MEDIUM": 1}
    st.risk.sort(key=lambda x: (rank[x[1]], x[5] or dt.date.min))
    sh = _Sheet(wb, "Stale sensitive", ["Path", "Sensitivity", "Data found", "Has credentials", "Topics",
                                        "Modified", "Age (years)", "Age group", "GB", "Duplicate copies"],
                [90, 11, 40, 11, 25, 12, 10, 16, 8, 10], max_rows)
    for row in st.risk:
        if not sh.add(list(row)):
            break
    return sh


def _sheet_types(wb, st, max_rows):
    sh = _Sheet(wb, "File types", ["Type group", "Extension", "Files", "GB", "% of total GB", "Avg size (MB)",
                                   "% stale (3y+)", "Oldest modified", "Newest modified"],
                [18, 12, 10, 10, 12, 12, 11, 14, 14], max_rows)

    def figures(d):
        return [d["files"], _gb(d["bytes"]), round(100 * d["bytes"] / max(1, st.n_bytes), 1),
                _mb(d["bytes"] / max(1, d["files"])), round(100 * d["stale"] / d["files"], 1)]

    for name, t in sorted(st.types.items(), key=lambda kv: -kv[1]["bytes"]):
        sh.add([name, None, *figures(t), _date(t["oldest"]), _date(t["newest"])])
    for ext, e in sorted(st.exts.items(), key=lambda kv: -kv[1]["bytes"])[:50]:
        sh.add([None, ext, *figures(e), None, None])
    return sh


def _sheet_largest(wb, st, max_rows):
    sh = _Sheet(wb, "Largest files", ["Path", "Size (MB)", "Type", "Modified", "Age group", "Sensitivity"],
                [90, 11, 14, 12, 16, 12], max_rows)
    for size, path, dtype, mod, group, sens in sorted(st.largest, reverse=True):
        sh.add([path, _mb(size), dtype, _date(mod), group, sens])
    return sh


def _sheet_growth(wb, st, max_rows):
    sh = _Sheet(wb, "Growth by year", ["Year", "Files created", "GB created", "Files last modified",
                                       "GB last modified"], [8, 14, 12, 18, 16], max_rows)
    for y in sorted(set(st.created_year) | set(st.modified_year)):
        sh.add([y, st.created_year[y]["files"], _gb(st.created_year[y]["bytes"]),
                st.modified_year[y]["files"], _gb(st.modified_year[y]["bytes"])])
    return sh


# ---- main ----------------------------------------------------------------------
def build_report(state, cfg, as_of=None, max_rows=EXCEL_MAX_ROWS, out_path=None) -> str:
    as_of = as_of or dt.datetime.now().astimezone()   # local date: what the user expects to see
    roots = sorted((s.root for s in cfg.sources), key=len, reverse=True)
    st = _Stats(as_of, roots)
    for r in state.db.execute(_FILES, (state.run_id,)):
        st.add(r)

    wb = Workbook(write_only=True)
    summary_ws = wb.create_sheet("Summary")
    sheets = [_sheet_folders(wb, st, max_rows)]
    dup_sheets, n_groups, copies, wasted = _sheets_duplicates(wb, state, st.cutoffs, max_rows)
    sheets += dup_sheets
    sheets += [_sheet_risk(wb, st, max_rows), _sheet_types(wb, st, max_rows),
               _sheet_largest(wb, st, max_rows), _sheet_growth(wb, st, max_rows)]
    for s in sheets:
        s.close()

    _summary(summary_ws, state, cfg, st, (n_groups, copies, wasted),
             [s.title for s in sheets if s.truncated], max_rows)

    out_path = out_path or os.path.join(cfg.output_dir, f"report_{as_of.date().isoformat()}.xlsx")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    wb.save(tmp)
    os.replace(tmp, out_path)
    return out_path


# ---- summary sheet -------------------------------------------------------------
_SHEET_HELP = [
    ("Age by folder", f"each folder {FOLDER_DEPTH} levels below the share root: files and GB per age group"),
    ("Duplicate groups", "sets of identical files (same SHA-256), with reclaimable space"),
    ("Duplicate files", "every file that belongs to a duplicate group"),
    ("Stale sensitive", "HIGH/MEDIUM sensitivity files not modified for 1 year or more"),
    ("File types", "GB and staleness by type group, then the top 50 extensions"),
    ("Largest files", "the 100 biggest files"),
    ("Growth by year", "files and GB by year created and year last modified"),
]


def _summary(ws, state, cfg, st, dups, truncated, max_rows):
    n_groups, dup_copies, dup_wasted = dups
    ws.column_dimensions["A"].width = 34
    for col in "BCDEFGH":
        ws.column_dimensions[col].width = 16

    def cell(v, font=None):
        c = WriteOnlyCell(ws, value=v)
        if font:
            c.font = font
        return c

    def title(text):
        ws.append([])
        ws.append([cell(text, _SECTION_FONT)])

    def header(values):
        row = [cell(v, _HEADER_FONT) for v in values]
        for c in row:
            c.fill = _HEADER_FILL
        ws.append(row)

    c, cc = state.counts(), state.content_counts()
    pending = c["files_pending"] + cc["pending"]
    status = ("COMPLETE" if pending == 0 else
              f"PARTIAL: {c['files_pending']:,} files not yet typed, {cc['pending']:,} not yet read for content")

    ws.append([cell("File inventory report", _TITLE_FONT)])
    ws.append([f"As of {st.as_of.date().isoformat()} (age = time since last modified)"])
    ws.append(["Sources: " + "; ".join(f"{s.name} = {s.root}" for s in cfg.sources)])
    ws.append([cell(f"Scan status: {status}", None if pending == 0 else _WARN_FONT)])
    ws.append([f"Files in report: {st.n_files:,}  |  {_gb(st.n_bytes):,} GB  |  "
               f"Excluded by config: {c['files_skipped']:,}"])

    title("Age groups")
    header(["Age group", "Meaning", "Files", "% of files", "GB", "% of GB"])
    defs = dict(AGE_GROUPS)
    for g in GROUP_NAMES + (["Unknown"] if st.totals["Unknown"]["files"] else []):
        t = st.totals[g]
        ws.append([g, defs.get(g, "no modified date"), t["files"],
                   round(100 * t["files"] / max(1, st.n_files), 1),
                   _gb(t["bytes"]), round(100 * t["bytes"] / max(1, st.n_bytes), 1)])

    title("Sensitivity by age group (files)")
    header(["Sensitivity"] + GROUP_NAMES + ["Total"])
    for level in SENSITIVITY_ORDER + ["Not scanned"]:
        if level in st.matrix:
            counts = [st.matrix[level].get(g, 0) for g in GROUP_NAMES]
            ws.append([level] + counts + [sum(counts)])

    title("Duplicates")
    ws.append(["Duplicate groups", n_groups])
    ws.append(["Redundant copies", dup_copies])
    ws.append(["Wasted GB (reclaimable)", _gb(dup_wasted)])

    title("Checks")
    if access_time_recorded(st.n_files, st.n_accessed_after):
        ws.append([f"Last-accessed dates look recorded by the server ({st.n_accessed_after:,} files were "
                   "opened after their last change). They are shown in the inventory but not used for age."])
    else:
        ws.append(["Last-accessed dates do NOT look recorded by the server (fewer than 1% of files were opened "
                   "after their last change). Don't use last-accessed for decisions."])
    ws.append(["Created dates reset when files are copied or restored; 'Growth by year' also shows the "
               "last-modified year for that reason."])
    for t in truncated:
        ws.append([cell(f"Sheet '{t}' was truncated at {max_rows:,} rows (Excel limit); biggest items first. "
                        "Use inventory.parquet for the full list.", _WARN_FONT)])

    title("Sheets")
    for name, text in _SHEET_HELP:
        ws.append([name, text])
