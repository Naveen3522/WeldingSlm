"""Any answer as a Word document: the question, the answer with its tables, and the numbered sources it cites.

The page sends what it shows (text blocks and tables, in order, and the sources numbered as the text numbers them); this lays them out. Nothing is looked up or added, so the
document says exactly what the answer said, with a note to check values against the cited documents.

    answer_docx(question, label, blocks, sources, who) -> bytes
"""
import datetime
import io
import re

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

MAX_BLOCKS, MAX_TEXT, MAX_ROWS, MAX_COLS, MAX_SOURCES = 80, 20000, 80, 8, 40
BOLD = re.compile(r"(\*\*[^*]+\*\*)")
CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
HEADING = re.compile(r"^\s*#{2,3}\s+")
BULLET = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+")
NOTE = "Prepared by Weld Assist from the reference documents. Check every value against the cited document before relying on it."


def _text(value, limit=MAX_TEXT):
    return CONTROL.sub("", str(value or ""))[:limit]


def _runs(paragraph, text):
    for part in BOLD.split(text):
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            paragraph.add_run(part[2:-2]).bold = True
        elif part:
            paragraph.add_run(part)


def _markdown(document, text):
    for line in _text(text).split("\n"):
        if not line.strip():
            continue
        if HEADING.match(line):
            document.add_heading(HEADING.sub("", line, count=1).strip(), 3)
        elif BULLET.match(line):
            _runs(document.add_paragraph(style="List Bullet"), BULLET.sub("", line, count=1))
        else:
            _runs(document.add_paragraph(), line.strip())


def _table(document, block):
    header = [_text(h, 80) for h in (block.get("header") or [])][:MAX_COLS]
    rows = [[_text(c, 600) for c in row][:MAX_COLS] for row in (block.get("rows") or [])][:MAX_ROWS]
    if not header:
        return
    if block.get("title"):
        title = document.add_paragraph()
        title.add_run(_text(block["title"], 200)).bold = True
    table = document.add_table(rows=1, cols=len(header))
    table.style = "Light Grid Accent 1"
    for i, label in enumerate(header):
        cell = table.rows[0].cells[i]
        cell.text = ""
        cell.paragraphs[0].add_run(label).bold = True
    for row in rows:
        cells = table.add_row().cells
        for i in range(len(header)):
            cells[i].text = row[i] if i < len(row) else ""
    document.add_paragraph()


def answer_docx(question, label, blocks, sources, who=""):
    document = docx.Document()
    document.styles["Normal"].font.name = "Calibri"
    document.styles["Normal"].font.size = Pt(11)
    document.add_heading("Weld Assist", 1)

    asked = document.add_paragraph()
    asked.add_run("Question: ").bold = True
    asked.add_run(_text(question, 800))
    kind = document.add_paragraph()
    run = kind.add_run(_text(label, 80) or "From the documents")
    run.italic = True

    for block in (blocks or [])[:MAX_BLOCKS]:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "table":
            _table(document, block)
        elif block.get("type") == "text":
            _markdown(document, block.get("text"))

    cited = [s for s in (sources or []) if isinstance(s, dict)][:MAX_SOURCES]
    if cited:
        document.add_heading("Sources", 2)
        for s in cited:
            name = re.sub(r"^\d+_", "", _text(s.get("source"), 200).rsplit(".", 1)[0]).replace("_", " ")
            section = _text(s.get("heading"), 200).split(" > ")[-1]
            line = document.add_paragraph()
            line.add_run(f"[{int(s.get('n') or 0)}] ").bold = True
            line.add_run(name + (f" / {section}" if section else ""))

    foot = document.add_paragraph()
    foot.alignment = WD_ALIGN_PARAGRAPH.LEFT
    stamp = f"{datetime.date.today():%d %B %Y}" + (f", {_text(who, 60)}" if who else "")
    note = foot.add_run(f"{NOTE} ({stamp})")
    note.italic = True
    note.font.size = Pt(9)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()
