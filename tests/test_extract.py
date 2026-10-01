import io
import threading
from types import SimpleNamespace

import pytest

from fscan.extract import Context, Interrupted, Unreadable, extract, is_readable
from fscan.extract.mail import msg_object_chunks
from fscan.extract.ocr import Ocr, find_tesseract
from fscan.pii.patterns import scan_text
from tests import samples as s

TESS = find_tesseract()
needs_ocr = pytest.mark.skipif(TESS is None, reason="Tesseract not installed")


def run(data: bytes, name: str, ctx=None, **kw):
    ctx = ctx or Context()
    chunks = list(extract(io.BytesIO(data), name, ctx, **kw))
    return chunks, ctx


def findings(chunks):
    out = []
    for c in chunks:
        for h in scan_text(c.text):
            out.append((h.type, c.inner_path, c.locate(h.start)))
    return out


def test_plain_text_line_numbers():
    chunks, _ = run(b"hello\nworld\nPAN ABCPE1234F\n", "a.txt")
    assert ("PAN", "", "line 3") in findings(chunks)


def test_csv_uses_header_as_context_and_cell_location():
    data = f"Name,Aadhaar No,Account Number\nRamesh,{s.AADHAAR.replace(' ', '')},50100123456789\n".encode()
    chunks, _ = run(data, "staff.csv")
    f = findings(chunks)
    assert ("AADHAAR", "", "row 2, Aadhaar No") in f
    assert ("BANK_ACCOUNT", "", "row 2, Account Number") in f


def test_docx_paragraphs_and_footer():
    chunks, _ = run(s.docx(["Offer letter", f"PAN: {s.PAN}", f"Email {s.EMAIL}"]), "offer.docx")
    f = findings(chunks)
    assert ("PAN", "", "paragraph 2") in f and ("EMAIL", "", "paragraph 3") in f
    assert any("Confidential" in c.text for c in chunks)


def test_xlsx_numbers_stored_as_floats_are_found():
    aadhaar_digits = int(s.AADHAAR.replace(" ", ""))
    data = s.xlsx([["Employee", "Aadhaar"], ["Ramesh", float(aadhaar_digits)]])
    chunks, _ = run(data, "staff.xlsx")
    assert ("AADHAAR", "", "Staff!B2") in findings(chunks)


def test_pptx_slides():
    chunks, ctx = run(s.pptx(["Agenda", f"Contact {s.EMAIL}"]), "deck.pptx")
    assert ("EMAIL", "", "slide 2") in findings(chunks)
    assert ctx.pages == 2


def test_pdf_text_layer():
    chunks, ctx = run(s.text_pdf(["Statement", f"PAN {s.PAN}"]), "stmt.pdf")
    assert ("PAN", "", "page 1") in findings(chunks)
    assert ctx.ocr_pages == 0


@needs_ocr
def test_scanned_pdf_is_ocrd():
    from PIL import Image
    img = Image.open(io.BytesIO(s.text_image([f"PAN: {s.PAN}", f"Email: {s.EMAIL}"])))
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=200)  # image-only PDF, like a scanner makes
    chunks, ctx = run(buf.getvalue(), "scan.pdf", Context(ocr=Ocr(TESS)))
    types = {t for t, _, _ in findings(chunks)}
    assert "PAN" in types and ctx.ocr_pages == 1
    assert any(loc == "page 1 (OCR)" for _, _, loc in findings(chunks))


def test_scanned_pdf_without_ocr_is_flagged():
    from PIL import Image
    img = Image.open(io.BytesIO(s.text_image(["PAN: ABCPE1234F"])))
    buf = io.BytesIO()
    img.save(buf, "PDF")
    _, ctx = run(buf.getvalue(), "scan.pdf", Context(ocr=None))
    assert "needs_ocr" in ctx.notes and ctx.partial


@needs_ocr
def test_image_ocr():
    chunks, ctx = run(s.text_image([f"Card {s.CARD}"]), "photo.png", Context(ocr=Ocr(TESS)))
    assert "CARD" in {t for t, _, _ in findings(chunks)}


def test_email_with_nested_zip_attachment():
    inner_zip = s.zip_of({"hr/salary.csv": f"Name,PAN\nRamesh,{s.PAN}\n".encode()})
    data = s.eml("Please find attached.", {"docs.zip": inner_zip})
    chunks, _ = run(data, "offer.eml")
    f = findings(chunks)
    assert ("EMAIL", "", "email header") in f
    assert ("PAN", "attachment: docs.zip > hr/salary.csv", "row 2, PAN") in f


def test_depth_limit():
    level3 = s.zip_of({"c.txt": f"PAN {s.PAN}".encode()})
    level2 = s.zip_of({"c.zip": level3})
    level1 = s.zip_of({"b.zip": level2})
    chunks, ctx = run(level1, "a.zip", Context(max_depth=2))
    assert "depth_limit" in ctx.notes and not findings(chunks)


def test_zip_bomb_is_stopped():
    bomb = s.zip_of({"zeros.bin": b"\x00" * (5 * 1024 * 1024), "ok.txt": f"PAN {s.PAN}".encode()})
    chunks, ctx = run(bomb, "bomb.zip", Context(bomb_min_size=1024 * 1024))
    assert "zip_bomb" in ctx.notes
    assert ("PAN", "ok.txt", "line 1") in findings(chunks)  # the rest is still read


def test_encrypted_zip_member_is_noted():
    data = s.zip_of({"secret.txt": b"x", "open.txt": f"PAN {s.PAN}".encode()}, encrypted={"secret.txt"})
    chunks, ctx = run(data, "mixed.zip")
    assert "encrypted_member" in ctx.notes and ctx.partial
    assert findings(chunks)


def test_gzip_single_file():
    import gzip
    chunks, _ = run(gzip.compress(f"PAN {s.PAN}\n".encode()), "dump.txt.gz")
    assert ("PAN", "dump.txt", "line 1") in findings(chunks)


def test_msg_object_with_attachment_and_embedded_email():
    embedded = SimpleNamespace(sender="a@b.com", to=None, cc=None, subject="fwd", body=f"PAN {s.PAN}",
                               attachments=[])
    msg = SimpleNamespace(sender="HR <hr@example.com>", to=s.EMAIL, cc=None, subject="Offer",
                          body="see attached", htmlBody=None,
                          attachments=[SimpleNamespace(longFilename="ids.txt", data=f"SSN 536-22-1234".encode()),
                                       SimpleNamespace(longFilename="fwd.msg", data=embedded)])

    def recurse(fp, name, inner):
        yield from extract(fp, name, Context(), inner, 1)
    f = findings(msg_object_chunks(msg, Context(), "", recurse))
    assert ("US_SSN", "attachment: ids.txt", "line 1") in f
    assert ("PAN", "attachment: fwd.msg", "email body") in f


def test_pst_without_reader_is_unreadable():
    with pytest.raises(Unreadable, match="pst_reader_unavailable"):
        run(b"!BDN" + b"\x00" * 100, "mailbox.pst")


def test_stop_request_interrupts():
    ctx = Context(stop=threading.Event())
    ctx.stop.set()
    with pytest.raises(Interrupted):
        run(b"x\n" * 10, "a.txt", ctx)


def test_readable_types():
    assert is_readable("pdf", "document") and is_readable("python", "code")
    assert not is_readable("exe", "executable") and not is_readable("mp4", "video")
