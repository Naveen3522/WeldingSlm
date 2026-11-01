"""Section store: every document rebuilt as sections of typed blocks (paragraph / list item / table).

The passage index cuts documents into overlapping ~1,100-character chunks; a numbered procedure or a table can be split
across chunks. Answers that need a whole procedure, a whole table or a whole section are built from this store instead,
and each block remembers which passage (chunk) it appears in so the page can cite and open it.
"""
from welding_ai import paths
import json
import re

import docx
from docx.text.paragraph import Paragraph

from welding_ai.ingestion import ingest

OUT = paths.INDEX_DIR / "sections.json"
_WS = re.compile(r"\s+")


# Collapse whitespace to single spaces and trim.
def _norm(s):
    return _WS.sub(" ", s).strip()


# Reclassify runs of paragraph blocks that form a hand-typed list as "li" blocks, matching the passage packer.
def _typed_lists(blocks, chunk_of_doc, chunk_of):
    """Runs of paragraphs typed as a list ("bullet item", "1. item", "Step 1 - item") become list items, as the passages already treat them."""
    i = 0
    while i < len(blocks):
        if blocks[i]["t"] != "p":
            i += 1
            continue
        j = i
        while j < len(blocks) and blocks[j]["t"] == "p":
            j += 1
        run = blocks[i:j]
        for b, item in zip(run, ingest.typed_list_items([b["text"] for b in run])):
            if item is not None:
                b["t"], b["num"], b["text"] = "li", ingest.marker(b["text"])[0] != "bullet", item
                b["chunk"] = chunk_of(chunk_of_doc, item)
        i = j



# Rebuild every document as a tree of sections, each holding its typed blocks tagged with their source chunk id.
def build(docs_dir=ingest.DOCS_DIR):
    chunks = json.loads((paths.INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
    by_src = {}
    for c in chunks:
        by_src.setdefault(c["source"], []).append((c["chunk_id"], _norm(c["text"].replace("|", " "))))

    # Find which chunk (if any) of this source document contains the start of this text snippet.
    def chunk_of(src, snippet):
        s = _norm(snippet.replace("|", " "))[:60]
        for cid, t in by_src.get(src, ()):
            if s and s in t:
                return cid
        return None

    sections = []
    for p in sorted(docs_dir.rglob("*.docx")):
        if p.name.startswith("~$"):
            continue
        d = docx.Document(str(p))
        hr = ingest.HeadingReader(d)
        trail, cur = [], None

        # Start a new current section under the current heading trail and append it to the output list.
        def new(title):
            nonlocal cur
            cur = {"source": p.name, "path": " > ".join(trail) or p.stem, "title": title, "level": len(trail), "blocks": []}
            sections.append(cur)

        new(p.stem)
        for b in ingest._iter_blocks(d):
            if isinstance(b, Paragraph):
                txt = _norm(b.text)
                if not txt:
                    continue
                style = (b.style.name or "").lower() if b.style is not None else ""
                lvl = hr.level(b)
                if lvl:
                    trail = trail[: lvl - 1] + [txt]
                    new(txt)
                elif "list" in style:
                    cur["blocks"].append({"t": "li", "text": txt, "num": "number" in style, "chunk": chunk_of(p.name, txt)})
                else:
                    cur["blocks"].append({"t": "p", "text": txt, "chunk": chunk_of(p.name, txt)})
            else:
                rows = []
                for r in b.rows:
                    cells, prev = [], None
                    for c in r.cells:
                        if c._tc is prev:
                            continue
                        prev = c._tc
                        cells.append(_norm(c.text))
                    if any(cells):
                        rows.append(cells)
                if len(rows) >= 2:
                    w = max(len(r) for r in rows)
                    rows = [r + [""] * (w - len(r)) for r in rows]
                    cur["blocks"].append({"t": "table", "header": rows[0], "rows": rows[1:], "chunk": chunk_of(p.name, " ".join(rows[1]))})
    for s in sections:
        _typed_lists(s["blocks"], chunk_of_doc=s["source"], chunk_of=chunk_of)
    for i, s in enumerate(sections):
        s["id"] = i
    sections = [s for s in sections if s["blocks"]]
    for i, s in enumerate(sections):
        s["id"] = i
    return sections


if __name__ == "__main__":
    sec = build()
    OUT.write_text(json.dumps(sec, ensure_ascii=False), encoding="utf-8")
    nb = sum(len(s["blocks"]) for s in sec)
    nl = sum(1 for s in sec for b in s["blocks"] if b["t"] == "li")
    miss = sum(1 for s in sec for b in s["blocks"] if b.get("chunk") is None)
    print(f"{len(sec)} sections, {nb} blocks ({nl} list items), {miss} blocks without a chunk -> {OUT}")
