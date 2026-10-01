"""PDF text layer via PDFium; pages without text (scans) go to OCR."""
from .base import Chunk, Unreadable

MIN_TEXT = 25     # fewer characters than this on a page with images = scanned page
OCR_DPI = 300


def pdf_chunks(f, ctx, inner):
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c
    try:
        doc = pdfium.PdfDocument(f)
    except pdfium.PdfiumError as e:
        msg = str(e).lower()
        raise Unreadable("encrypted" if "password" in msg else f"corrupt: {e}")
    try:
        ctx.pages += len(doc)
        for i in range(len(doc)):
            ctx.check_stop()
            page = doc[i]
            try:
                tp = page.get_textpage()
                text = tp.get_text_range()
                tp.close()
                where = f"page {i + 1}"
                if len(text.strip()) < MIN_TEXT and _has_images(page, pdfium_c):
                    if ctx.ocr is None:
                        ctx.note("needs_ocr")
                    else:
                        bitmap = page.render(scale=OCR_DPI / 72)
                        text = text + "\n" + ctx.ocr.read(bitmap.to_pil())
                        ctx.ocr_pages += 1
                        where = f"page {i + 1} (OCR)"
                if text.strip():
                    yield Chunk(text, inner, lambda pos, w=where: w)
            finally:
                page.close()
    finally:
        doc.close()


def _has_images(page, pdfium_c) -> bool:
    return any(True for _ in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=2))
