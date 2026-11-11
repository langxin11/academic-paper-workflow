"""Preflight every quote before atomically publishing PDF underline comments."""

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

import pymupdf as pdf

from ._locking import writer_lock
from .quote_locator import bounds, locate, location, normalized
from .resources import resource_text


OWNER = "/PaperFlowV1"


def colors():
    rows = re.findall(r"^\| (\w+) \| ([\w—]+) \| \x60#([0-9A-Fa-f]{6})\x60 \|", resource_text("colors"), re.M)
    if not rows:
        raise ValueError("六色规范中缺少颜色表")
    result = {}
    for name, alias, value in rows:
        result[name] = tuple(byte / 255 for byte in bytes.fromhex(value))
        if alias != "—":
            result[alias] = result[name]
    return result


@dataclass
class Entry:
    id: str
    page: int  # always zero-based internally
    quote: str
    color: tuple
    comment: str
    title: str
    before: str
    after: str
    region: tuple | None


def read_entries(data):
    legacy = isinstance(data, list)
    if legacy:
        base, rows = 0, data
    elif isinstance(data, dict):
        base, rows = data.get("page_base"), data.get("annotations")
    else:
        raise ValueError("JSON 应为含 page_base 和 annotations 的对象或旧版数组")
    if type(base) is not int or base not in (0, 1) or not isinstance(rows, list):
        raise ValueError("需要 page_base（0 或 1）和 annotations 数组")
    palette = colors()
    entries, ids = [], set()
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"第 {n} 条批注应为对象")
        page = row.get("page", 0 if legacy else None)
        quote = row.get("quote", row.get("search") if legacy else None)
        color = row.get("color", "amber" if legacy else None)
        comment = row.get("comment", row.get("content", "") if legacy else None)
        title = row.get("title", "批注")
        before, after = row.get("before", ""), row.get("after", "")
        if type(page) is not int or not isinstance(quote, str) or not normalized(quote):
            raise ValueError(f"第 {n} 条需要整数页码和完整引文")
        if not isinstance(color, str) or color.lower().strip() not in palette:
            raise ValueError(f"第 {n} 条的色名未在六色表中定义：{color!r}")
        if not all(isinstance(s, str) and "\x00" not in s for s in [title, comment, before, after, quote]):
            raise ValueError(f"第 {n} 条的评论、标题、引文及前后文应为文本")
        if not legacy and not comment.strip():
            raise ValueError(f"第 {n} 条需要非空 comment")
        region = row.get("region")
        if region is not None:
            if (not isinstance(region, list) or len(region) != 4
                    or not all(type(v) in (float, int) and math.isfinite(v) for v in region)
                    or region[0] >= region[2] or region[1] >= region[3]):
                raise ValueError(f"第 {n} 条的 region 应为 [x0,y0,x1,y1]（PDF 点）")
            region = tuple(region)
        ident = row.get("id")
        if legacy and ident is None:
            locator = json.dumps([page, normalized(quote), normalized(before), normalized(after), region])
            ident = "legacy-" + hashlib.sha256(locator.encode()).hexdigest()[:32]
        if not isinstance(ident, str) or not re.fullmatch(r"[\w.-]{1,128}", ident):
            raise ValueError(f"第 {n} 条需要稳定 id（1–128 个字母、数字、下划线、点或连字符）")
        if ident in ids:
            raise ValueError(f"批注 ID 重复：{ident}")
        ids.add(ident)
        entries.append(Entry(ident, page-base, quote, palette[color.strip().lower()],
                             comment, title, before, after, region))
    return entries


def ownership(doc, xref):
    if doc.xref_get_key(xref, "PaperFlowOwner") == ("name", OWNER):
        kind, name = doc.xref_get_key(xref, "NM")
        namespace = "paper-flow:"
        if kind != "string" or not name.startswith(namespace) or name == namespace:
            raise ValueError(f"引擎批注 xref {xref} 缺少稳定 ID")
        return name.removeprefix(namespace)
    return None


def inventory(doc):
    owned = {}
    annots = {}
    for page in doc:
        for xref, _, _ in page.annot_xrefs():
            annots[xref] = (page.number, doc.xref_object(xref))
            ident = ownership(doc, xref)
            if ident is not None:
                if ident in owned:
                    raise ValueError(f"PDF 已有多个同 ID 的引擎批注：{ident}")
                owned[ident] = (page.number, xref)
    return owned, annots


def same_geometry(annot, quads):
    wanted = [tuple(point) for quad in quads for point in quad]
    actual = annot.vertices or []
    return (annot.type[0] == pdf.PDF_ANNOT_UNDERLINE and len(actual) == len(wanted)
            and all(abs(a-b) < 0.002 for pt, ref in zip(actual, wanted)
                    for a, b in zip(pt, ref)))


def same_annotation(doc, existing, entry, quads):
    if existing is None or existing[0] != entry.page:
        return False
    page = doc[entry.page]
    annot = page.load_annot(existing[1])
    stroke = annot.colors.get("stroke") or []
    return (same_geometry(annot, quads) and annot.info["content"] == entry.comment
            and annot.info["title"] == entry.title and len(stroke) == 3
            and all(abs(a-b) < 1e-5 for a, b in zip(stroke, entry.color)))


@dataclass
class Plan:
    entry: Entry
    quads: list
    previous: tuple | None
    changed: bool


def plan_batch(doc, entries, owned):
    plans, errors = [], []
    for entry in entries:
        label = f"{entry.id} · PDF 物理第 {entry.page+1} 页"
        try:
            if not 0 <= entry.page < len(doc):
                raise ValueError(f"页码越界，共 {len(doc)} 页")
            quads = locate(doc[entry.page], entry)
            old = owned.get(entry.id)
            changed = not same_annotation(doc, old, entry, quads)
            plans.append(Plan(entry, quads, old, changed))
            print(f"{label} · {len(quads)} 段 {location(bounds(quads))} · "
                  + ("将写入" if changed else "已一致"))
        except ValueError as exc:
            errors.append(f"{label} · {exc}")
    if errors:
        raise ValueError("整批预检失败：\n" + "\n".join(errors))
    return plans


def modify(doc, plans, protected):
    for plan in plans:
        if not plan.changed:
            continue
        entry = plan.entry
        page = doc[entry.page]
        annot = None
        if plan.previous:
            prev_page, xref = plan.previous
            source_page = page if prev_page == page.number else doc[prev_page]
            previous = source_page.load_annot(xref)
            protected.pop(xref, None)
            if prev_page == page.number and same_geometry(previous, plan.quads):
                annot = previous
            else:
                # Some readers retain a reply with a dangling /IRT when its
                # parent is deleted. Preserve the author's discussion thread.
                if any(doc.xref_get_key(ref, "IRT") == ("xref", f"{xref} 0 R")
                       for ref in protected):
                    raise ValueError(f"{entry.id} 有关联回复，移动下划线会破坏回复关联")
                if previous.popup_xref:
                    protected.pop(previous.popup_xref, None)
                source_page.delete_annot(previous)
        if annot is None:
            annot = page.add_underline_annot(plan.quads)
        annot.set_colors(stroke=entry.color)
        annot.set_info(title=entry.title, content=entry.comment)
        annot.update()
        doc.xref_set_key(annot.xref, "PaperFlowOwner", OWNER)
        doc.xref_set_key(annot.xref, "NM", pdf.get_pdf_str("paper-flow:" + entry.id))


def verify(path, plans, protected, page_count):
    with pdf.open(path) as doc:
        if doc.is_repaired or len(doc) != page_count:
            raise ValueError("重读临时 PDF 时页数或结构校验失败")
        owned, annots = inventory(doc)
        for xref, original in protected.items():
            if annots.get(xref) != original:
                raise ValueError(f"重读校验发现既有批注变化，xref {xref}")
        for plan in plans:
            if not same_annotation(doc, owned.get(plan.entry.id), plan.entry, plan.quads):
                raise ValueError(f"重读校验发现引文定位、颜色或评论变化：{plan.entry.id}")


def fingerprint(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").digest()


def check_source(path, expected):
    if fingerprint(path) != expected:
        raise ValueError("PDF 自预检后已变化，请重新运行")


def sync_directory(folder):
    if os.name == "posix":
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def backup(path, data, digest, folder):
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{path.name}.{uuid.uuid4().hex}.bak"
    with tempfile.NamedTemporaryFile(dir=folder, prefix=".paper-flow-", suffix=".partial", delete=False) as out:
        temp = Path(out.name)
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if fingerprint(temp) != digest:
            raise ValueError("本轮备份与输入校验值不同")
        os.replace(temp, target)
        sync_directory(folder)
        return target
    finally:
        temp.unlink(missing_ok=True)


def annotate(path, data, *, check=False, backup_dir=None):
    path = Path(path).expanduser().resolve(strict=True)
    snapshot = path.read_bytes()
    digest = hashlib.sha256(snapshot).digest()
    entries = read_entries(data)
    with pdf.open(stream=snapshot, filetype="pdf") as original:
        if not original.is_pdf or original.needs_pass:
            raise ValueError("需要可读取的 PDF")
        owned, protected = inventory(original)
        plans = plan_batch(original, entries, owned)
        page_count = len(original)
    if check or not any(plan.changed for plan in plans):
        check_source(path, digest)
        return None

    with writer_lock(path):
        check_source(path, digest)
        # An incremental save on a disposable exact copy leaves manual PDF
        # objects as-is, including reader-specific fields and appearance streams.
        with tempfile.NamedTemporaryFile(prefix=".paper-flow-", suffix=".pdf", dir=path.parent, delete=False) as out:
            temp = Path(out.name)
        try:
            temp.write_bytes(snapshot)
            with pdf.open(temp) as doc:
                if not doc.can_save_incrementally():
                    raise ValueError("PDF 结构不支持安全增量保存，需要先检查原件")
                modify(doc, plans, protected)
                doc.saveIncr()
            with temp.open("r+b") as handle:
                os.fsync(handle.fileno())
            verify(temp, plans, protected, page_count)
            check_source(path, digest)
            saved = backup(path, snapshot, digest, Path(backup_dir) if backup_dir else path.parent)
            os.chmod(temp, stat.S_IMODE(path.stat().st_mode))
            check_source(path, digest)
            os.replace(temp, path)
            sync_directory(path.parent)
            return saved
        finally:
            temp.unlink(missing_ok=True)


def run(args):
    folder = args.backup_dir
    if folder is None and not args.check:
        from .config import load_local_config
        config, settings = load_local_config()
        if "backup_dir" in settings:
            value = settings["backup_dir"]
            if not isinstance(value, str) or not value.strip():
                raise ValueError("backup_dir 应为非空路径")
            folder = config.parent / Path(value).expanduser()
    payload = json.loads(Path(args.json).read_text(encoding="utf-8-sig"))
    saved = annotate(args.pdf, payload, check=args.check,
                     backup_dir=Path(folder).expanduser() if folder is not None else None)
    if saved:
        print(f"已写入。可恢复本轮原件：{saved}")
    else:
        print("整批预检通过，写入 0 个文件。")
    return 0


def register(parser):
    parser.add_argument("--pdf", required=True, help="待批注的 PDF")
    parser.add_argument("--json", required=True, help="完整引文、物理页码及语义色 JSON")
    parser.add_argument("--check", action="store_true", help="只预检整批匹配及物理页序")
    parser.add_argument("--backup-dir", type=Path, help="本轮恢复副本目录，默认使用配置或 PDF 所在目录")
    parser.set_defaults(handler=run)
