"""Archives: ZIP (incl. nested), TAR, GZ/BZ2/XZ, with zip-bomb protection."""
import bz2
import gzip
import lzma
import os
import tarfile
import zipfile

from .base import Unreadable, join_inner, spool


def zip_chunks(f, ctx, inner, recurse):
    try:
        zf = zipfile.ZipFile(f)
    except zipfile.BadZipFile as e:
        raise Unreadable(f"corrupt: {e}")
    with zf:
        for info in zf.infolist():
            ctx.check_stop()
            if info.is_dir():
                continue
            where = join_inner(inner, info.filename)
            if info.flag_bits & 0x1:
                ctx.note("encrypted_member")
                continue
            if (info.file_size >= ctx.bomb_min_size and info.compress_size
                    and info.file_size / info.compress_size > ctx.bomb_ratio):
                ctx.note("zip_bomb")
                continue
            try:
                with zf.open(info) as member:
                    data = spool(member, ctx, limit=max(info.file_size, 1) * 2)
            except Unreadable as e:
                ctx.note(e.note)
                return
            except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError) as e:
                ctx.note(f"member_unreadable: {type(e).__name__}")
                continue
            with data:
                yield from recurse(data, os.path.basename(info.filename), where)


def tar_chunks(f, ctx, inner, recurse):
    try:
        tf = tarfile.open(fileobj=f, mode="r:*")
    except tarfile.TarError as e:
        raise Unreadable(f"corrupt: {e}")
    with tf:
        for member in tf:
            ctx.check_stop()
            if not member.isfile():
                continue
            fp = tf.extractfile(member)  # read-only stream; nothing is written to disk
            if fp is None:
                continue
            try:
                data = spool(fp, ctx)
            except Unreadable as e:
                ctx.note(e.note)
                return
            with data:
                yield from recurse(data, os.path.basename(member.name), join_inner(inner, member.name))


_SINGLE = {"gzip": gzip.GzipFile, "bzip2": bz2.BZ2File, "xz": lzma.LZMAFile}


def compressed_chunks(f, ctx, inner, recurse, kind, name):
    """.gz/.bz2/.xz: a tarball, or a single compressed file."""
    start = f.tell()
    try:
        if tarfile.is_tarfile(f):
            f.seek(start)
            yield from tar_chunks(f, ctx, inner, recurse)
            return
    except (OSError, EOFError, tarfile.TarError):
        pass
    f.seek(start)
    stem = os.path.splitext(name)[0] or "content"
    try:
        with _SINGLE[kind](fileobj=f) as z:
            data = spool(z, ctx)
    except Unreadable:
        raise
    except (OSError, EOFError, lzma.LZMAError) as e:
        raise Unreadable(f"corrupt: {type(e).__name__}")
    with data:
        yield from recurse(data, stem, join_inner(inner, stem))
