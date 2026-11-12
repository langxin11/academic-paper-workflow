"""Bounded MinerU ZIP ingestion and validated, atomic cache publication.

The caller owns the per-job lock. Published generations are retained for review;
only the small current.json pointer is replaced, never the working generation.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import tempfile
from urllib.parse import unquote, urlsplit
import uuid
import zipfile

MAX_FILES = 10000
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


class MaterialValidationError(ValueError):
    """An archive or extracted material is unsafe or incomplete."""


def _parts(name: str) -> list[str]:
    if not name or "\\" in name or PureWindowsPath(name).drive or name.startswith("/"):
        raise MaterialValidationError(f"Unsafe relative path: {name!r}")
    parts = name.rstrip("/").split("/")
    if len(parts) > 30 or len(name) > 220:
        raise MaterialValidationError(f"Path too long: {name!r}")
    for part in parts:
        if (part in ("", ".", "..") or part.endswith((" ", "."))
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or _RESERVED.match(part)):
            raise MaterialValidationError(f"Unsafe path component: {name!r}")
    return parts


def _regular(path: Path, root: Path, *, allow_empty=False) -> None:
    try:
        relative = path.relative_to(root)
        cursor = root
        for part in relative.parts:
            cursor /= part
            info = cursor.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise MaterialValidationError(f"Linked material: {cursor}")
        path.resolve().relative_to(root.resolve())
        if not path.is_file():
            raise MaterialValidationError(f"Not a regular file: {path}")
        with path.open("rb") as stream:
            if not allow_empty and not stream.read(1):
                raise MaterialValidationError(f"Empty material: {path}")
    except (OSError, ValueError) as exc:
        raise MaterialValidationError(f"Unreadable or escaping material: {path}") from exc


def _image_targets(text: str):
    definitions = {m[1].strip().casefold(): m[2] for m in re.finditer(
        r'^\s*\[([^\]]+)\]:\s*<?([^\s>]+)>?', text, re.M)}
    # MinerU emits inline Markdown images; also accept reference and HTML images.
    for match in re.finditer(r'!\[[^\]]*\]\(\s*(<[^>]*>|[^\s]+?)(?:\s+["\'][^\n]*?["\'])?\s*\)', text):
        yield match[1].strip("<>")
    for match in re.finditer(r'!\[([^\]]*)\]\[([^\]]*)\]', text):
        key = (match[2] or match[1]).strip().casefold()
        if key not in definitions:
            raise MaterialValidationError(f"Undefined image reference: {key}")
        yield definitions[key]
    for match in re.finditer(r'<img\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']', text, re.I):
        yield match[1]


def _referenced_images(full: Path, root: Path, text: str):
    yield from _image_targets(text)
    # Some Zotero plugins put captions in the Markdown but keep all figure
    # paths in the MinerU content list. Validate those assets as well.
    for sidecar in full.parent.glob("*content_list.json"):
        _regular(sidecar, root)
        if sidecar.stat().st_size > MAX_FILE_BYTES:
            raise MaterialValidationError("MinerU content list exceeds size limit")
        try:
            data = json.loads(sidecar.read_bytes())
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise MaterialValidationError(f"Unreadable MinerU content list: {sidecar}") from exc
        if not isinstance(data, (list, dict)):
            raise MaterialValidationError(f"Invalid MinerU content list: {sidecar}")
        pending = [data]
        while pending:
            item = pending.pop()
            if isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, dict):
                image = item.get("img_path")
                if image:
                    if not isinstance(image, str):
                        raise MaterialValidationError("Invalid img_path in MinerU content list")
                    yield image
                pending.extend(item.values())


def validate_materials(root: Path) -> Path:
    """Return the sole readable UTF-8 full.md, with safe local image references."""
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir() or getattr(root.lstat(), "st_file_attributes", 0) & 0x400:
        raise MaterialValidationError(f"Invalid material directory: {root}")
    candidates = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            entry = Path(directory) / name
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise MaterialValidationError(f"Linked material: {entry}")
        candidates.extend(Path(directory) / name for name in files if name.casefold() == "full.md")
    if len(candidates) != 1:
        names = ", ".join(p.relative_to(root).as_posix() for p in candidates) or "none"
        raise MaterialValidationError(f"Expected one full.md; candidates: {names}")
    full = candidates[0]
    _regular(full, root)
    if full.stat().st_size > MAX_FILE_BYTES:
        raise MaterialValidationError("full.md exceeds size limit")
    try:
        text = full.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise MaterialValidationError("full.md is not UTF-8") from exc
    if not text.strip():
        raise MaterialValidationError("full.md is empty")
    for target in dict.fromkeys(_referenced_images(full, root, text)):
        parsed = urlsplit(target)
        if parsed.scheme in ("https", "http", "data"):
            continue
        if parsed.scheme or parsed.netloc:
            raise MaterialValidationError(f"Unsupported image path: {target}")
        decoded = unquote(parsed.path)
        while decoded.startswith("./"):
            decoded = decoded[2:]
        parts = _parts(decoded)
        image = full.parent.joinpath(*parts)
        _regular(image, root)
        if image.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
            try:
                import pymupdf
                if image.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError("oversized image")
                with pymupdf.open(str(image)) as image_document:
                    bounds = image_document[0].rect
                    if bounds.width * bounds.height > MAX_IMAGE_PIXELS:
                        raise ValueError("image exceeds pixel limit")
                pixmap = pymupdf.Pixmap(str(image))
                if pixmap.width < 1 or pixmap.height < 1:
                    raise ValueError("empty image")
            except Exception as exc:
                raise MaterialValidationError(f"Cannot decode image: {image}") from exc
    return full


def _hashes(root: Path) -> dict[str, str]:
    result = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_file():
            _regular(path, root, allow_empty=True)
            total += path.stat().st_size
            if len(result) >= MAX_FILES or path.stat().st_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise MaterialValidationError("Cached material exceeds capacity limit")
            with path.open("rb") as stream:
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def cached_result(job_dir: Path) -> Path | None:
    """Return a revalidated current generation; corrupt pointers are misses."""
    job_dir = Path(job_dir).absolute()
    try:
        pointer = job_dir / "current.json"
        _regular(pointer, job_dir.absolute())
        if pointer.stat().st_size > 4 * 1024 * 1024:
            return None
        data = json.loads(pointer.read_text(encoding="utf-8"))
        generation = data["generation"]
        if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{32}", generation):
            return None
        result = job_dir / "results" / generation
        if result.resolve() != result:
            return None
        full = validate_materials(result)
        _regular(full, job_dir)
        if data.get("sha256") != _hashes(job_dir / "results" / generation):
            return None
        return full
    except (OSError, ValueError, KeyError, TypeError):
        return None


def publish_archive(archive: Path, job_dir: Path) -> Path:
    """Safely extract, validate, then atomically switch to a new generation."""
    archive, job_dir = Path(archive), Path(job_dir).absolute()
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise MaterialValidationError("Archive exceeds size limit")
    job_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".extract-", dir=job_dir))
    pointer_temp = job_dir / (".current-" + uuid.uuid4().hex)
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if len(entries) > MAX_FILES or sum(e.file_size for e in entries) > MAX_TOTAL_BYTES:
                raise MaterialValidationError("Archive exceeds capacity limit")
            seen = set()
            total = 0
            for entry in entries:
                parts = _parts(entry.orig_filename)
                key = "/".join(parts).casefold()
                mode = entry.external_attr >> 16
                if key in seen or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise MaterialValidationError(f"Duplicate or special archive entry: {entry.filename}")
                seen.add(key)
                if entry.file_size > MAX_FILE_BYTES or entry.flag_bits & 1:
                    raise MaterialValidationError(f"Oversized or encrypted entry: {entry.filename}")
                target = stage.joinpath(*parts)
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                size = 0
                with bundle.open(entry) as source, target.open("xb") as dest:
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        total += len(chunk)
                        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                            raise MaterialValidationError("Extracted data exceeds capacity limit")
                        dest.write(chunk)
                if size != entry.file_size:
                    raise MaterialValidationError("Archive size mismatch")
        full = validate_materials(stage)
        hashes = _hashes(stage)
        relative = full.relative_to(stage)
        generation = uuid.uuid4().hex
        results = job_dir / "results"
        results.mkdir(exist_ok=True)
        if results.is_symlink() or getattr(results.lstat(), "st_file_attributes", 0) & 0x400:
            raise MaterialValidationError("Linked cache results directory")
        published = results / generation
        stage.rename(published)
        pointer_temp.write_text(json.dumps({"generation": generation, "sha256": hashes}), encoding="utf-8")
        os.replace(pointer_temp, job_dir / "current.json")
        return published / relative
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise MaterialValidationError(f"Invalid ZIP archive: {exc}") from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        pointer_temp.unlink(missing_ok=True)
