"""Plain text, CSV/TSV and RTF, streamed so any size fits in memory."""
import codecs
import csv
import io
import re

from .base import Chunk, LineLocator, OffsetLocator

BLOCK = 4 * 1024 * 1024
OVERLAP = 256
CSV_ROWS_PER_CHUNK = 5000
csv.field_size_limit(2**31 - 1)  # very long cells must not abort the file


def _encoding(head: bytes) -> str:
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    return "utf-8"


def text_chunks(f, ctx, inner: str):
    head = f.read(4)
    f.seek(0)
    decoder = codecs.getincrementaldecoder(_encoding(head))(errors="replace")
    carry, line = "", 1
    while True:
        ctx.check_stop()
        block = f.read(BLOCK)
        final = not block
        text = carry + decoder.decode(block, final=final)
        if final:
            if text:
                yield Chunk(text, inner, LineLocator(text, line))
            return
        cut = text.rfind("\n")
        if cut == -1:  # one enormous line: split, keeping an overlap so values aren't cut
            carry = text[max(0, len(text) - OVERLAP):]
            yield Chunk(text, inner, LineLocator(text, line))
            continue
        body, carry = text[:cut + 1], text[cut + 1:]
        yield Chunk(body, inner, LineLocator(body, line))
        line += body.count("\n")


def _is_header(row) -> bool:
    cells = [c.strip() for c in row if c and c.strip()]
    return bool(cells) and all(not re.fullmatch(r"[\d.,\-+/ ]+", c) for c in cells)


def csv_chunks(f, ctx, inner: str, delimiter: str = ","):
    head = f.read(4)
    f.seek(0)
    enc = _encoding(head)
    stream = io.TextIOWrapper(f, encoding=enc, errors="replace", newline="")
    try:
        reader = csv.reader(stream, delimiter=delimiter)
        header = None
        rows = []
        for n, row in enumerate(reader, start=1):
            if n == 1 and _is_header(row):
                header = [h.strip() or f"col{i + 1}" for i, h in enumerate(row)]
                continue
            rows.append((n, row))
            if len(rows) >= CSV_ROWS_PER_CHUNK:
                ctx.check_stop()
                yield _csv_chunk(rows, header, inner)
                rows = []
        if rows:
            yield _csv_chunk(rows, header, inner)
    finally:
        stream.detach()


def _csv_chunk(rows, header, inner):
    loc, parts, pos = OffsetLocator(), [], 0
    for n, row in rows:
        for i, cell in enumerate(row):
            name = header[i] if header and i < len(header) else f"col{i + 1}"
            seg = f"{name}: {cell} | "
            loc.add(pos, f"row {n}, {name}")
            parts.append(seg)
            pos += len(seg)
        parts.append("\n")
        pos += 1
    return Chunk("".join(parts), inner, loc)


_RTF_GROUPS = re.compile(r"\{\\\*[^{}]*\}|\{\\(?:fonttbl|colortbl|stylesheet|info|pict)[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")
_RTF_CTRL = re.compile(r"\\[a-z]+-?\d* ?|\\'[0-9a-f]{2}|[{}]", re.I)


def rtf_chunks(f, ctx, inner: str):
    ctx.check_stop()
    raw = f.read().decode("latin-1")
    text = _RTF_CTRL.sub(" ", _RTF_GROUPS.sub(" ", raw)).replace("\\par", "\n")
    yield Chunk(text, inner, LineLocator(text, 1, "text line"))
