"""Emails (.eml, .msg) and Outlook mailboxes (.pst/.ost), with attachments."""
import email
import io
import re
from email import policy

from .base import Chunk, Unreadable, join_inner

_TAGS = re.compile(r"<[^>]+>")
_HEADERS = ("From", "To", "Cc", "Bcc", "Reply-To", "Subject")


def _html_to_text(html: str) -> str:
    return _TAGS.sub(" ", re.sub(r"(?is)<(script|style).*?</\1>", " ", html))


# ---- .eml ----------------------------------------------------------------------
def eml_chunks(f, ctx, inner, recurse):
    msg = email.message_from_binary_file(f, policy=policy.default)
    yield from _message_chunks(msg, ctx, inner, recurse)


def _message_chunks(msg, ctx, inner, recurse):
    head = "\n".join(f"{h}: {msg[h]}" for h in _HEADERS if msg[h])
    if head:
        yield Chunk(head, inner, lambda pos: "email header")
    n = 0
    for part in msg.walk():
        ctx.check_stop()
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        if ctype == "message/rfc822" and not filename:
            continue  # walk() descends into it
        if filename or part.get_content_disposition() == "attachment":
            n += 1
            data = part.get_payload(decode=True) or b""
            name = filename or f"attachment {n}"
            yield from recurse(io.BytesIO(data), name, join_inner(inner, f"attachment: {name}"))
        elif ctype in ("text/plain", "text/html"):
            try:
                text = part.get_content()
            except (LookupError, UnicodeError):
                text = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
            if ctype == "text/html":
                text = _html_to_text(text)
            if text.strip():
                yield Chunk(text, inner, lambda pos: "email body")


# ---- .msg (Outlook item) -------------------------------------------------------
def msg_chunks(f, ctx, inner, recurse):
    import extract_msg
    try:
        msg = extract_msg.openMsg(f.read())
    except Exception as e:
        raise Unreadable(f"corrupt: {type(e).__name__}")
    try:
        yield from msg_object_chunks(msg, ctx, inner, recurse)
    finally:
        msg.close()


def msg_object_chunks(msg, ctx, inner, recurse):
    """Works on any object shaped like extract_msg's MSGFile (also embedded messages)."""
    head = "\n".join(f"{k}: {v}" for k, v in [("From", getattr(msg, "sender", None)),
                                               ("To", getattr(msg, "to", None)),
                                               ("Cc", getattr(msg, "cc", None)),
                                               ("Subject", getattr(msg, "subject", None))] if v)
    if head:
        yield Chunk(head, inner, lambda pos: "email header")
    body = getattr(msg, "body", None) or ""
    if not body.strip():
        html = getattr(msg, "htmlBody", None) or b""
        body = _html_to_text(html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html)
    if body.strip():
        yield Chunk(body, inner, lambda pos: "email body")
    for n, att in enumerate(getattr(msg, "attachments", []) or [], start=1):
        ctx.check_stop()
        name = getattr(att, "longFilename", None) or getattr(att, "shortFilename", None) or f"attachment {n}"
        data = getattr(att, "data", None)
        where = join_inner(inner, f"attachment: {name}")
        if isinstance(data, (bytes, bytearray)):
            yield from recurse(io.BytesIO(data), name, where)
        elif data is not None and hasattr(data, "attachments"):  # embedded email
            yield from msg_object_chunks(data, ctx, where, recurse)


# ---- .pst / .ost (needs pypff, i.e. libpff) --------------------------------------
def pst_available() -> bool:
    try:
        import pypff  # noqa: F401
        return True
    except ImportError:
        return False


def pst_chunks(f, ctx, inner, recurse):
    if not pst_available():
        raise Unreadable("pst_reader_unavailable")
    import pypff
    pst = pypff.file()
    try:
        pst.open_file_object(f)
    except Exception as e:
        raise Unreadable(f"corrupt: {type(e).__name__}")
    try:
        yield from _pst_folder(pst.get_root_folder(), "", ctx, inner, recurse)
    finally:
        pst.close()


def _pst_folder(folder, path, ctx, inner, recurse):
    name = folder.name or ""
    here = f"{path}/{name}" if name else path
    for i in range(folder.number_of_sub_messages):
        ctx.check_stop()
        try:
            msg = folder.get_sub_message(i)
            yield from _pst_message(msg, here or "/", ctx, inner, recurse)
        except (OSError, IOError):
            ctx.note("pst_message_unreadable")
    for i in range(folder.number_of_sub_folders):
        yield from _pst_folder(folder.get_sub_folder(i), here, ctx, inner, recurse)


def _pst_message(msg, folder_path, ctx, inner, recurse):
    subject = (msg.subject or "(no subject)")[:80]
    where_msg = join_inner(inner, f"pst: {folder_path} > \"{subject}\"")
    head = "\n".join(x for x in [msg.sender_name or "", msg.transport_headers or ""] if x)
    if head.strip():
        yield Chunk(head, where_msg, lambda pos: "email header")
    body = msg.plain_text_body or b""
    if not body and msg.html_body:
        body = _html_to_text(msg.html_body.decode("utf-8", errors="replace")).encode()
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    if text.strip():
        yield Chunk(text, where_msg, lambda pos: "email body")
    for n in range(msg.number_of_attachments):
        att = msg.get_attachment(n)
        size = att.get_size() or 0
        if size <= 0:
            continue
        name = f"attachment {n + 1}"
        yield from recurse(io.BytesIO(att.read_buffer(size)), name, join_inner(where_msg, name))
