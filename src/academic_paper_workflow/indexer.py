"""Deterministic literature ledger and opt-in, incremental reading-route entries."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import yaml

from ._locking import index_lock as file_lock
from .config import load_local_config, resolve_vault
from .resources import resource_file_text

BEGIN = "<!-- paper-flow:index:begin -->"
END = "<!-- paper-flow:index:end -->"
DEFAULT_NOTES = "文献研究/文献笔记"
DEFAULT_OUTPUT = "文献研究/主题综述/文献笔记总索引（自动生成）.md"
DEFAULT_MAINTAINED = ["文献研究/主题综述/文献精读检索入口.md"]


def vault_path(vault: Path, value: str, field: str) -> Path:
    """All index configuration and targets are Vault-relative, with symlink checks."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 应为非空的 Vault 相对路径")
    # Also reject Windows absolute paths when running on a non-Windows host.
    if Path(value).is_absolute() or re.match(r"^(?:[A-Za-z]:|[\\/])", value):
        raise ValueError(f"{field} 应为 Vault 相对路径：{value}")
    result = (vault / value.replace("\\", "/")).resolve()
    if not result.is_relative_to(vault.resolve()) or result == vault.resolve():
        raise ValueError(f"{field} 超出 Vault 或指向根目录：{value}")
    return result


@dataclass(frozen=True)
class Settings:
    notes: Path
    output: Path
    maintained: tuple[Path, ...]
    aliases: dict[str, str]


def settings(vault: Path) -> Settings:
    _, config = load_local_config()
    index = config.get("index", {})
    if not isinstance(index, dict):
        raise ValueError("config.local.yaml 的 index 应为字段映射")
    maintained = index.get("maintained_indexes", DEFAULT_MAINTAINED)
    if not isinstance(maintained, list) or not all(isinstance(p, str) for p in maintained):
        raise ValueError("index.maintained_indexes 应为相对路径列表")
    defaults = yaml.safe_load(resource_file_text("core/index_groups.yaml"))
    aliases = index.get("group_aliases", {})
    if not isinstance(aliases, dict) or not all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in aliases.items()):
        raise ValueError("index.group_aliases 应为字符串到非空分组名称的映射")
    return Settings(
        vault_path(vault, index.get("notes_dir", DEFAULT_NOTES), "index.notes_dir"),
        vault_path(vault, index.get("output", DEFAULT_OUTPUT), "index.output"),
        tuple(vault_path(vault, p, "index.maintained_indexes") for p in maintained),
        {**defaults, **aliases},
    )


def parse_frontmatter(text: str) -> tuple[dict, str | None]:
    text = text.lstrip("\ufeff")
    opening = re.match(r"^---[ \t]*\r?\n", text)
    if not opening:
        return {}, "缺少 frontmatter"
    body = text[opening.end():]
    match = re.search(r"^---[ \t]*\r?$", body, re.M)
    if not match:
        return {}, "frontmatter 未闭合"
    try:
        meta = yaml.safe_load(body[:match.start()])
    except yaml.YAMLError:
        return {}, "frontmatter YAML 格式有误"
    if meta is None:
        return {}, "frontmatter 为空"
    if not isinstance(meta, dict):
        return {}, "frontmatter 应为字段映射"
    return meta, None


def clean(value: object, limit: int = 160) -> str:
    text = str(value) if value is not None else ""
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\s+", " ", text.replace("**", "").replace("`", "")).strip()
    return text if len(text) <= limit else text[:limit - 3] + "..."


def cell(value: object) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("\r", " ").replace("\n", " ")


def oneliner(text: str) -> tuple[str, str]:
    match = re.search(r"^>\s*-\s*\*\*本质创新[：:]?\*\*[：:]?\s*(.+)$", text, re.M)
    if match:
        return clean(match[1]), "30秒看板"
    lines = text.splitlines()
    for headings, source in (({"一句话总结", "一句话", "One-Sentence Take", "One-Sentence Takeaways"}, "一句话"), ({"Summary", "摘要"}, "Summary 回退")):
        for i, line in enumerate(lines):
            if re.sub(r"^#+\s*", "", line.strip()) not in headings or not line.startswith("#"):
                continue
            for following in lines[i + 1:]:
                candidate = following.strip()
                if candidate.startswith("#"):
                    break
                if not candidate or candidate.startswith(("|", "!", "<!--")):
                    continue
                return clean(re.sub(r"^(?:-\s+|>\s*)", "", candidate)), source
    return "", ""


@dataclass(frozen=True)
class Note:
    path: Path
    relative: str
    meta: dict
    guide: str
    source: str
    problems: tuple[str, ...]


def read_note(path: Path, vault: Path) -> Note:
    text = path.read_text(encoding="utf-8-sig")
    meta, problem = parse_frontmatter(text)
    guide, source = oneliner(text)
    problems = ([problem] if problem else []) + ([] if guide else ["缺少一句话导读"])
    return Note(path, path.relative_to(vault).with_suffix("").as_posix(), meta, guide, source, tuple(problems))


def is_literature_note(path: Path, directory: Path) -> bool:
    return (path.suffix.lower() == ".md"
            and not path.name.lower().endswith(".excalidraw.md")
            and "assets" not in {part.casefold() for part in path.relative_to(directory).parts})


def wiki(note: Note, table: bool = False) -> str:
    # Full Vault-relative targets disambiguate identical filenames in subdirectories.
    target = note.relative
    for char in ("%", "[", "]", "#", "^", "|", "<", ">", "\r", "\n"):
        target = target.replace(char, f"%{ord(char):02X}")
    label = cell(note.path.stem).replace("[", "&#91;").replace("]", "&#93;")
    separator = "\\|" if table else "|"
    return f"[[{target}{separator}{label}]]"


def render(notes: list[Note], aliases: dict[str, str]) -> str:
    groups: dict[str, list[Note]] = {}
    for note in notes:
        project = note.meta.get("project", "")
        if isinstance(project, list):
            project = project[0] if project else ""
        project = clean(project)
        groups.setdefault(aliases.get(project, project or "未分类"), []).append(note)
    out = ["# 文献笔记总索引（自动生成）", "", "> 由 `paper-flow index` 生成；受管标记内部随笔记重建，人工阅读路线在各专题索引维护。", f"> 共 {len(notes)} 篇；按 frontmatter 的 project 分组。", ""]
    for group in sorted(groups, key=lambda g: (g == "未分类", g.casefold(), g)):
        out += [f"## {cell(group)}（{len(groups[group])} 篇）", "", "| 笔记 | 年份 | 参考用途 | 状态 | 一句话 |", "|---|---|---|---|---|"]
        def year(n: Note) -> str:
            raw = n.meta.get("year")
            match = re.search(r"(?:19|20)\d{2}", str(raw or n.path.stem))
            return match[0] if match else "?"
        for note in sorted(groups[group], key=lambda n: (-int(year(n)) if year(n).isdigit() else 1, n.relative.casefold(), n.relative)):
            guide = note.guide or "（无一句话小节）"
            if note.source == "Summary 回退":
                guide += "（Summary 首条）"
            out.append(f"| {wiki(note, True)} | {cell(year(note))} | {cell(note.meta.get('usefulness', '待评估'))} | {cell(note.meta.get('status', '未知'))} | {cell(guide)} |")
        out.append("")
    problems = [(note.relative, problem) for note in notes for problem in note.problems]
    if problems:
        out += ["## 待修复", ""] + [f"- {cell(name)}：{problem}" for name, problem in problems] + [""]
    return "\n".join(out)


def generated_update(existing: str | None, content: str) -> str:
    block = BEGIN + "\n" + content.rstrip("\n") + "\n" + END
    if existing is None:
        return "---\ntags: [literature-index, auto-generated]\nstatus: auto\n---\n\n" + block + "\n"
    if existing.count(BEGIN) == existing.count(END) == 1 and existing.index(BEGIN) < existing.index(END):
        return existing[:existing.index(BEGIN)] + block + existing[existing.index(END) + len(END):]
    if BEGIN in existing or END in existing:
        raise ValueError("自动总索引的受管标记重复或不完整，保留原文件")
    raise ValueError("输出已有未标记内容（含旧自动索引），保留原文件；请先将旧页另存为快照，或仅在确认的自动正文前后放置 paper-flow:index:begin/end HTML 标记")


def register_entry(existing: str, note: Note, guide: str, backlog: str | None, unique_stem: bool = True) -> str:
    identity = hashlib.sha256(note.relative.encode("utf-8")).hexdigest()[:20]
    start = f"<!-- paper-flow:entry:{identity}:begin -->"
    end = f"<!-- paper-flow:entry:{identity}:end -->"
    # Existing authored registrations remain authoritative, even if a new guide is supplied.
    if start not in existing:
        # Backlog registration is independent from an already-authored reading route.
        search = existing
        if backlog is not None:
            section = re.search(r"^##\s+待补收录\s*$", existing, re.M)
            search = existing[section.end():] if section else ""
            boundary = re.search(r"^#{1,2}\s", search, re.M)
            if boundary:
                search = search[:boundary.start()]
        targets = [unquote(match.group(1).replace("\\|", "|").split("|", 1)[0].removesuffix(".md")) for match in re.finditer(r"\[\[([^\]]+)\]\]", search)]
        if note.relative in targets or (unique_stem and note.path.stem in targets):
            return existing
    description = cell(clean(guide, 1000))
    if backlog is not None:
        entry = "| 笔记 | 导读 | 待确认事项 |\n|---|---|---|\n" + f"| {wiki(note, True)} | {description} | {cell(clean(backlog, 1000))} |"
        heading = "待补收录"
    else:
        entry = f"- {wiki(note)} — {description}"
        heading = "paper-flow 登记"
    block = start + "\n" + entry + "\n" + end
    if start in existing or end in existing:
        if existing.count(start) != 1 or existing.count(end) != 1 or existing.index(start) > existing.index(end):
            raise ValueError("登记条目的受管标记不完整，保留人工索引")
        before = existing[:existing.index(start)]
        after = existing[existing.index(end) + len(end):]
        sections = list(re.finditer(r"^#{1,2}\s+(.+?)\s*$", before, re.M))
        if sections and sections[-1][1] == heading:
            return before + block + after
        return register_entry(before + after, note, guide, backlog, unique_stem)
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$", existing, re.M)
    if match:
        next_heading = re.search(r"^#{1,2}\s", existing[match.end():], re.M)
        position = match.end() + next_heading.start() if next_heading else len(existing)
        return existing[:position].rstrip("\n") + "\n\n" + block + "\n\n" + existing[position:]
    return existing.rstrip("\n") + f"\n\n## {heading}\n\n" + block + "\n"


def atomic_write(path: Path, content: str, previous: bytes | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        current = path.read_bytes() if path.exists() else None
        if current != previous:
            raise RuntimeError(f"写入前文件已变化，保留新版本：{path}")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _update_locked(path: Path, transform, check: bool) -> bool:
    previous = path.read_bytes() if path.exists() else None
    existing = previous.decode("utf-8-sig") if previous is not None else None
    content = transform(existing)
    changed = existing != content
    if changed and not check:
        atomic_write(path, ("\ufeff" if previous and previous.startswith(b"\xef\xbb\xbf") else "") + content, previous)
    print(f"{'待更新' if check and changed else '已更新' if changed else '无变化'}：{path}")
    return changed


def _update(path: Path, transform, check: bool) -> bool:
    if check:
        return _update_locked(path, transform, True)
    with file_lock(path):
        return _update_locked(path, transform, check)


def handler(args: argparse.Namespace) -> int:
    vault = resolve_vault(args.vault)
    options = settings(vault)
    if not options.notes.is_dir():
        raise ValueError(f"文献笔记目录不存在：{options.notes}")
    note_path = vault_path(vault, args.note, "--note") if args.note else None
    target = vault_path(vault, args.index, "--index") if args.index else (options.maintained[0] if options.maintained else None)
    if (args.guide is not None or args.backlog is not None or args.index is not None) and note_path is None:
        raise ValueError("--guide、--backlog 和 --index 需要同时指定 --note")
    if note_path and (not note_path.is_file() or not note_path.is_relative_to(options.notes)
                      or not is_literature_note(note_path, options.notes)):
        raise ValueError("--note 应为配置文献笔记目录内已有的 Markdown 文献笔记；assets 与 Excalidraw 文件不参与登记")
    if note_path and target is None:
        raise ValueError("登记需要 --index 或 index.maintained_indexes")
    protected = set(options.maintained) | ({target} if note_path and target else set())
    if options.output.suffix.lower() != ".md" or any(path.suffix.lower() != ".md" for path in protected):
        raise ValueError("自动总索引与人工索引应使用 .md 文件")
    if options.output.is_relative_to(options.notes) or options.output in protected:
        raise ValueError("自动总索引输出与文献笔记目录或人工索引重叠")
    if any(path.is_relative_to(options.notes) for path in protected):
        raise ValueError("人工索引与文献笔记目录重叠")
    notes: list[Note] = []
    errors = 0
    changed = False
    # A broken/unreadable note must not publish a silently incomplete total ledger.
    try:
        for path in sorted(options.notes.rglob("*.md"), key=lambda p: (p.as_posix().casefold(), p.as_posix())):
            if not is_literature_note(path, options.notes):
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(options.notes) or path.is_symlink():
                raise ValueError(f"笔记路径通过符号链接离开目录，保留索引：{path}")
            notes.append(read_note(path, vault))
        changed |= _update(options.output, lambda old: generated_update(old, render(notes, options.aliases)), args.check)
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        print(f"自动总索引失败：{error}", file=sys.stderr)
        errors += 1
    if note_path and target:
        try:
            if not target.is_file():
                raise ValueError(f"人工索引不存在，请先创建或指定已有索引：{target}")
            note = read_note(note_path, vault)
            guide = args.guide if args.guide is not None else note.guide
            if not guide.strip():
                raise ValueError("笔记缺少导读，请用 --guide 指定")
            unique_stem = sum(p.stem.casefold() == note_path.stem.casefold() and is_literature_note(p, options.notes) for p in options.notes.rglob("*.md")) == 1
            changed |= _update(target, lambda old: register_entry(old or "", note, guide, args.backlog, unique_stem), args.check)
        except (OSError, UnicodeError, ValueError, RuntimeError) as error:
            print(f"人工索引登记失败：{error}", file=sys.stderr)
            errors += 1
    return 2 if errors else 1 if args.check and changed else 0


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--check", action="store_true", help="预览总索引和登记更新；有待更新时返回 1")
    parser.add_argument("--note", metavar="PATH", help="登记单篇笔记，使用 Vault 相对路径")
    parser.add_argument("--index", metavar="PATH", help="已有人工索引，默认使用配置中的首个入口")
    parser.add_argument("--guide", help="登记导读；缺省提取笔记看板或一句话")
    parser.add_argument("--backlog", metavar="REASON", help="登记到待补收录，附上待确认事项")
    parser.set_defaults(handler=handler)
