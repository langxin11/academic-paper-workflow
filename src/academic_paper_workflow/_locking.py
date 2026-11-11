"""Local advisory locks shared by workflow writers; never stored in a Vault."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import tempfile


@contextmanager
def local_lock(path, *, message, error_type):
    # Keep a stable inode. Kernel locks release on process exit, including crashes.
    scope = f"paper-flow-{os.getuid()}" if hasattr(os, "getuid") else "paper-flow"
    folder = Path(tempfile.gettempdir()) / scope
    folder.mkdir(mode=0o700, exist_ok=True)
    if os.name == "posix":
        info = folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("临时锁目录的属主或权限异常")
    key = hashlib.sha256(os.fsencode(os.path.normcase(str(Path(path).resolve())))).hexdigest()
    fd = os.open(folder / key, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise error_type(message) from exc
        yield


def writer_lock(path):
    """Preserve the PDF writer's public lock and contention error contract."""
    return local_lock(path, message="同一 PDF 正在写回；请稍后重试", error_type=ValueError)


def index_lock(path):
    return local_lock(path, message=f"另一进程正在更新索引，请稍后重试：{path}", error_type=RuntimeError)
