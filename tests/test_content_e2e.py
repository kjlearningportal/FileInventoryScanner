"""End-to-end: planted fake sensitive data → findings, levels, topics; no raw value leaks."""
import csv
import io
import os

import pytest

from fscan.config import build_config
from fscan.engine import Scanner
from fscan.extract.ocr import find_tesseract
from fscan.sources.filesystem import FileSystemSource
from fscan.state import State
from tests import samples as s

SSN = "536-22-1234"
PASSWORD = "Welcome@123"
RAW_VALUES = [s.PAN, s.AADHAAR, s.AADHAAR.replace(" ", ""), s.CARD, s.CARD.replace(" ", ""),
              SSN, PASSWORD, "50100123456789"]


def _plant(root):
    files = {
        r"HR/Payroll/salary_2025.csv":
            f"Name,Aadhaar No,Account Number,Salary\nRamesh,{s.AADHAAR.replace(' ', '')},50100123456789,85000\n".encode(),
        r"HR/offer_letter.docx": s.docx(["Offer letter", "Dear Ramesh,", f"PAN: {s.PAN}", "Salary and CTC details"]),
        r"Mail/offer.eml": s.eml("Please find the payslip attached.",
                                 {"docs.zip": s.zip_of({"payslip.txt": f"SSN {SSN}\nemployee payslip\n".encode()})}),
        r"IT/app.config": f"Server=db1;Database=hr;User Id=sa;Password={PASSWORD};\n".encode(),
        r"Finance/invoice_march.txt": b"Invoice 2231 for March. GST and TDS as per ledger.\n",
        r"Misc/readme.txt": b"nothing to see here\n",
        r"Misc/app.exe": b"MZ" + b"\x00" * 200,
    }
    if find_tesseract():
        files[r"Scans/card_photo.png"] = s.text_image([f"Card {s.CARD}"])
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def _cfg(tmp_path, **content):
    root = tmp_path / "share"
    root.mkdir()
    _plant(root)
    return build_config({"sources": [{"name": "share", "root": str(root)}],
                         "output_dir": str(tmp_path / "out"), "workers": 2, "list_workers": 2,
                         "max_read_mb_per_sec": 0, "batch_size": 2, "batch_seconds": 0.01, "sample_size": 2,
                         "content": {"workers": 2, "sample_size": 2, **content}})


def _scanner(cfg, **kw):
    state = State(cfg.state_db)
    sources = {x.name: FileSystemSource(x.name, x.root) for x in cfg.sources}
    return Scanner(cfg, state, sources, stream=io.StringIO(), **kw), state


def _rows(cfg, name):
    with open(os.path.join(cfg.output_dir, name), encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _by_name(rows):
    return {os.path.basename(r["path"]): r for r in rows}


def test_findings_levels_and_topics(tmp_path):
    cfg = _cfg(tmp_path)
    scanner, state = _scanner(cfg)
    assert scanner.run(assume_yes=True) == "complete"
    out = scanner.stream.getvalue()
    assert "Sensitive data:" in out and "TOTAL" in out

    inv = _by_name(_rows(cfg, "inventory.csv"))
    assert inv["salary_2025.csv"]["sensitivity"] == "HIGH"
    assert "AADHAAR" in inv["salary_2025.csv"]["pii_types"]
    assert inv["salary_2025.csv"]["topics"].startswith("HR/Payroll")
    assert inv["offer_letter.docx"]["sensitivity"] == "MEDIUM"          # one PAN
    assert inv["offer.eml"]["sensitivity"] == "HIGH"                    # SSN inside zip inside email
    assert inv["app.config"]["has_credentials"] == "True"
    assert inv["invoice_march.txt"]["topics"] == "Finance/Accounts"
    assert inv["readme.txt"]["sensitivity"] == "NONE"
    assert inv["app.exe"]["content_status"] == "not_applicable"

    findings = _rows(cfg, "findings.csv")
    loc = {(os.path.basename(r["path"]), r["inner_path"], r["data_type"]): r for r in findings}
    assert loc[("salary_2025.csv", "", "AADHAAR")]["first_location"] == "row 2, Aadhaar No"
    assert loc[("salary_2025.csv", "", "BANK_ACCOUNT")]["masked_value"] == "XXXXXXXX6789"
    assert loc[("offer_letter.docx", "", "PAN")]["masked_value"] == "ABCP*****F"
    assert ("offer.eml", "attachment: docs.zip > payslip.txt", "US_SSN") in loc
    assert loc[("app.config", "", "DB_CONNECTION")]["masked_value"] == "[connection string]"
    if find_tesseract():
        assert inv["card_photo.png"]["sensitivity"] == "HIGH"
        assert inv["card_photo.png"]["ocr_pages"] == "1"


def test_no_raw_value_is_written_anywhere(tmp_path):
    cfg = _cfg(tmp_path)
    scanner, state = _scanner(cfg)
    assert scanner.run(assume_yes=True) == "complete"
    state.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    state.close()
    leaks = []
    for dirpath, _, names in os.walk(cfg.output_dir):
        for n in names:
            data = open(os.path.join(dirpath, n), "rb").read()
            for raw in RAW_VALUES:
                for enc in ("utf-8", "utf-16-le"):
                    if raw.encode(enc) in data:
                        leaks.append((n, raw, enc))
    assert leaks == []


def test_content_stage_resumes_after_interrupt(tmp_path):
    cfg = _cfg(tmp_path, workers=1)
    for i in range(40):
        (tmp_path / "share" / "Misc" / f"note{i}.txt").write_bytes(f"note {i} mail a{i}@example.com\n".encode())
    scanner, state = _scanner(cfg)

    def stop_in_content():
        if scanner.stage == "content" and state.content_counts()["done"] >= 5:
            scanner.stop.set()
    scanner.after_batch = stop_in_content
    assert scanner.run(assume_yes=True) == "stopped"
    first = {r["path"]: r["processed"] for r in state.db.execute(
        "SELECT path, processed FROM content WHERE status='done'")}
    pending = state.content_counts()["pending"]
    assert first and pending > 0
    state.close()

    scanner, state = _scanner(cfg, confirm=lambda _: pytest.fail("must not ask again"))
    assert scanner.run() == "complete"
    after = {r["path"]: r["processed"] for r in state.db.execute(
        "SELECT path, processed FROM content WHERE status='done'")}
    assert all(after[p] == t for p, t in first.items())   # not re-read
    assert state.content_counts()["pending"] == 0


def test_content_can_be_disabled(tmp_path):
    cfg = _cfg(tmp_path, enabled=False)
    scanner, state = _scanner(cfg)
    assert scanner.run(assume_yes=True) == "complete"
    assert state.content_counts()["done"] == 0


def test_rescan_content_applies_edited_rules(tmp_path):
    import shutil
    from fscan.config import PROJECT_RULES
    rules = tmp_path / "rules"
    shutil.copytree(PROJECT_RULES, rules)
    cfg = _cfg(tmp_path, rules_dir=str(rules))
    scanner, state = _scanner(cfg)
    assert scanner.run(assume_yes=True) == "complete"
    assert _by_name(_rows(cfg, "inventory.csv"))["readme.txt"]["topics"] == "unclassified"
    state.close()

    # User adds a topic, then asks for content to be re-read
    (rules / "topics.yaml").write_text((rules / "topics.yaml").read_text() +
                                       "  Misc/Docs: [readme, misc, nothing to see]\n")  # path 3+3, content 1
    from fscan.cli import main
    import yaml
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({
        "sources": [{"name": "share", "root": str(tmp_path / "share")}], "output_dir": str(tmp_path / "out"),
        "max_read_mb_per_sec": 0, "content": {"workers": 2, "rules_dir": str(rules)}}))
    assert main(["rescan-content", "-c", str(cfg_file), "--yes"]) == 0
    assert _by_name(_rows(cfg, "inventory.csv"))["readme.txt"]["topics"] == "Misc/Docs"
