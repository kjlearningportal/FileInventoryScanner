"""Text extraction dispatcher: picks an extractor from the file's real type.

extract() yields Chunk(text, inner_path, locate) for a file and everything nested in
it (attachments, archive members, mailbox items), up to ctx.max_depth levels.
"""
import os

from ..detect import detect
from .archive import compressed_chunks, tar_chunks, zip_chunks
from .base import Chunk, Context, Interrupted, Unreadable, throttled  # noqa: F401
from .mail import eml_chunks, msg_chunks, pst_available, pst_chunks  # noqa: F401
from .office import docx_chunks, odf_chunks, ole_chunks, pptx_chunks, xls_chunks, xlsx_chunks
from .ocr import image_chunks
from .pdf import pdf_chunks
from .text import csv_chunks, rtf_chunks, text_chunks

TEXT_GROUPS = {"text", "data", "config", "log", "code", "web"}
IMAGE_TYPES = {"png", "jpeg", "gif", "tiff", "bmp", "webp"}

_SIMPLE = {
    "docx": docx_chunks, "xlsx": xlsx_chunks, "pptx": pptx_chunks, "xls": xls_chunks,
    "doc": ole_chunks, "ppt": ole_chunks, "ole": ole_chunks, "vsd": ole_chunks,
    "odt": odf_chunks, "ods": odf_chunks, "odp": odf_chunks, "epub": odf_chunks,
    "pdf": pdf_chunks, "rtf": rtf_chunks,
}
_NESTED = {"zip": zip_chunks, "tar": tar_chunks, "eml": eml_chunks, "msg": msg_chunks, "pst": pst_chunks}
_UNSUPPORTED = {"7z", "rar", "cab", "heic", "psd", "vsdx", "mbox"}

READABLE_TYPES = (set(_SIMPLE) | set(_NESTED) | IMAGE_TYPES | _UNSUPPORTED
                  | {"gzip", "bzip2", "xz", "csv", "tsv"})


def is_readable(detected_type, type_group) -> bool:
    return detected_type in READABLE_TYPES or type_group in TEXT_GROUPS


def extract(f, name, ctx, inner="", depth=0, detected_type=None, type_group=None):
    ext = os.path.splitext(name)[1].lower()
    if detected_type is None:
        detected_type, type_group = _sniff(f, ext)
    if ext in (".pst", ".ost"):
        detected_type = "pst"

    def recurse(fp, child_name, child_inner):
        if depth + 1 > ctx.max_depth:
            ctx.note("depth_limit")
            return
        try:
            yield from extract(fp, child_name, ctx, child_inner, depth + 1)
        except Unreadable as e:
            ctx.note(f"{e.note} ({child_name})")

    if detected_type in _SIMPLE:
        yield from _SIMPLE[detected_type](f, ctx, inner)
    elif detected_type in _NESTED:
        yield from _NESTED[detected_type](f, ctx, inner, recurse)
    elif detected_type in ("gzip", "bzip2", "xz"):
        yield from compressed_chunks(f, ctx, inner, recurse, detected_type, name)
    elif detected_type in IMAGE_TYPES:
        yield from image_chunks(f, ctx, inner)
    elif detected_type in ("csv", "tsv"):
        yield from csv_chunks(f, ctx, inner, "," if detected_type == "csv" else "\t")
    elif type_group in TEXT_GROUPS:
        yield from text_chunks(f, ctx, inner)
    elif detected_type in _UNSUPPORTED:
        raise Unreadable(f"unsupported_format: {detected_type}")
    # anything else (executables, video, fonts...) has no readable text: nothing to yield


def _sniff(f, ext):
    start = f.tell()
    head = f.read(64 * 1024)
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(max(start, size - 64 * 1024))
    tail = f.read()
    f.seek(start)
    d = detect(head, tail, ext, size - start)
    return d.type, d.group
