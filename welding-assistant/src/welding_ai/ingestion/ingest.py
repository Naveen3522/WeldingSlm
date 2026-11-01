"""Extract text from the welding documents and split it into heading-aware passages.

Same extraction and chunking rules as the training notebook (steps 4-5), so chunk ids match
welding_slm/02_chunks.json.  Run:  python ingest.py
"""
from welding_ai import paths
import json
import os
import re
import sys
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

try:
    import fitz  # PyMuPDF, optional (PDF support)
except ImportError:
    fitz = None

DOCS_DIR = paths.DOCS_DIR
OUT = paths.INDEX_DIR / "chunks.json"
FILE_TYPES = {".docx", ".pdf", ".txt", ".md"}
CHUNK_CHARS, CHUNK_OVERLAP, MIN_CHUNK = 1400, 200, 250
SHORT_SECTION = 60           # a whole section shorter than MIN_CHUNK is kept as one passage if it has at least this many characters


# Walk a docx body in document order, yielding each Paragraph and Table as encountered.
def _iter_blocks(d):
    for child in d.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, d)
        elif child.tag == qn("w:tbl"):
            yield Table(child, d)


# Render a docx table as a padded GitHub-flavored markdown table, collapsing merged cells.
def _table_md(tbl):
    rows = []
    for r in tbl.rows:
        cells, prev = [], None
        for c in r.cells:  # merged cells repeat -> collapse
            t = " ".join(c.text.split())
            if c._tc is prev:
                continue
            prev = c._tc
            cells.append(t.replace("|", "/"))
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "|".join([" --- "] * w) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


class HeadingReader:
    """Which paragraphs of a .docx are headings, and at what level.

    A document that uses Word's heading styles is read by style. Many documents format their headings by hand instead: ordinary
    ("Normal") paragraphs set in bold and larger than the body text. Those are recognised by their formatting, and the level follows the
    size rank (the largest two sizes are level 1, then 2, 3), the same convention the styled documents follow."""

    MAX_LEN = 160

    # Detect whether the document uses Word heading styles; if not, learn the body font size and the
    # distinct larger/bold sizes used for hand-formatted headings.
    def __init__(self, d):
        self.styled = any(self._style_level(p) for p in d.paragraphs)
        self.body, self.sizes = None, []
        if self.styled:
            return
        chars = {}
        for p in d.paragraphs:
            for r in p.runs:
                if r.text.strip() and r.font.size:
                    chars[r.font.size] = chars.get(r.font.size, 0) + len(r.text)
        if not chars:
            return
        self.body = max(chars, key=chars.get)
        self.sizes = sorted({self._size(p) for p in d.paragraphs if self._is_hand_heading(p)}, reverse=True)

    # Heading level (1-based) from the paragraph's Word style name, or None if it isn't a heading style.
    @staticmethod
    def _style_level(par):
        style = (par.style.name or "").lower() if par.style is not None else ""
        if style.startswith("heading") or style == "title":
            m = re.search(r"\d+", style)
            return int(m.group()) if m else 1
        return None

    # The largest run font size used in the paragraph.
    @staticmethod
    def _size(par):
        return max((r.font.size for r in par.runs if r.text.strip() and r.font.size), default=None)

    # True if the paragraph looks like a hand-formatted heading: short, bold throughout, larger than body text.
    def _is_hand_heading(self, par):
        runs = [r for r in par.runs if r.text.strip()]
        text = par.text.strip()
        size = self._size(par)
        return bool(runs and text and len(text) <= self.MAX_LEN and size and self.body and size > self.body and all(r.bold for r in runs))

    # The heading level of a paragraph (1-4), by style if styled, else by hand-heading font-size rank.
    def level(self, par):
        """The heading level of a paragraph (1 to 4), or None for body text."""
        lvl = self._style_level(par)
        if lvl or self.styled or not self.sizes or not self._is_hand_heading(par):
            return lvl
        rank = self.sizes.index(self._size(par))
        return 1 if rank < 2 else min(rank, 4)


BULLET_CHARS = "".join(chr(c) for c in (0x2022, 0x25AA, 0x25E6, 0x2023, 0x25CF, 0x25CB))     # bullet, small square, white bullet, triangular, black circle, white circle


DASHES = chr(0x2014) + chr(0x2013) + "-"
_NUMBERED = re.compile(r"^(\d{1,2})[.)]\s+(\S.*)$")
_WORD_NUMBER = re.compile(r"^([A-Z][a-z]+)\s+(\d{1,2})\s*[:." + DASHES + r"]\s*(\S.*)$")      # "Step 3 - text", "Stage 2: text"


# Detect a hand-typed list marker at the start of a paragraph (bullet char, "3.", "3)" or "Step 3 - ").
def marker(text):
    """(shape, number, rest) when a paragraph starts with a typed list marker, else None. Shapes: 'bullet', 'number' ("3. text", "3) text")
    and 'word:<Word>' ("Step 3 - text"). Many documents type their lists by hand instead of using Word's list style."""
    t = " ".join(text.split())
    if len(t) > 2 and t[0] in BULLET_CHARS and t[1] == " ":
        return "bullet", None, t[2:]
    m = _NUMBERED.match(t)
    if m:
        return "number", int(m.group(1)), m.group(2)
    m = _WORD_NUMBER.match(t)
    if m:
        return "word:" + m.group(1), int(m.group(2)), m.group(3)
    return None


# Identify which consecutive paragraphs form a hand-typed list, and return each item's text (or None).
def typed_list_items(texts, min_run=3):
    """For consecutive paragraph texts, the item text of each paragraph that belongs to a typed list, else None. A bullet paragraph is a list
    item on its own; numbered items ("1.", "Step 1 -") count in a run of at least `min_run` that counts up by one, or in a run of two when the
    paragraph before it ends with a colon, which is the documents' own way of introducing a list."""
    out = [None] * len(texts)
    marks = [marker(t) for t in texts]
    for i, m in enumerate(marks):
        if m and m[0] == "bullet":
            out[i] = m[2]
    i = 0
    while i < len(marks):
        m = marks[i]
        if not m or m[0] == "bullet":
            i += 1
            continue
        j = i + 1
        while j < len(marks) and marks[j] and marks[j][0] == m[0] and marks[j][1] == marks[j - 1][1] + 1:
            j += 1
        introduced = i and not marks[i - 1] and texts[i - 1].rstrip().endswith(":")
        if j - i >= (2 if introduced else min_run):
            for k in range(i, j):
                out[k] = marks[k][2]
        i = j
    return out


# Turn a .docx into a single markdown-ish text: "# heading" lines, "- " list items, and rebuilt tables.
def extract_docx(path):
    d, entries = docx.Document(str(path)), []
    hr = HeadingReader(d)
    for b in _iter_blocks(d):
        if isinstance(b, Paragraph):
            txt = " ".join(b.text.split())
            if not txt:
                continue
            style = (b.style.name or "").lower() if b.style is not None else ""
            lvl = hr.level(b)
            if lvl:
                entries.append(("h", "#" * min(lvl, 4) + " " + txt))
            elif "list" in style:
                entries.append(("li", txt))
            else:
                entries.append(("p", txt))
        else:
            t = _table_md(b)
            if t:
                entries.append(("t", t))
    parts, i = [], 0
    while i < len(entries):
        if entries[i][0] != "p":
            parts.append(("- " if entries[i][0] == "li" else "") + entries[i][1])
            i += 1
            continue
        j = i
        while j < len(entries) and entries[j][0] == "p":
            j += 1
        run = [e[1] for e in entries[i:j]]
        for text, item in zip(run, typed_list_items(run)):
            parts.append(text if item is None else "- " + text)          # keep the marker's words ("Step 3 - ...") in the passage text
        i = j
    return (chr(10) * 2).join(parts)


# Extract PDF text via PyMuPDF, guessing numbered-section headings ("1.2.3 Title") from short lines.
def extract_pdf(path):
    if fitz is None:
        return ""
    parts = []
    with fitz.open(str(path)) as pdf:
        for page in pdf:
            text = page.get_text("text")
            for p in (" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()):
                if len(p) < 90 and re.match(r"^(\d+(\.\d+){0,3})\s+[A-Z]", p):
                    parts.append("#" * min(p.split()[0].count(".") + 1, 4) + " " + p)
                else:
                    parts.append(p)
    return "\n\n".join(parts)


# Dispatch text extraction by file extension (.docx / .pdf / plain text).
def extract_any(path):
    ext = path.suffix.lower()
    if ext == ".docx":
        return extract_docx(path)
    if ext == ".pdf":
        return extract_pdf(path)
    return path.read_text(encoding="utf-8", errors="ignore")


# Split extracted text into (heading-path, body) sections at "#"-level markdown headings.
def split_sections(text):
    sections, trail, buf = [], [], []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.append((" > ".join(trail), body))

    for line in text.split("\n"):
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            flush()
            buf = []
            lvl = len(m.group(1))
            trail = trail[: lvl - 1] + [m.group(2).strip()]
        else:
            buf.append(line)
    flush()
    return sections


# Split an oversized markdown table into row-chunks that each repeat the header and fit within `limit` chars.
def split_table(block, limit):
    lines = block.split("\n")
    head, rows = lines[:2], lines[2:]
    out, cur = [], []
    for r in rows:
        if cur and len("\n".join(head + cur + [r])) > limit:
            out.append("\n".join(head + cur))
            cur = []
        cur.append(r)
    if cur:
        out.append("\n".join(head + cur))
    return out


TABLE_LIMIT, LIST_LIMIT = int(os.environ.get("WELD_TABLE_LIMIT", 4200)), int(os.environ.get("WELD_LIST_LIMIT", 3000))     # a table / list up to this size is kept in one passage
_LEAD = re.compile(r"(:\s*$|\b(following|below|table|lists?|listed|include[sd]?|as follows)\b)", re.I)


# Split a section body into typed atoms (paragraph/table/list), gluing a short lead-in paragraph to the
# table or list that follows it so it doesn't get cut off from what it's introducing.
def _atoms(body):
    """Paragraph blocks -> atoms: ('p', text) | ('table', text) | ('list', text); a short lead-in paragraph is glued to the
    table / list run that follows it."""
    paras = [x.strip() for x in body.split("\n\n") if x.strip()]
    atoms, i = [], 0
    while i < len(paras):
        p = paras[i]
        if p.startswith("|"):
            atoms.append(["table", p])
            i += 1
        elif p.startswith("- "):
            j = i
            while j < len(paras) and paras[j].startswith("- "):
                j += 1
            atoms.append(["list", "\n".join(paras[i:j])])
            i = j
        else:
            atoms.append(["p", p])
            i += 1
    out = []
    for kind, text in atoms:
        if kind in ("table", "list") and out and out[-1][0] == "p" and len(out[-1][1]) <= 700 and _LEAD.search(out[-1][1]):
            lead = out.pop()[1]
            out.append((kind, lead + "\n\n" + text))
        else:
            out.append((kind, text))
    return out


# Split an oversized list into chunks that fit within `limit` chars, keeping any lead-in line with each piece.
def _split_list(text, limit):
    parts, cur = [], []
    lead = ""
    lines = text.split("\n")
    if lines and not lines[0].startswith("- "):
        lead, lines = lines[0], lines[1:]
    for ln in lines:
        if cur and len("\n".join([lead] + cur + [ln])) > limit:
            parts.append("\n".join(([lead] if lead else []) + cur))
            cur = []
        cur.append(ln)
    if cur:
        parts.append("\n".join(([lead] if lead else []) + cur))
    return parts


STRUCTURED = os.environ.get("WELD_STRUCTURED_CHUNKS", "0") == "1"   # scored worse than the plain packer on the 60-question set (see README); off by default


# Pack a section body into chunk-sized passages; the alternate (currently off) atom-aware packer that
# keeps tables/lists intact, vs. pack_v1's simpler paragraph-only packer.
def pack(body, heading, source, structured=None):
    structured = STRUCTURED if structured is None else structured
    if not structured:
        return pack_v1(body, heading, source)
    chunks, cur, keep = [], "", set()

    def flush():
        nonlocal cur
        if cur:
            chunks.append(cur)
            cur = ""

    for kind, text in _atoms(body):
        if kind == "p":
            pieces = [text] if len(text) <= CHUNK_CHARS else [text[i:i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS - CHUNK_OVERLAP)]
            for piece in pieces:
                if len(cur) + len(piece) + 2 <= CHUNK_CHARS:
                    cur = (cur + "\n\n" + piece).strip()
                else:
                    tail = cur[-CHUNK_OVERLAP:] if cur and not cur.startswith("|") else ""
                    flush()
                    cur = (tail + "\n\n" + piece).strip() if tail else piece
            continue
        limit = TABLE_LIMIT if kind == "table" else LIST_LIMIT
        if kind == "table":
            lead, _, tbl = text.rpartition("\n\n") if not text.startswith("|") else ("", "", text)
            pieces = [text] if len(text) <= limit else [(lead + "\n\n" if lead else "") + t for t in split_table(tbl, limit - len(lead))]
        else:
            pieces = [text] if len(text) <= limit else _split_list(text, limit)
        for piece in pieces:
            if cur and len(cur) < 600 and len(cur) + len(piece) + 2 <= limit:
                cur = cur + "\n\n" + piece
            else:
                flush()
                cur = piece
            keep.add(len(chunks))               # this chunk holds a table / list: never dropped for being short
        flush()
    flush()
    return [{"source": source, "heading": heading, "text": c} for i, c in enumerate(chunks) if len(c) >= MIN_CHUNK or i in keep]


# Default passage packer: greedily fill ~CHUNK_CHARS-sized passages from paragraphs, with overlap carried
# forward between consecutive chunks of the same section.
def pack_v1(body, heading, source):
    paras = []
    for p in (x.strip() for x in body.split("\n\n") if x.strip()):
        if len(p) > CHUNK_CHARS and p.startswith("|"):
            paras.extend(split_table(p, CHUNK_CHARS))
        elif len(p) > CHUNK_CHARS:
            paras.extend(p[i:i + CHUNK_CHARS] for i in range(0, len(p), CHUNK_CHARS - CHUNK_OVERLAP))
        else:
            paras.append(p)
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 2 <= CHUNK_CHARS:
            cur = (cur + "\n\n" + p).strip()
        else:
            if cur:
                chunks.append(cur)
            tail = "" if p.startswith("|") or not chunks else chunks[-1][-CHUNK_OVERLAP:]
            cur = (tail + "\n\n" + p).strip() if tail and not tail.lstrip().startswith("|") else p
    if cur:
        chunks.append(cur)
    return [{"source": source, "heading": heading, "text": c} for c in chunks if len(c) >= MIN_CHUNK]


# Pull every table out of a .docx as a standalone record with its section path and surrounding paragraphs.
def extract_tables(path):
    """Every table of a .docx, rebuilt whole (tables are cut across passages in the search index) with its section,
    the paragraph(s) that introduce it and the paragraph that follows it."""
    d = docx.Document(str(path))
    hr = HeadingReader(d)
    trail, recent, tables = [], [], []
    pending_after = None
    for b in _iter_blocks(d):
        if isinstance(b, Paragraph):
            txt = " ".join(b.text.split())
            if not txt:
                continue
            lvl = hr.level(b)
            if pending_after is not None and not pending_after.get("after") and not lvl:
                pending_after["after"] = txt[:600]
            if lvl:
                trail = trail[: lvl - 1] + [txt]
                recent = []
                pending_after = None
            else:
                recent.append(txt)
                recent = recent[-2:]
        else:
            rows = []
            for r in b.rows:
                cells, prev = [], None
                for c in r.cells:
                    if c._tc is prev:
                        continue
                    prev = c._tc
                    cells.append(" ".join(c.text.split()))
                if any(cells):
                    rows.append(cells)
            if len(rows) >= 2:
                w = max(len(r) for r in rows)
                rows = [r + [""] * (w - len(r)) for r in rows]
                t = {"source": path.name, "heading": " > ".join(trail), "title": trail[-1] if trail else path.stem,
                     "lead": " ".join(recent)[:700], "header": rows[0], "rows": rows[1:], "after": ""}
                tables.append(t)
                pending_after = t
                recent = []
    return tables


# Top-level pipeline: extract every source file, section and pack it into passages, then rebuild tables
# whole and link each table to the chunk ids that contain its rows.
def build(docs_dir=DOCS_DIR):
    files = sorted(p for p in docs_dir.rglob("*")
                   if p.is_file() and p.suffix.lower() in FILE_TYPES and not p.name.startswith("~$"))
    chunks, skipped = [], []
    for p in files:
        try:
            text = extract_any(p)
        except Exception as e:  # keep going, report at the end
            skipped.append((p.name, f"{type(e).__name__}: {e}"))
            continue
        if len(text.strip()) < 80:
            skipped.append((p.name, "almost no text (scanned? run OCR first)"))
            continue
        for heading, body in split_sections(text):
            packed = pack(body, heading or p.stem, p.name)
            if not packed and len(body.strip()) >= SHORT_SECTION:          # a short section (a lede, a one-paragraph note) is still content: keep it whole
                packed = [{"source": p.name, "heading": heading or p.stem, "text": body.strip()}]
            chunks.extend(packed)
    for i, c in enumerate(chunks):
        c["chunk_id"] = i
    tables = []
    for p in files:
        if p.suffix.lower() == ".docx":
            try:
                tables.extend(extract_tables(p))
            except Exception as e:
                skipped.append((p.name, f"tables: {type(e).__name__}: {e}"))
    by_src = {}
    for c in chunks:
        by_src.setdefault(c["source"], []).append(c)
    for i, t in enumerate(tables):
        t["table_id"] = i
        row_md = ["| " + " | ".join(c.replace("|", "/") for c in r) + " |" for r in t["rows"]]
        t["chunk_ids"] = [c["chunk_id"] for c in by_src.get(t["source"], []) if any(r in c["text"] for r in row_md[:6] + row_md[-2:])]
    return chunks, skipped, len(files), tables


if __name__ == "__main__":
    docs = Path(sys.argv[1]) if len(sys.argv) > 1 else DOCS_DIR
    chunks, skipped, nfiles, tables = build(docs)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    (OUT.parent / "tables.json").write_text(json.dumps(tables, ensure_ascii=False), encoding="utf-8")
    print(f"{len(tables)} tables rebuilt whole -> tables.json")
    print(f"{nfiles} files -> {len(chunks)} passages, avg {sum(len(c['text']) for c in chunks) // max(len(chunks), 1)} chars -> {OUT}")
    for n, why in skipped:
        print("  skipped:", n, "-", why)
