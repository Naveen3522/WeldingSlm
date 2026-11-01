"""A WPS draft for resistance spot welding, laid out as document 08 says a compliant WPS must be: its eleven data groups and their suggested fields.

What the situation and the parameter guide give is filled in and labelled as a starting estimate; every other field says "engineer to confirm".
The draft carries the document's own status ("DRAFT: not yet evaluated or approved for any use") and states that it is not qualified. It is a
starting document for a procedure qualification, never a qualified procedure.

    build(scenario, knowledge, formulas, today) -> Draft          to_docx(draft, who) -> bytes
"""
import datetime
import io
import re
from dataclasses import dataclass, field

from welding_ai.agents import parameters, slots

DOC = "08_"
TO_CONFIRM = "[engineer to confirm]"
GROUPS = "3. The WPS Data Groups"
STANDARDS = "2. The Governing Standards"
STATUS = "9. Digital Record"
LOW, HIGH = parameters.SWEEP_MM


@dataclass
class Draft:
    refused: str = ""
    rows: list = field(default_factory=list)       # [(group, field, value, why it matters, filled)]
    standards: list = field(default_factory=list)  # [(standard, how it is used)]
    status: str = ""
    notes: list = field(default_factory=list)
    chunks: list = field(default_factory=list)

    @property
    def filled(self):
        return sum(1 for r in self.rows if r[4])


def _fields(text):
    """The suggested fields of a data group, split at commas that are not inside brackets."""
    parts, depth, cur = [], 0, ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [re.sub(r"^\s*and\s+", "", p).strip() for p in parts if p.strip()]


def _material_words(s):
    seen = []
    for sheet in s.sheets:
        word = slots.MATERIALS[sheet.material][1] if sheet.material else ""
        if word and word not in seen:
            seen.append(word)
    return ", ".join(seen) or ", ".join(slots.MATERIALS[m][1] for m in s.materials)


def _grades(s):
    """The material families, with any grade the person actually wrote ("DP600") beside the family it was read as."""
    families = _material_words(s)
    written = [w for w, _read, assumed in s.readings if assumed and re.search(r"\d", w)]
    return families + (f" (as stated: {', '.join(written)})" if families and written else "")


def build(s, knowledge, formulas, today=None):
    """-> Draft. Refused (with the reason) unless the situation is resistance spot welding."""
    d = Draft()
    if s.process not in ("", "rsw"):
        d.refused = ("The documents give a WPS content list for resistance spot welding (Chapter 8), so that is the only WPS I can lay out. "
                     f"For {slots.PROCESSES[s.process][1]} I have no such list.")
        return d
    groups, standards, statuses = knowledge.table(DOC, GROUPS), knowledge.table(DOC, STANDARDS), knowledge.table(DOC, STATUS)
    if groups is None:
        d.refused = "The WPS data-group table (Chapter 8, section 3) could not be found in the documents."
        return d
    d.chunks = [t["chunk_ids"][0] for t in (groups, standards, statuses) if t]
    today = today or datetime.date.today()

    values = None
    if s.sheets:
        r = parameters.compute(s, list(parameters.ALL), formulas, knowledge)
        values = None if r.refused else {q["name"]: q for q in r.quantities}
        if r.refused:
            d.notes.append("No starting values were filled in: " + r.refused)
        else:
            d.chunks += [c for c in r.chunks if c not in d.chunks]
    else:
        d.notes.append("No sheet thickness was given, so the material, joint, electrode and parameter fields are left for the engineer.")

    def q(name):
        return next((v for k, v in (values or {}).items() if k.startswith(name)), None)

    def start(name):
        v = q(name)
        return f"{v['text']} {v['unit']}".strip() if v else ""

    prefill = {
        ("identification", "wps number"): "[to be assigned]", ("identification", "revision"): "A",
        ("identification", "approval status"): "DRAFT",
        ("identification", "date"): today.isoformat(),
        ("process", "process name"): "Resistance spot welding; ISO 4063 reference number 21",
        ("parent materials", "grades"): _grades(s),
        ("parent materials", "thicknesses"): " and ".join(f"{x.thickness_mm:g} mm" for x in s.sheets),
        ("parent materials", "coatings"): s.coating_word if s.coating else "",
        ("joint", "lap/stack-up"): ("" if not s.sheets else f"Lap joint, {len(s.sheets)} sheets" if len(s.sheets) > 1 else f"Lap joint, two sheets of {s.sheets[0].thickness_mm:g} mm (assumed: one thickness was given)"),
        ("joint", "pitch/spacing"): (f"not less than {start('Minimum spacing')} (16 x t, parameter guide)" if q("Minimum spacing") else ""),
        ("electrodes", "geometry"): (f"face diameter about {start('Electrode face')} (5 x sqrt(t), a starting size)" if q("Electrode face") else ""),
    }
    force, current, weld_time = q("Electrode force"), q("Weld current"), q("Weld time")
    if force and current and weld_time:
        prefill[("parameters", "force")] = (f"STARTING ESTIMATES for qualification trials, not qualified values: force {force['text']} {force['unit']}; "
                                            f"current {current['text']}; time {weld_time['text']}. Pulse pattern and tolerance bands come from the procedure qualification.")

    for row in groups["rows"]:
        group, suggested, why = row[0], row[1], row[2]
        for item in _fields(suggested):
            value = ""
            for (g, key), text in prefill.items():
                if g == group.lower() and key in item.lower() and text:
                    value = text
                    break
            d.rows.append((group, item, value or TO_CONFIRM, why, bool(value)))

    if standards:
        d.standards = [(r[0], r[4]) for r in standards["rows"]]
    if statuses:
        d.status = next((f"{r[0]}: {r[1]}" for r in statuses["rows"] if r[0] == "DRAFT"), "DRAFT")
    if parameters.family_of(s)[0] in ("hsla", "ahss", "zinc", "mixed"):
        d.notes.append("The parameter guide says this material needs its own procedure qualification; a mild-steel schedule must not be transferred to it.")
    if s.sheets and any(not LOW <= x.thickness_mm <= HIGH for x in s.sheets):
        d.notes.append(f"A sheet is outside {LOW:g} to {HIGH:g} mm, the range the guide covers, so no starting values were filled in.")
    return d


def to_docx(d, who="", when=None):
    """The draft as a Word document: a banner saying it is not qualified, the data-group table, the governing standards and the notes."""
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    when = when or datetime.datetime.now()
    doc = docx.Document()
    for section in doc.sections:
        section.left_margin = section.right_margin = docx.shared.Cm(1.8)
    doc.add_heading("Welding Procedure Specification: resistance spot welding", 0)
    banner = doc.add_paragraph()
    run = banner.add_run("DRAFT. Not qualified. " + (d.status.split(": ", 1)[1] if ": " in d.status else "Under development.")
                         + " A procedure qualification (ISO 15614-12) and engineer approval are required before this is used for production.")
    run.bold = True
    run.font.color.rgb = RGBColor(0xB4, 0x23, 0x18)
    doc.add_paragraph(f"Prepared from the reference documents by Weld Assist{(' for ' + who) if who else ''} on {when:%Y-%m-%d %H:%M}. "
                      f"{d.filled} of {len(d.rows)} fields were filled from the situation and the parameter guide; the rest are marked {TO_CONFIRM}.")

    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, ("Data group", "Field", "Value", "Why it matters (Chapter 8)")):
        cell.text = ""
        cell.paragraphs[0].add_run(text).bold = True
    previous = None
    for group, item, value, why, filled in d.rows:
        cells = table.add_row().cells
        cells[0].text = group if group != previous else ""
        cells[1].text, cells[2].text = item, value
        cells[3].text = why if group != previous else ""
        previous = group
        if not filled:
            shade = OxmlElement("w:shd")
            shade.set(qn("w:val"), "clear")
            shade.set(qn("w:color"), "auto")
            shade.set(qn("w:fill"), "FFF3DF")
            cells[2]._tc.get_or_add_tcPr().append(shade)
    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9)

    if d.standards:
        doc.add_heading("Governing standards (Chapter 8, section 2)", level=2)
        for name, use in d.standards:
            doc.add_paragraph(f"{name}: {use}", style="List Bullet")
    if d.notes:
        doc.add_heading("Notes", level=2)
        for n in d.notes:
            doc.add_paragraph(n, style="List Bullet")
    foot = doc.add_paragraph("Sources: Chapter 8 (WPS and Qualification) sections 2, 3 and 9; Chapter 20 (Material and Thickness Parameter Guide) for any starting estimate.")
    foot.alignment = WD_ALIGN_PARAGRAPH.LEFT
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
