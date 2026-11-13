"""Read-only, conservative title resolution against a Zotero data directory."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path
import re
import stat
import sqlite3
import unicodedata


@dataclass(frozen=True)
class TitleMatch:
    pdf: Path
    attachment_key: str | None
    title: str
    item_key: str
    doi: str | None = None
    year: str | None = None
    library_id: int | None = None


class TitleResolutionError(ValueError):
    """No automatic choice is safe; candidates are suitable for user selection."""
    def __init__(self, message: str, candidates: list[dict] | None = None):
        self.candidates = candidates or []
        details = []
        for c in self.candidates:
            files = '; '.join(f"{a['key']}: {a['filename']} [{a['status']}]" for a in c['attachments'])
            if not files:
                files = '未检查附件' if c.get('attachments_checked') is False else '未发现 PDF 附件'
            details.append(f"{c['title']} | 年份={c['year'] or '?'} | DOI={c['doi'] or '?'} | 条目={c['key']} | {files}")
        super().__init__(message + ('\n' + '\n'.join(details) if details else ''))


def normalize_title(title: str) -> str:
    text = unicodedata.normalize('NFKC', unescape(re.sub(r'<[^>]*>', '', title)))
    text = re.sub(r'[《》〈〉「」『』]', '', text)
    text = ''.join(' ' if unicodedata.category(ch) == 'Pd' or ch == '\u2212' else ch for ch in text)
    return ' '.join(text.casefold().split())


def select_candidates(title: str, candidates: list[dict]) -> TitleMatch:
    """Shared conservative selection for SQLite and read-only API backends."""
    wanted = normalize_title(title)
    if not wanted:
        raise TitleResolutionError('请输入非空的完整论文标题。')
    exact = [c for c in candidates if normalize_title(c['title']) == wanted]
    if len(exact) == 1 and len(exact[0]['attachments']) == 1:
        item = exact[0]
        attachment = item['attachments'][0]
        if attachment['path'] is not None:
            return TitleMatch(attachment['path'], attachment['key'], item['title'], item['key'], item.get('doi'), item.get('year'), item.get('library_id'))
    if len(exact) > 1:
        raise TitleResolutionError(f'找到 {len(exact)} 个同名条目，请根据年份、DOI 或条目键明确选择；以下最多列出 5 个。', exact[:5])
    if exact:
        count = len(exact[0]['attachments'])
        if count == 0:
            message = '已匹配完整标题，但未发现 PDF 附件。请先在 Zotero 中关联 PDF。'
        elif count > 1:
            message = f'已匹配完整标题，但关联了 {count} 份 PDF。请明确选择附件键。'
        else:
            message = '已匹配完整标题，但本地 PDF 不可用。请检查文件是否已下载、移动或使用了相对链接路径。'
        raise TitleResolutionError(message, exact)
    ranked = []
    for candidate in candidates:
        norm = normalize_title(candidate['title'])
        score = SequenceMatcher(None, wanted, norm).ratio()
        if norm and (wanted in norm or norm in wanted or score >= .55):
            ranked.append((score, candidate))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]['key']))
    nearby = [dict(candidate, attachments_checked=False) for _, candidate in ranked[:5]]
    if nearby:
        raise TitleResolutionError('未找到完整标题匹配，仅找到以下近似候选；请确认完整标题或条目键，不会自动选择。', nearby)
    raise TitleResolutionError('未找到与该完整标题匹配或足够接近的条目。请检查标题拼写及目标文献库。')


def _attachment_path(data_dir: Path, key: str, raw: str) -> tuple[Path | None, str]:
    if raw.startswith('storage:'):
        # Stored names may not escape their attachment's directory.
        if not re.fullmatch(r'[A-Za-z0-9]+', key):
            return None, 'unsafe attachment key'
        storage_dir = data_dir / 'storage'
        attachment_dir = storage_dir / key
        # Reject both symlinks and Windows junction/reparse points on the path.
        for directory in (storage_dir, attachment_dir):
            if directory.exists() and (directory.is_symlink() or
                getattr(directory.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)):
                return None, 'unsafe storage junction or symlink'
        relative = raw[len('storage:'):]
        if Path(relative).is_absolute() or '..' in relative.replace('\\', '/').split('/'):
            return None, 'unsafe storage path'
        root = attachment_dir.resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            return None, 'unsafe storage path'
    elif raw.startswith('attachments:'):
        return None, 'relative linked file requires its configured Zotero base directory'
    else:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            return None, 'unresolved linked path'
        path = path.resolve()
    if path.suffix.casefold() != '.pdf':
        return None, 'not a PDF path'
    return (path, 'available') if path.is_file() else (None, 'missing file')


def resolve_title(title: str, data_dir: Path) -> TitleMatch:
    """Choose only one full normalized title and one live PDF attachment.

    Uses SQLite's normal read-only/WAL semantics. Never copies the live database,
    opens it immutable, or writes to Zotero. Relative linked paths require explicit
    resolution outside this function rather than guessing a user's profile.
    """
    wanted = normalize_title(title)
    if not wanted:
        raise TitleResolutionError('A non-empty complete title is required.')
    data_dir = Path(data_dir).resolve()
    database = data_dir / 'zotero.sqlite'
    if not database.is_file():
        raise TitleResolutionError(f'Zotero database not found: {database}')
    try:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1)) as conn:
            conn.execute('PRAGMA query_only=ON')
            rows = conn.execute('''SELECT i.itemID, i.key, i.libraryID, f.fieldName, v.value
                FROM items i JOIN itemTypes t ON t.itemTypeID=i.itemTypeID
                JOIN itemData d ON d.itemID=i.itemID
                JOIN fields f ON f.fieldID=d.fieldID
                JOIN itemDataValues v ON v.valueID=d.valueID
                WHERE t.typeName NOT IN (?, ?, ?)
                AND NOT EXISTS (SELECT 1 FROM deletedItems x WHERE x.itemID=i.itemID)
                AND f.fieldName IN (?, ?, ?)''',
                ('attachment', 'note', 'annotation', 'title', 'date', 'DOI')).fetchall()
            items: dict[int, dict] = {}
            for ident, key, library, field, value in rows:
                item = items.setdefault(ident, {'id': ident, 'key': key, 'library_id': library,
                    'title': '', 'date': '', 'doi': None, 'attachments': []})
                item[{'DOI': 'doi'}.get(field, field)] = value
            ranked = []
            exact_ids = {ident for ident, item in items.items() if normalize_title(item['title']) == wanted}
            for item in items.values():
                norm = normalize_title(item['title'])
                if not norm or (exact_ids and item['id'] not in exact_ids):
                    continue
                score = 1.0 if exact_ids else SequenceMatcher(None, wanted, norm).ratio()
                if norm == wanted or wanted in norm or norm in wanted or score >= .55:
                    item['exact'] = norm == wanted
                    item['score'] = score
                    year = re.search(r'\b(?:18|19|20|21)\d{2}\b', item['date'])
                    item['year'] = year.group() if year else None
                    ranked.append(item)
            exact = [i for i in ranked if i['exact']]
            candidates = exact or sorted(ranked, key=lambda i: (-i['score'], i['key']))[:10]
            for item in candidates:
                attachments = conn.execute('''SELECT i.key, a.path, a.contentType FROM itemAttachments a
                    JOIN items i ON i.itemID=a.itemID WHERE a.parentItemID=?
                    AND NOT EXISTS (SELECT 1 FROM deletedItems x WHERE x.itemID=a.itemID)''', (item['id'],)).fetchall()
                for key, raw, content_type in attachments:
                    raw = raw or ''
                    if content_type != 'application/pdf' and not raw.casefold().endswith('.pdf'):
                        continue
                    path, status = _attachment_path(data_dir, key, raw)
                    item['attachments'].append({'key': key, 'filename': raw.replace('\\', '/').rsplit('/', 1)[-1], 'path': path, 'status': status})
            return select_candidates(title, candidates)
    except sqlite3.Error as exc:
        if 'locked' in str(exc).casefold() or 'busy' in str(exc).casefold():
            from .zotero_title_api import resolve_api_title
            return resolve_api_title(title, data_dir)
        raise TitleResolutionError('Zotero SQLite could not be read (possibly locked). Close Zotero and retry, or use the read-only local API fallback. ' + str(exc)) from exc
