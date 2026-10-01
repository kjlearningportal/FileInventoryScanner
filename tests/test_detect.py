import io
import zipfile

from fscan.detect import detect


def _zip(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return buf.getvalue()


def test_pdf():
    d = detect(b"%PDF-1.7\n...", b"", ".pdf", 100)
    assert (d.type, d.group, d.ext_mismatch) == ("pdf", "document", False)


def test_png_and_jpeg():
    assert detect(b"\x89PNG\r\n\x1a\n....", b"", ".png", 50).type == "png"
    assert detect(b"\xff\xd8\xff\xe0....", b"", ".jpg", 50).group == "image"


def test_docx_xlsx_pptx_from_zip_contents():
    docx = _zip(["[Content_Types].xml", "word/document.xml"])
    xlsx = _zip(["[Content_Types].xml", "xl/workbook.xml"])
    pptx = _zip(["[Content_Types].xml", "ppt/presentation.xml"])
    assert detect(docx, b"", ".docx", len(docx)).type == "docx"
    assert detect(xlsx, b"", ".xlsx", len(xlsx)).group == "spreadsheet"
    assert detect(pptx, b"", ".pptx", len(pptx)).group == "presentation"


def test_plain_zip():
    z = _zip(["a.txt"])
    d = detect(z, b"", ".zip", len(z))
    assert (d.type, d.group, d.ext_mismatch) == ("zip", "archive", False)


def test_exe_disguised_as_pdf_is_flagged():
    d = detect(b"MZ\x90\x00" + b"\x00" * 60, b"", ".pdf", 64)
    assert d.group == "executable"
    assert d.ext_mismatch is True


def test_text_files_use_extension_for_subtype():
    d = detect(b"id,name\n1,a\n", b"", ".csv", 12)
    assert (d.type, d.group, d.ext_mismatch) == ("csv", "data", False)
    assert detect(b"SELECT 1;\n", b"", ".sql", 10).group == "code"
    assert detect(b"just words\n", b"", ".weird", 11).type == "text"


def test_text_pretending_to_be_docx_is_flagged():
    d = detect(b"hello world\n", b"", ".docx", 12)
    assert d.group == "text" and d.ext_mismatch is True


def test_empty_and_unknown():
    assert detect(b"", b"", ".txt", 0).type == "empty"
    d = detect(b"\x00\x13\x99\xfe" * 100, b"", ".dat", 400)
    assert (d.type, d.group, d.ext_mismatch) == ("unknown", "unknown", False)


def test_utf16_text_is_text():
    raw = "hello, world\n".encode("utf-16")
    assert detect(raw, b"", ".txt", len(raw)).group == "text"


def test_static_library():
    assert detect(b"!<arch>\n/               0", b"", ".lib", 30).type == "static_lib"
