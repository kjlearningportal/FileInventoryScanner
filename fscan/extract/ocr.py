"""OCR with Tesseract, plus image extraction (photos, scans, screenshots)."""
import os
import shutil

from .base import Chunk, Unreadable

MAX_SIDE = 5000  # downscale huge images; text stays readable, OCR time stays bounded

_DEFAULT_PATHS = [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]


def find_tesseract(configured=None):
    for p in [configured] + _DEFAULT_PATHS + [shutil.which("tesseract")]:
        if p and os.path.isfile(p):
            return p
    return None


class Ocr:
    def __init__(self, cmd: str, lang: str = "eng", timeout: int = 180):
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = cmd
        self._pt = pytesseract
        self.lang, self.timeout = lang, timeout

    def read(self, image) -> str:
        img = image.convert("L")
        if max(img.size) > MAX_SIDE:
            img.thumbnail((MAX_SIDE, MAX_SIDE))
        try:
            return self._pt.image_to_string(img, lang=self.lang, timeout=self.timeout)
        except RuntimeError as e:  # pytesseract raises RuntimeError on timeout
            if "timeout" in str(e).lower():
                return ""
            raise


def image_chunks(f, ctx, inner):
    from PIL import Image, ImageSequence, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = 300_000_000
    try:
        img = Image.open(f)
    except (UnidentifiedImageError, OSError) as e:
        raise Unreadable(f"corrupt: {type(e).__name__}")
    with img:
        if min(img.size) < ctx.ocr_min_side:
            return  # icon / thumbnail
        if ctx.ocr is None:
            ctx.note("needs_ocr")
            return
        frames = list(ImageSequence.Iterator(img))
        for n, frame in enumerate(frames, start=1):
            ctx.check_stop()
            text = ctx.ocr.read(frame)
            ctx.ocr_pages += 1
            where = "image (OCR)" if len(frames) == 1 else f"frame {n} (OCR)"
            if text.strip():
                yield Chunk(text, inner, lambda pos, w=where: w)
