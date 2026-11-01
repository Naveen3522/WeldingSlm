"""Word tables in and out: read the table of an uploaded .docx (after the checks), and write a template or a draft as a .docx.

A file is refused, and nothing is kept, when it is: bigger than 5 MB, password protected, not a real Word file (damaged, or another kind of file renamed to .docx),
or has no table. Small problems are warnings and the table is still used: rows with no field name are skipped.
"""
import io
import zipfile

from docx import Document
from docx.shared import Pt

MAX_BYTES = 5 * 1024 * 1024
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")           # a password-protected .docx is an OLE file, not a zip


class DocxProblem(Exception):
    """The file cannot be used. `message` is safe to show."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def read_table(data, name="The file"):
    """-> {"heads": [...], "rows": [[...], ...], "report": {"found": "Table found. 24 fields in 1 group.", "warnings": [...]}} or raises DocxProblem."""
    if len(data) > MAX_BYTES:
        raise DocxProblem(f"{name} is too large. The limit is 5 MB.")
    if data[:8] == OLE_MAGIC:
        raise DocxProblem(f"{name} is password protected. Remove the password and try again.")
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise DocxProblem(f"{name} is not a valid Word file. It may be damaged, or it is another kind of file renamed to .docx.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if "word/document.xml" not in z.namelist() or z.testzip() is not None:
                raise ValueError("not a Word document")
        document = Document(io.BytesIO(data))
    except Exception:
        raise DocxProblem(f"{name} is not a valid Word file. It may be damaged, or it is another kind of file renamed to .docx.")
    tables = [t for t in document.tables if len(t.rows) >= 2 and len(t.columns) >= 2]
    if not tables:
        raise DocxProblem(f"{name} has no table. Add a table with the field names in the first column.")
    table = max(tables, key=lambda t: len(t.rows) * len(t.columns))              # the biggest table is the form
    grid = [[(c.text or "").strip() for c in row.cells] for row in table.rows]
    heads = grid[0]
    width = len(heads)
    heads = [h or ("Column %d" % (i + 1)) for i, h in enumerate(heads)]
    rows, skipped = [], 0
    for line in grid[1:]:
        line = (line + [""] * width)[:width]
        if not line[0]:
            skipped += 1
            continue
        rows.append(line)
    if not rows:
        raise DocxProblem(f"{name} has no table with field names in the first column.")
    warnings = []
    if skipped:
        warnings.append(f"{skipped} row{'s' if skipped != 1 else ''} {'has' if skipped == 1 else 'have'} no field name. {'It' if skipped == 1 else 'They'} will be skipped.")
    return {"heads": heads, "rows": rows, "report": {"found": f"Table found. {len(rows)} field{'s' if len(rows) != 1 else ''}, {width} columns.", "warnings": warnings}}


def read_xlsx_table(data, name="The file"):
    """-> the same as read_table, from an Excel (.xlsx) file: the first row is the column titles and the first column holds the field names."""
    from welding_ai.core import xlsx
    if len(data) > MAX_BYTES:
        raise DocxProblem(f"{name} is too large. The limit is 5 MB.")
    if data[:8] == OLE_MAGIC:
        raise DocxProblem(f"{name} is password protected. Remove the password and try again.")
    try:
        grid = [r for r in xlsx.read(data) if any(c.strip() for c in r)]
    except Exception:
        raise DocxProblem(f"{name} is not a valid Excel file. It may be damaged, or it is another kind of file renamed to .xlsx.")
    if len(grid) < 2 or max(len(r) for r in grid) < 2:
        raise DocxProblem(f"{name} has no table. Put the column titles in the first row and the field names in the first column.")
    width = max(len(r) for r in grid)
    heads = [(h or "").strip() or ("Column %d" % (i + 1)) for i, h in enumerate((grid[0] + [""] * width)[:width])]
    rows, skipped = [], 0
    for line in grid[1:]:
        line = [c.strip() for c in (line + [""] * width)[:width]]
        if not line[0]:
            skipped += 1
            continue
        rows.append(line)
    if not rows:
        raise DocxProblem(f"{name} has no table with field names in the first column.")
    warnings = []
    if skipped:
        warnings.append(f"{skipped} row{'s' if skipped != 1 else ''} {'has' if skipped == 1 else 'have'} no field name. {'It' if skipped == 1 else 'They'} will be skipped.")
    return {"heads": heads, "rows": rows, "report": {"found": f"Table found. {len(rows)} field{'s' if len(rows) != 1 else ''}, {width} columns.", "warnings": warnings}}


def write_docx(title, heads, rows, note=""):
    """-> the bytes of a .docx holding a heading, the table (the first row in bold) and an optional one-line note. Empty cells stay empty."""
    document = Document()
    document.add_heading(title or "Template", level=1)
    if note:
        paragraph = document.add_paragraph()
        run = paragraph.add_run(note)
        run.italic = True
    table = document.add_table(rows=1, cols=len(heads))
    table.style = "Table Grid"
    for i, head in enumerate(heads):
        cell = table.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(head)
        run.bold = True
        run.font.size = Pt(10)
    for line in rows:
        cells = table.add_row().cells
        for i in range(len(heads)):
            cells[i].text = line[i] if i < len(line) else ""
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()
