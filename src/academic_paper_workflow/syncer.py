"""Conservative, portable distribution of the paper workflow's Vault files."""

from __future__ import annotations

from contextlib import contextmanager
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile

from .resources import VERSION, resource_text


_MANIFEST = ".paper-flow/manifest.json"
_ENTRY = ".agents/skills/paper-note/SKILL.md"
_CLAUDE = ".claude/skills/paper-note/SKILL.md"
_TEMPLATE = "Templates/文献精读模板.md"
_MANAGED = {_ENTRY, _CLAUDE, _TEMPLATE}


class SyncError(Exception):
    """An unsafe or invalid sync request."""


def _canonical(data: bytes) -> bytes:
    return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _digest(data: bytes) -> str:
    # Hash logical text so CRLF conversion during cross-device sync is harmless.
    return hashlib.sha256(_canonical(data)).hexdigest()


def _path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or any(p in {".", ".."} or ":" in p or "\\" in p for p in parts):
        raise SyncError(f"Unsafe managed path: {relative}")
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise SyncError(f"Refusing a symlink or junction: {relative}")
        if not current.resolve().is_relative_to(root):
            raise SyncError(f"Path leaves the Vault: {relative}")
    return current


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _manifest(root: Path) -> dict:
    raw = _read(_path(root, _MANIFEST))
    if raw is None:
        return {"schema_version": 1, "files": {}}
    try:
        manifest = json.loads(raw.decode("utf-8"))
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), dict):
            raise ValueError("unsupported manifest schema")
        for name, record in manifest["files"].items():
            if name not in _MANAGED or not isinstance(record, dict):
                raise ValueError("unknown managed file or invalid record")
            _path(root, name)
            for field in ("sha256", "pending_sha256"):
                value = record.get(field)
                if value is not None and (not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)):
                    raise ValueError(f"invalid {field}")
            if not record.get("sha256") and not record.get("pending_sha256"):
                raise ValueError("record has no ownership hash")
        return manifest
    except (ValueError, UnicodeError) as exc:
        raise SyncError(f"Invalid {_MANIFEST}: {exc}") from exc


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _replace(root: Path, relative: str, content: bytes, expected: bytes | None) -> None:
    """Publish a complete file, rechecking external edits immediately before replace."""
    path = _path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path = _path(root, relative)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".paper-flow-", suffix=".tmp", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        path = _path(root, relative)
        if _read(path) != expected:
            raise SyncError(f"File changed during sync; preserved: {relative}. Run sync again.")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _lock(root: Path):
    """OS locks release after a crash; a persistent lock file needs no stale cleanup."""
    path = _path(root, ".paper-flow/sync.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    path = _path(root, ".paper-flow/sync.lock")
    with path.open("a+b") as stream:
        acquired = False
        try:
            try:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise SyncError("Another sync is running for this Vault; retry when it finishes.") from exc
            yield
        finally:
            if acquired:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _desired(target: str) -> dict[str, bytes]:
    if target not in {"all", "codex", "claude", "antigravity"}:
        raise SyncError(f"Unknown sync target: {target}")
    entry = _canonical(resource_text("agent-entry").replace("{{VERSION}}", str(VERSION)).encode("utf-8"))
    files = {_TEMPLATE: _canonical(resource_text("note-template").encode("utf-8"))}
    if target in {"all", "codex", "antigravity"}:
        files[_ENTRY] = entry
    if target in {"all", "claude"}:
        files[_CLAUDE] = entry
    return files


def _run(root: Path, desired: dict[str, bytes], check: bool) -> int:
    manifest_path = _path(root, _MANIFEST)
    original_manifest = _read(manifest_path)
    manifest = _manifest(root)
    records = manifest["files"]
    actions = []
    conflicts = False
    for relative, content in desired.items():
        current = _read(_path(root, relative))
        record = records.get(relative, {})
        actual_hash = _digest(current) if current is not None else None
        desired_hash = _digest(content)
        owned_hashes = {record.get("sha256"), record.get("pending_sha256")} - {None}
        if current is not None and actual_hash != desired_hash and actual_hash not in owned_hashes:
            conflicts = True
            print(f"CONFLICT {relative}: local content preserved; merge the displayed changes manually.")
            for line in difflib.unified_diff(current.decode("utf-8-sig").splitlines(), content.decode("utf-8").splitlines(), fromfile=f"local/{relative}", tofile=f"distributed/{relative}", lineterm=""):
                print(line)
            continue
        new_record = {"sha256": desired_hash, "version": str(VERSION)}
        if current != content:
            action = "ADD" if current is None else "UPDATE"
        elif record != new_record:
            action = "TRACK"
        else:
            continue
        print(f"{action} {relative}")
        actions.append((relative, content, current, new_record))
    if conflicts:
        print("Sync stopped: resolve local conflicts before applying changes.")
        return 2
    if not actions:
        print("Sync clean: all selected files are current; no files written.")
        return 0
    if check:
        print(f"Sync pending: {len(actions)} selected file(s) need installation or tracking; check made no changes.")
        return 1

    # Journal the *next* hash before changing a file. After an interruption, either
    # its previous or pending text remains recognized as managed. Unrelated local
    # edits still conflict. Commit each file before starting the next transaction.
    # This also makes retrying with a different package version safe.
    manifest_bytes = original_manifest
    writes = 0
    for relative, content, current, new_record in actions:
        if current != content:
            if _read(_path(root, relative)) != current:
                raise SyncError(f"File changed during sync; preserved: {relative}. Run sync again.")
            previous = dict(records.get(relative, {}))
            if current is not None:
                # A previous interrupted upgrade may have left its pending text
                # on disk. Promote that observed, already accepted hash before
                # replacing the journal's pending hash with a newer version.
                previous["sha256"] = _digest(current)
            records[relative] = {**previous, "pending_sha256": new_record["sha256"], "pending_version": str(VERSION)}
            pending = _json_bytes(manifest)
            _replace(root, _MANIFEST, pending, manifest_bytes)
            manifest_bytes = pending
            _replace(root, relative, content, current)
            writes += 1
        records[relative] = new_record
        committed = _json_bytes(manifest)
        _replace(root, _MANIFEST, committed, manifest_bytes)
        manifest_bytes = committed
    print(f"Sync complete: wrote {writes} content file(s); updated managed hashes.")
    return 0


def sync(vault: Path, target: str, check: bool) -> int:
    """Return 0 when clean/applied, 1 for pending check, 2 for conflict/error.

    Checks never create a directory, lock, manifest, or temporary file. Normal
    syncs serialize writers and leave a recoverable hash journal after a crash.
    """
    try:
        root = Path(vault).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise SyncError(f"Vault is not a directory: {root}")
        desired = _desired(target)
        if check:
            return _run(root, desired, True)
        with _lock(root):
            return _run(root, desired, False)
    except (SyncError, OSError, UnicodeError, ValueError) as exc:
        print(f"Sync error: {exc}")
        return 2
