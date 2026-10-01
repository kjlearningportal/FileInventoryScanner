"""State DB → inventory / findings (CSV + Parquet), errors.csv, summary.json.

Safe to run at any time, including mid-scan, for a partial view.
Findings contain masked values only; the state DB never holds raw values.
"""
import csv
import datetime as dt
import json
import os
from collections import Counter

import pyarrow as pa
import pyarrow.parquet as pq

_QUERY = """
SELECT f.source, f.path, f.dir, f.name, f.ext, f.size, f.created, f.modified, f.accessed, f.attributes,
       f.detected_type, f.type_group, f.mime, f.ext_mismatch, f.quick_fp, f.sha256, f.status, f.error,
       CASE WHEN f.sha256 IS NULL OR f.size = 0 THEN 1
            ELSE COUNT(*) OVER (PARTITION BY f.sha256) END AS dup_count,
       c.status AS c_status, c.content_status, c.sensitivity, c.pii_types, c.pii_counts, c.has_credentials,
       c.topics, c.topic_scores, c.text_chars, c.pages, c.ocr_pages, c.note, c.error AS c_error
FROM files f LEFT JOIN content c ON c.path = f.path
WHERE f.seen_run = ?
ORDER BY f.path
"""

_FINDINGS = """
SELECT fi.path, fi.inner_path, fi.data_type, fi.masked_value, fi.confidence, fi.occurrences,
       fi.first_location, c.sensitivity
FROM findings fi JOIN files f ON f.path = fi.path LEFT JOIN content c ON c.path = fi.path
WHERE f.seen_run = ?
ORDER BY fi.path, fi.inner_path, fi.data_type
"""

_TS = pa.timestamp("s", tz="UTC")
_S, _I, _B = pa.string(), pa.int64(), pa.bool_()
_SCHEMA = pa.schema([
    ("source", _S), ("path", _S), ("folder", _S), ("name", _S), ("extension", _S), ("size_bytes", _I),
    ("created_utc", _TS), ("modified_utc", _TS), ("accessed_utc_prescan", _TS),
    ("is_hidden", _B), ("is_system", _B), ("is_readonly", _B),
    ("detected_type", _S), ("type_group", _S), ("mime", _S), ("ext_mismatch", _B),
    ("quick_fp", _S), ("sha256", _S), ("dup_count", _I), ("status", _S), ("error", _S),
    ("content_status", _S), ("sensitivity", _S), ("pii_types", _S), ("pii_counts", _S),
    ("has_credentials", _B), ("topics", _S), ("topic_scores", _S),
    ("text_chars", _I), ("pages", _I), ("ocr_pages", _I), ("content_note", _S),
])
COLUMNS = _SCHEMA.names

_FINDINGS_SCHEMA = pa.schema([
    ("path", _S), ("inner_path", _S), ("data_type", _S), ("masked_value", _S), ("confidence", _S),
    ("occurrences", _I), ("first_location", _S), ("file_sensitivity", _S),
])
FINDINGS_COLUMNS = _FINDINGS_SCHEMA.names


def _ts(epoch):
    if epoch is None:
        return None
    return dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).replace(microsecond=0)


def _content_status(r):
    if r["c_status"] is None:
        return "not_applicable" if r["status"] == "done" else None
    if r["c_status"] == "done":
        return r["content_status"]
    return r["c_status"]  # pending | error


def _record(r) -> dict:
    attrs = r["attributes"] or 0
    return {
        "source": r["source"], "path": r["path"], "folder": r["dir"], "name": r["name"],
        "extension": r["ext"], "size_bytes": r["size"],
        "created_utc": _ts(r["created"]), "modified_utc": _ts(r["modified"]),
        "accessed_utc_prescan": _ts(r["accessed"]),
        "is_hidden": bool(attrs & 0x2), "is_system": bool(attrs & 0x4), "is_readonly": bool(attrs & 0x1),
        "detected_type": r["detected_type"], "type_group": r["type_group"], "mime": r["mime"],
        "ext_mismatch": None if r["ext_mismatch"] is None else bool(r["ext_mismatch"]),
        "quick_fp": r["quick_fp"], "sha256": r["sha256"], "dup_count": r["dup_count"],
        "status": r["status"], "error": r["error"] or r["c_error"],
        "content_status": _content_status(r), "sensitivity": r["sensitivity"],
        "pii_types": r["pii_types"], "pii_counts": r["pii_counts"],
        "has_credentials": None if r["has_credentials"] is None else bool(r["has_credentials"]),
        "topics": r["topics"], "topic_scores": r["topic_scores"],
        "text_chars": r["text_chars"], "pages": r["pages"], "ocr_pages": r["ocr_pages"],
        "content_note": r["note"],
    }


def _csv_value(v):
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return "" if v is None else v


def _write_table(cursor, columns, schema, to_record, csv_path, parquet_path, batch, on_record=None):
    n = 0
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fcsv, pq.ParquetWriter(parquet_path, schema) as pw:
        w = csv.writer(fcsv)
        w.writerow(columns)
        while True:
            rows = cursor.fetchmany(batch)
            if not rows:
                break
            recs = [to_record(r) for r in rows]
            for rec in recs:
                w.writerow([_csv_value(rec[c]) for c in columns])
                if on_record:
                    on_record(rec)
            pw.write_table(pa.Table.from_pylist(recs, schema=schema))
            n += len(recs)
    return n


def export(state, out_dir: str, batch: int = 50_000) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    paths = {k: os.path.join(out_dir, f) for k, f in [
        ("csv", "inventory.csv"), ("parquet", "inventory.parquet"),
        ("findings_csv", "findings.csv"), ("findings_parquet", "findings.parquet"),
        ("errors", "errors.csv"), ("summary", "summary.json")]}
    tmp = {k: p + ".tmp" for k, p in paths.items()}

    groups, levels, topic_counts = {}, Counter(), Counter()

    def tally(rec):
        g = groups.setdefault(rec["type_group"] or rec["status"], [0, 0])
        g[0] += 1
        g[1] += rec["size_bytes"]
        if rec["sensitivity"]:
            levels[rec["sensitivity"]] += 1
        for t in (rec["topics"] or "").split(";"):
            if t:
                topic_counts[t] += 1

    n = _write_table(state.db.execute(_QUERY, (state.run_id,)), COLUMNS, _SCHEMA, _record,
                     tmp["csv"], tmp["parquet"], batch, tally)

    files_by_type = Counter()

    def finding_record(r):
        return {"path": r["path"], "inner_path": r["inner_path"], "data_type": r["data_type"],
                "masked_value": r["masked_value"], "confidence": r["confidence"],
                "occurrences": r["occurrences"], "first_location": r["first_location"],
                "file_sensitivity": r["sensitivity"]}

    n_findings = _write_table(state.db.execute(_FINDINGS, (state.run_id,)), FINDINGS_COLUMNS,
                              _FINDINGS_SCHEMA, finding_record, tmp["findings_csv"], tmp["findings_parquet"], batch)
    for r in state.db.execute("SELECT data_type, COUNT(DISTINCT fi.path) n FROM findings fi "
                              "JOIN files f ON f.path = fi.path WHERE f.seen_run=? GROUP BY data_type",
                              (state.run_id,)):
        files_by_type[r["data_type"]] = r["n"]

    with open(tmp["errors"], "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["kind", "path", "attempts", "error"])
        for r in state.db.execute(
                "SELECT 'folder' k, path, attempts, error FROM dirs WHERE status='error' "
                "UNION ALL SELECT 'file', path, attempts, error FROM files WHERE status='error' AND seen_run=? "
                "UNION ALL SELECT 'content', c.path, c.attempts, c.error FROM content c JOIN files f "
                "ON f.path = c.path WHERE c.status='error' AND f.seen_run=?", (state.run_id, state.run_id)):
            w.writerow([r["k"], r["path"], r["attempts"], r["error"]])

    summary = {
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": n,
        "findings_rows": n_findings,
        "files_by_sensitivity": dict(levels.most_common()),
        "files_with_data_type": dict(files_by_type.most_common()),
        "files_by_topic": dict(topic_counts.most_common()),
        "by_type_group": {k: {"files": v[0], "bytes": v[1]}
                          for k, v in sorted(groups.items(), key=lambda kv: -kv[1][1])},
        "counts": state.counts(),
        "content_counts": state.content_counts(),
    }
    with open(tmp["summary"], "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    for k in paths:
        os.replace(tmp[k], paths[k])
    return {"rows": n, "findings": n_findings, **paths}
