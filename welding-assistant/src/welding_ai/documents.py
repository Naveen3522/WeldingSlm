"""Document service: reading the source .docx files for the in-page preview and the download, and marking the cited passage."""
import copy
import re
import threading

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from welding_ai import paths
from welding_ai.ingestion.ingest import _iter_blocks


# Strip a leading "NN_" ordering prefix and turn underscores into spaces for a display title.
def title_of(name):
    return re.sub(r"^\d+_", "", name.rsplit(".", 1)[0]).replace("_", " ")


# Mark which blocks (paragraphs/list items/table rows) of a document correspond to the cited chunk text.
def mark_passage(blocks, chunk_text):
    """Flag the blocks that make up the cited passage. Short lines (contents entries, headings) are ignored
    unless nothing longer matches, so a heading repeated at the end of a passage is not mistaken for it."""
    norm = lambda s: " ".join(s.split()).lower()
    ctext = norm(chunk_text)
    units = [norm(u) for u in re.split(r"\n+", chunk_text) if len(u.strip()) >= 20]
    first, hits = None, 0
    for min_len in (40, 20):
        first, hits = None, 0
        for i, b in enumerate(blocks):
            b.pop("hit", None)
            b.pop("hit_rows", None)
            if b["t"] in ("p", "li"):
                s = norm(b["x"])
                b["hit"] = len(s) >= min_len and (s in ctext or any(len(u) >= 40 and u in s for u in units))
            elif b["t"] == "table":
                rows_hit = []
                for j, row in enumerate(b["rows"]):
                    r = norm("| " + " | ".join(c.replace("|", "/") for c in row) + " |")
                    if r in ctext:
                        rows_hit.append(j)
                b["hit_rows"] = rows_hit
                b["hit"] = bool(rows_hit)
            if b.get("hit"):
                hits += 1
                if first is None:
                    first = i
        if hits:
            break
    return first, hits


class DocumentService:
    # Track which source names are known and keep a per-document block cache.
    def __init__(self, sources, chunks, docs_dir=None):
        self.sources, self.chunks = set(sources), chunks
        self.docs_dir = docs_dir or paths.DOCS_DIR
        self._cache, self._lock = {}, threading.Lock()

    # True if the name is a known, existing source document.
    def has(self, name):
        return name in self.sources and (self.docs_dir / name).is_file()

    # Read the raw bytes of a source document (for download).
    def raw(self, name):
        return (self.docs_dir / name).read_bytes()

    # Parse a .docx into a flat, cached list of paragraph/list/heading/table blocks.
    def _blocks(self, name):
        with self._lock:
            if name in self._cache:
                return self._cache[name]
        d = docx.Document(str(self.docs_dir / name))
        blocks = []
        for b in _iter_blocks(d):
            if isinstance(b, Paragraph):
                txt = " ".join(b.text.split())
                if not txt:
                    continue
                style = (b.style.name or "").lower() if b.style is not None else ""
                if style.startswith("heading") or style == "title":
                    m = re.search(r"\d+", style)
                    blocks.append({"t": "h", "l": min(int(m.group()) if m else 1, 4), "x": txt})
                elif "list" in style:
                    blocks.append({"t": "li", "x": txt})
                else:
                    blocks.append({"t": "p", "x": txt})
            elif isinstance(b, Table):
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
                if rows:
                    blocks.append({"t": "table", "rows": rows})
        with self._lock:
            self._cache[name] = blocks
        return blocks

    # Build the preview payload for a document, with the cited chunk (if any) flagged.
    def preview(self, name, chunk_id=None):
        """Blocks of the document with the cited passage flagged. Raises KeyError for an unknown document."""
        if not self.has(name):
            raise KeyError(name)
        blocks = copy.deepcopy(self._blocks(name))
        first, hits = None, 0
        try:
            first, hits = mark_passage(blocks, self.chunks[int(chunk_id)]["text"])
        except (TypeError, ValueError, IndexError):
            pass
        return {"name": name, "title": title_of(name), "blocks": blocks, "first_hit": first, "hits": hits}
