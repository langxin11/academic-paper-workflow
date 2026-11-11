"""Match a complete quote in a PDF text block and retain character geometry."""

from dataclasses import dataclass
from itertools import groupby
import re
import unicodedata

import pymupdf as pdf


_TRANSLATE = str.maketrans({"\u00ad": "", "‐": "-", "‑": "-", "‘": "'", "’": "'", "“": '"', "”": '"'})


def fold(text):
    return unicodedata.normalize("NFKC", text).translate(_TRANSLATE).casefold()


def normalized(text):
    text = re.sub(r"(?<=\w)[-\u00ad]\s*[\r\n]+\s*(?=\w)", "", text)
    return " ".join(fold(text).split())


def word_cut(left, right):
    # A quote ending in "10" cannot attest a printed value of "100".
    return bool(left and right and left.isascii() and right.isascii()
                and left.isalnum() and right.isalnum())


@dataclass
class Line:
    direction: tuple
    cells: list  # (span number, span, raw character)
    box: pdf.Rect


def split_line(raw):
    cells = []
    box = pdf.Rect()
    horizontal = raw["dir"][0] > 0.99
    for span_no, span in enumerate(raw["spans"]):
        for char in span["chars"]:
            rect = pdf.Rect(char["bbox"])
            if horizontal and cells and rect.x0 - box.x1 > 2 * max(rect.height, box.height):
                yield Line(raw["dir"], cells, box)
                cells, box = [], pdf.Rect()
            cells.append((span_no, span, char))
            box |= rect
    if cells:
        yield Line(raw["dir"], cells, box)


def blocks(page):
    flags = pdf.TEXTFLAGS_RAWDICT & ~pdf.TEXT_PRESERVE_IMAGES
    for block in page.get_text("rawdict", flags=flags)["blocks"]:
        columns = []
        for raw in block.get("lines", []):
            for line in split_line(raw):
                # Join only vertically successive, overlapping lines in the same text block.
                compatible = []
                for col in columns:
                    prev = col[-1]
                    a, b = prev.box, line.box
                    if (prev.direction == line.direction and line.direction[0] > 0.99
                            and b.y0 > a.y0 + 0.25 * min(a.height, b.height)
                            and b.y0 - a.y1 < 1.5 * max(a.height, b.height)
                            and min(a.x1, b.x1) > max(a.x0, b.x0)):
                        compatible.append(col)
                if len(compatible) == 1:
                    compatible[0].append(line)
                else:
                    columns.append([line])
        yield from columns


def stream(lines, keep_hyphen):
    letters, origins = [], []
    for li, line in enumerate(lines):
        if letters:
            starts_word = fold(line.cells[0][2]["c"])[:1].isalpha()
            if letters[-1] == "-" and len(letters) > 1 and letters[-2].isalpha() and starts_word:
                if not keep_hyphen:
                    letters.pop()
                    origins.pop()
            elif not letters[-1].isspace() and not lines[li-1].cells[-1][2]["c"].endswith("\u00ad"):
                letters.append(" ")
                origins.append(None)
        for ci, (_, _, char) in enumerate(line.cells):
            for ch in fold(char["c"]):
                if ch.isspace():
                    if letters and letters[-1] == " ":
                        continue
                    ch = " "
                letters.append(ch)
                origins.append((li, ci))
    return "".join(letters), origins


def geometry(lines, origins):
    # Include the intervening printed punctuation, e.g. a dehyphenated line ending.
    points = [point for point in origins if point is not None]
    if not points:
        return []
    (first, start), (last, end) = points[0], points[-1]
    quads = []
    for i in range(first, last + 1):
        line = lines[i]
        segment = line.cells[start if i == first else 0 : end + 1 if i == last else None]
        for _, cells in groupby(segment, key=lambda cell: cell[0]):
            cells = list(cells)
            quads.append(pdf.recover_span_quad(line.direction, cells[0][1], [c[2] for c in cells]))
    return quads


def bounds(quads):
    rect = pdf.Rect()
    for quad in quads:
        rect |= quad.rect
    return rect


def location(rect):
    return "[" + ", ".join(f"{v:.1f}" for v in rect) + "]"


def locate(page, request):
    needle = normalized(request.quote)
    before, after = normalized(request.before), normalized(request.after)
    matches = {}
    full = {}
    prefix, prefix_box = "", None
    for block_no, lines in enumerate(blocks(page)):
        for keep_hyphen in (False, True):
            text, positions = stream(lines, keep_hyphen)
            cursor = 0
            while (pos := text.find(needle, cursor)) != -1:
                end = pos + len(needle)
                cursor = pos + 1
                if (word_cut(text[pos-1:pos], needle[0])
                        or word_cut(needle[-1], text[end:end+1])
                        or (needle[-1].isdigit() and re.match(r"[.,]\d", text[end:]))):
                    continue
                quads = geometry(lines, positions[pos:end])
                if not quads:
                    continue
                rect = bounds(quads)
                key = (block_no, positions[pos], positions[end-1])
                full[key] = rect
                if (text[:pos].rstrip().endswith(before) and text[end:].lstrip().startswith(after)
                        and (request.region is None or rect in pdf.Rect(request.region))):
                    matches[key] = quads
            # Direct prefix only for diagnostics; never write a partial match.
            if not full and len(prefix) < len(needle):
                hit = text.find(needle[:1])
                while hit >= 0:
                    length = 1
                    while length < len(needle) and hit+length < len(text) and needle[length] == text[hit+length]:
                        length += 1
                    if length > len(prefix):
                        quads = geometry(lines, positions[hit:hit+length])
                        if quads:
                            prefix, prefix_box = needle[:length], bounds(quads)
                    hit = text.find(needle[:1], hit+1)
    if len(matches) == 1:
        return next(iter(matches.values()))
    if full:
        places = ", ".join(location(box) for box in full.values())
        raise ValueError(f"完整匹配 {len(full)} 处，限定后 {len(matches)} 处；引文 {request.quote!r}，位置 {places}")
    detail = f"；最长实际匹配前缀 {prefix!r} {location(prefix_box)}" if prefix_box is not None else ""
    raise ValueError(f"完整匹配 0 处；引文 {request.quote!r}{detail}")
