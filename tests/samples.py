"""Generate small sample files for extractor tests. All values are fake."""
import io
import zipfile
from email.message import EmailMessage

from fscan.pii import validators as v

AADHAAR = "4991 8765 432" + v.verhoeff_check_digit("49918765432")
PAN = "ABCPE1234F"
EMAIL = "ramesh.kumar@example.com"
CARD = "4111 1111 1111 1111"


def docx(paragraphs):
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<w:document {ns}><w:body>{body}</w:body></w:document>')
        z.writestr("word/footer1.xml", f'<w:ftr {ns}><w:p><w:r><w:t>Confidential</w:t></w:r></w:p></w:ftr>')
    return buf.getvalue()


def pptx(slides):
    ns = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        for i, text in enumerate(slides, start=1):
            z.writestr(f"ppt/slides/slide{i}.xml", f"<p:sld {ns} xmlns:p='p'><a:t>{text}</a:t></p:sld>")
    return buf.getvalue()


def xlsx(rows, sheet="Staff"):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def text_pdf(lines):
    content = "BT /F1 12 Tf 72 720 Td " + " ".join(f"({l}) Tj 0 -16 Td" for l in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def text_image(lines, fmt="PNG"):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1400, 120 + 70 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("arial.ttf", 44)
    for i, line in enumerate(lines):
        draw.text((60, 60 + 70 * i), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, fmt, resolution=200)
    return buf.getvalue()


def zip_of(files: dict, encrypted=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    raw = bytearray(buf.getvalue())
    for name in encrypted:  # zipfile can't write encrypted members: set the flag bit by hand
        n = name.encode()
        for sig, flag_at, name_at in ((b"PK\x03\x04", 6, 30), (b"PK\x01\x02", 8, 46)):
            i = raw.find(sig)
            while i != -1:
                if raw[i + name_at:i + name_at + len(n)] == n:
                    raw[i + flag_at] |= 0x1
                i = raw.find(sig, i + 4)
    return bytes(raw)


def eml(body, attachments: dict):
    m = EmailMessage()
    m["From"] = "HR Team <hr@example.com>"
    m["To"] = EMAIL
    m["Subject"] = "Your offer letter"
    m.set_content(body)
    for name, data in attachments.items():
        m.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    return m.as_bytes()
