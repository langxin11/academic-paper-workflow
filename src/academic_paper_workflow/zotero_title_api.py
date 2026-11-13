"""Read-only Zotero Desktop API fallback; never contacts a remote server."""
from __future__ import annotations

import json
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

from .zotero_titles import TitleMatch, TitleResolutionError, normalize_title, select_candidates

BASE = 'http://127.0.0.1:23119'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _get(route: str):
    if not route.startswith('/api/') or '\\' in route:
        raise TitleResolutionError('Invalid local Zotero API route.')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(urllib.request.Request(BASE + route, headers={'Zotero-API-Version': '3'}), timeout=10) as response:
        raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise TitleResolutionError('Local Zotero API response exceeds the size limit.')
        text = raw.decode('utf-8')
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text.strip()


def _pages(route: str):
    start = 0
    while True:
        separator = '&' if '?' in route else '?'
        rows = _get(f'{route}{separator}limit=100&start={start}')
        if not isinstance(rows, list):
            raise TitleResolutionError('Unexpected local Zotero API list response.')
        yield from rows
        if len(rows) < 100:
            return
        start += len(rows)
        if start > 100000:
            raise TitleResolutionError('Local Zotero API pagination exceeds the limit.')


def _file_path(value) -> Path | None:
    if not isinstance(value, str):
        return None
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost') or parsed.query or parsed.fragment:
        return None
    raw = urllib.request.url2pathname(parsed.path)
    path = Path(raw)
    return path.resolve() if path.is_absolute() and path.suffix.casefold() == '.pdf' and path.is_file() else None


def resolve_api_title(title: str, data_dir: Path) -> TitleMatch:
    """Resolve personal and available group records using shared selection rules.

    Attachment URLs are read as text, never followed. Their file paths come from
    Zotero's association rather than constructing storage paths in a guessed DB.
    """
    if not normalize_title(title):
        raise TitleResolutionError('A non-empty complete title is required.')
    try:
        libraries = [('/api/users/0', None)]
        for group in _pages('/api/users/0/groups'):
            ident = group.get('id') or group.get('data', {}).get('id')
            if str(ident).isdigit():
                libraries.append((f'/api/groups/{ident}', int(ident)))
        candidates = []
        # Query a stable word so punctuation/Unicode dash variants do not make
        # the API's substring search narrower than our shared exact comparison.
        wanted = normalize_title(title)
        words = re.findall(r'[^\W_]+', wanted, re.UNICODE)
        search = max(words, key=len) if words else wanted
        query = urllib.parse.urlencode({'q': search})
        for prefix, library in libraries:
            for item in _pages(f'{prefix}/items/top?{query}'):
                if len(candidates) >= 1000:
                    raise TitleResolutionError('Title query returned too many candidates; use a more distinctive complete title.')
                data = item.get('data', {})
                key = item.get('key') or data.get('key', '')
                if not re.fullmatch('[A-Z0-9]{8}', key) or data.get('itemType') in ('attachment', 'note', 'annotation'):
                    continue
                year = re.search(r'\b(?:18|19|20|21)\d{2}\b', data.get('date', ''))
                candidate = {'title': data.get('title', ''), 'key': key, 'doi': data.get('DOI') or None,
                             'year': year.group() if year else None, 'library_id': library, 'attachments': []}
                candidates.append(candidate)
                if normalize_title(candidate['title']) != wanted:
                    continue
                for child in _pages(f'{prefix}/items/{key}/children'):
                    attachment = child.get('data', {})
                    filename = attachment.get('filename', '')
                    if attachment.get('itemType') != 'attachment' or (attachment.get('contentType') != 'application/pdf' and not filename.casefold().endswith('.pdf')):
                        continue
                    child_key = child.get('key') or attachment.get('key', '')
                    path = None
                    if re.fullmatch('[A-Z0-9]{8}', child_key):
                        try:
                            path = _file_path(_get(f'{prefix}/items/{child_key}/file/view/url'))
                        except urllib.error.HTTPError as exc:
                            if exc.code not in (400, 404):
                                raise
                    if path is not None and attachment.get('linkMode') in ('imported_file', 'imported_url', 0, 1):
                        expected = Path(data_dir).resolve() / 'storage' / child_key
                        if expected.resolve() != expected or not path.is_relative_to(expected):
                            raise TitleResolutionError('Running Zotero library does not match the requested data directory (stored PDF location differs).')
                    candidate['attachments'].append({'key': child_key, 'filename': filename or attachment.get('title', ''),
                                                       'path': path, 'status': 'available' if path else 'local PDF unavailable'})
        return select_candidates(title, candidates)
    except TitleResolutionError:
        raise
    except (OSError, ValueError) as exc:
        raise TitleResolutionError('Read-only local Zotero API unavailable: ' + str(exc)) from exc
