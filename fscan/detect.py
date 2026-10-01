"""Identify a file's real type from its bytes, not its extension.

detect() looks at the first bytes (and, for ZIP-based formats, the tail where
the ZIP directory lives). Text files are recognised by content, then given a
subtype from the extension.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Detection:
    type: str
    group: str
    mime: str
    ext_mismatch: bool


# (offset, magic bytes, type, group, mime). Longer / more specific first.
_SIGNATURES = [
    (0, b"%PDF", "pdf", "document", "application/pdf"),
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole", "document", "application/x-ole-storage"),
    (0, b"\x89PNG\r\n\x1a\n", "png", "image", "image/png"),
    (0, b"\xff\xd8\xff", "jpeg", "image", "image/jpeg"),
    (0, b"GIF87a", "gif", "image", "image/gif"),
    (0, b"GIF89a", "gif", "image", "image/gif"),
    (0, b"II*\x00", "tiff", "image", "image/tiff"),
    (0, b"MM\x00*", "tiff", "image", "image/tiff"),
    (0, b"8BPS", "psd", "image", "image/vnd.adobe.photoshop"),
    (0, b"\x00\x00\x01\x00", "ico", "image", "image/x-icon"),
    (0, b"SQLite format 3\x00", "sqlite", "database", "application/vnd.sqlite3"),
    (0, b"!BDN", "pst", "email", "application/vnd.ms-outlook"),
    (0, b"TAPE", "mssql_backup", "database", "application/octet-stream"),
    (0, b"PAR1", "parquet", "data", "application/vnd.apache.parquet"),
    (0, b"{\\rtf", "rtf", "document", "application/rtf"),
    (0, b"%!PS", "postscript", "document", "application/postscript"),
    (0, b"7z\xbc\xaf\x27\x1c", "7z", "archive", "application/x-7z-compressed"),
    (0, b"Rar!\x1a\x07", "rar", "archive", "application/vnd.rar"),
    (0, b"\x1f\x8b", "gzip", "archive", "application/gzip"),
    (0, b"BZh", "bzip2", "archive", "application/x-bzip2"),
    (0, b"\xfd7zXZ\x00", "xz", "archive", "application/x-xz"),
    (0, b"MSCF", "cab", "archive", "application/vnd.ms-cab-compressed"),
    (257, b"ustar", "tar", "archive", "application/x-tar"),
    (32769, b"CD001", "iso", "disk_image", "application/x-iso9660-image"),
    (0, b"conectix", "vhd", "disk_image", "application/x-vhd"),
    (0, b"vhdxfile", "vhdx", "disk_image", "application/x-vhdx"),
    (0, b"!<arch>\n", "static_lib", "executable", "application/x-archive"),
    (0, b"MZ", "exe", "executable", "application/vnd.microsoft.portable-executable"),
    (0, b"\x7fELF", "elf", "executable", "application/x-elf"),
    (0, b"\xca\xfe\xba\xbe", "java_class", "executable", "application/java-vm"),
    (0, b"ID3", "mp3", "audio", "audio/mpeg"),
    (0, b"\xff\xfb", "mp3", "audio", "audio/mpeg"),
    (0, b"OggS", "ogg", "audio", "audio/ogg"),
    (0, b"fLaC", "flac", "audio", "audio/flac"),
    (0, b"\x1aE\xdf\xa3", "mkv", "video", "video/x-matroska"),
    (0, b"0&\xb2u\x8ef\xcf\x11", "asf", "video", "video/x-ms-asf"),
    (0, b"wOFF", "woff", "font", "font/woff"),
    (0, b"wOF2", "woff2", "font", "font/woff2"),
    (0, b"OTTO", "otf", "font", "font/otf"),
    (0, b"\x00\x01\x00\x00\x00", "ttf", "font", "font/ttf"),
]

# ZIP-based formats, recognised by entry names found in the ZIP headers.
_ZIP_KINDS = [
    (b"word/", "docx", "document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    (b"xl/", "xlsx", "spreadsheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    (b"ppt/", "pptx", "presentation", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    (b"visio/", "vsdx", "document", "application/vnd.ms-visio.drawing"),
    (b"mimetypeapplication/vnd.oasis.opendocument.text", "odt", "document", "application/vnd.oasis.opendocument.text"),
    (b"mimetypeapplication/vnd.oasis.opendocument.spreadsheet", "ods", "spreadsheet", "application/vnd.oasis.opendocument.spreadsheet"),
    (b"mimetypeapplication/vnd.oasis.opendocument.presentation", "odp", "presentation", "application/vnd.oasis.opendocument.presentation"),
    (b"mimetypeapplication/epub+zip", "epub", "document", "application/epub+zip"),
    (b"AndroidManifest.xml", "apk", "executable", "application/vnd.android.package-archive"),
    (b"META-INF/MANIFEST.MF", "jar", "executable", "application/java-archive"),
]

# Legacy Office / Outlook files share the OLE container; the extension decides.
_OLE_KINDS = {
    ".doc": ("doc", "document", "application/msword"),
    ".dot": ("doc", "document", "application/msword"),
    ".xls": ("xls", "spreadsheet", "application/vnd.ms-excel"),
    ".xlt": ("xls", "spreadsheet", "application/vnd.ms-excel"),
    ".ppt": ("ppt", "presentation", "application/vnd.ms-powerpoint"),
    ".pps": ("ppt", "presentation", "application/vnd.ms-powerpoint"),
    ".msg": ("msg", "email", "application/vnd.ms-outlook"),
    ".msi": ("msi", "executable", "application/x-msi"),
    ".vsd": ("vsd", "document", "application/vnd.visio"),
}

# Text formats: subtype and group come from the extension.
_TEXT_EXTS = {
    ".txt": ("text", "text"), ".md": ("markdown", "document"), ".rst": ("text", "text"),
    ".csv": ("csv", "data"), ".tsv": ("tsv", "data"), ".json": ("json", "data"),
    ".xml": ("xml", "data"), ".yaml": ("yaml", "config"), ".yml": ("yaml", "config"),
    ".ini": ("ini", "config"), ".cfg": ("config", "config"), ".conf": ("config", "config"),
    ".config": ("config", "config"), ".properties": ("config", "config"), ".toml": ("toml", "config"),
    ".log": ("log", "log"), ".out": ("log", "log"),
    ".eml": ("eml", "email"), ".mbox": ("mbox", "email"), ".vcf": ("vcard", "data"), ".ics": ("ical", "data"),
    ".html": ("html", "web"), ".htm": ("html", "web"), ".css": ("css", "web"), ".svg": ("svg", "image"),
    ".sql": ("sql", "code"), ".py": ("python", "code"), ".js": ("javascript", "code"),
    ".ts": ("typescript", "code"), ".java": ("java", "code"), ".cs": ("csharp", "code"),
    ".c": ("c", "code"), ".h": ("c", "code"), ".cpp": ("cpp", "code"), ".go": ("go", "code"),
    ".rb": ("ruby", "code"), ".php": ("php", "code"), ".sh": ("shell", "code"),
    ".ps1": ("powershell", "code"), ".bat": ("batch", "code"), ".cmd": ("batch", "code"),
    ".vbs": ("vbscript", "code"), ".r": ("r", "code"), ".ipynb": ("notebook", "code"),
}

# Expected group per binary extension, used to flag mismatches.
_BINARY_EXT_GROUP = {
    ".pdf": "document", ".doc": "document", ".docx": "document", ".rtf": "document",
    ".odt": "document", ".epub": "document", ".vsd": "document", ".vsdx": "document",
    ".xls": "spreadsheet", ".xlsx": "spreadsheet", ".xlsm": "spreadsheet", ".ods": "spreadsheet",
    ".ppt": "presentation", ".pptx": "presentation", ".odp": "presentation",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".tif": "image",
    ".tiff": "image", ".bmp": "image", ".ico": "image", ".psd": "image",
    ".zip": "archive", ".7z": "archive", ".rar": "archive", ".gz": "archive", ".tgz": "archive",
    ".bz2": "archive", ".xz": "archive", ".tar": "archive", ".cab": "archive",
    ".exe": "executable", ".dll": "executable", ".sys": "executable", ".msi": "executable",
    ".jar": "executable", ".apk": "executable", ".class": "executable",
    ".mp3": "audio", ".ogg": "audio", ".flac": "audio", ".wav": "audio",
    ".mp4": "video", ".mov": "video", ".mkv": "video", ".webm": "video", ".avi": "video", ".wmv": "video",
    ".db": "database", ".sqlite": "database", ".bak": "database",
    ".pst": "email", ".msg": "email",
    ".iso": "disk_image", ".vhd": "disk_image", ".vhdx": "disk_image",
    ".ttf": "font", ".otf": "font", ".woff": "font", ".woff2": "font",
    ".parquet": "data",
}

_UNKNOWN = ("unknown", "unknown", "application/octet-stream")


def detect(head: bytes, tail: bytes, ext: str, size: int) -> Detection:
    ext = (ext or "").lower()
    if size == 0:
        return Detection("empty", "empty", "application/x-empty", False)

    found = _match_magic(head, tail, ext)
    if found:
        type_, group, mime = found
        expected = _BINARY_EXT_GROUP.get(ext) or _TEXT_EXTS.get(ext, (None, None))[1]
        return Detection(type_, group, mime, expected is not None and expected != group)

    if _looks_like_text(head):
        type_, group = _TEXT_EXTS.get(ext, ("text", "text"))
        mismatch = ext in _BINARY_EXT_GROUP
        if mismatch:
            type_, group = "text", "text"
        return Detection(type_, group, "text/plain", mismatch)

    return Detection(*_UNKNOWN, False)


def _match_magic(head: bytes, tail: bytes, ext: str):
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return _zip_kind(head + tail)
    if head[:4] == b"RIFF" and len(head) >= 12:
        return {b"WEBP": ("webp", "image", "image/webp"),
                b"WAVE": ("wav", "audio", "audio/wav"),
                b"AVI ": ("avi", "video", "video/x-msvideo")}.get(head[8:12])
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand.startswith((b"heic", b"heix", b"mif1")):
            return ("heic", "image", "image/heic")
        if brand.startswith(b"qt"):
            return ("mov", "video", "video/quicktime")
        if brand.startswith(b"M4A"):
            return ("m4a", "audio", "audio/mp4")
        return ("mp4", "video", "video/mp4")
    if head.startswith(b"BM") and ext == ".bmp":  # 2-byte magic: too weak alone
        return ("bmp", "image", "image/bmp")
    for offset, magic, type_, group, mime in _SIGNATURES:
        if head[offset:offset + len(magic)] == magic:
            if type_ == "ole":
                return _OLE_KINDS.get(ext, ("ole", "document", "application/x-ole-storage"))
            return (type_, group, mime)
    return None


def _zip_kind(data: bytes):
    for marker, type_, group, mime in _ZIP_KINDS:
        if marker in data:
            return (type_, group, mime)
    return ("zip", "archive", "application/zip")


def _looks_like_text(head: bytes) -> bool:
    sample = head[:8192]
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            sample.decode("utf-16")
            return True
        except UnicodeDecodeError:
            # A 1 MB cut can split a character; judge on an even-length prefix.
            try:
                sample[: len(sample) - (len(sample) % 2) - 2].decode("utf-16")
                return True
            except UnicodeDecodeError:
                return False
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError as e:
        if e.start >= len(sample) - 4:  # cut mid-character at the sample edge
            return True
    # Legacy single-byte text (cp1252 etc.): mostly printable characters.
    printable = sum(1 for b in sample if b >= 32 or b in (9, 10, 13))
    return printable / len(sample) > 0.95
