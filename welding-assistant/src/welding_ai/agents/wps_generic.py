"""A WPS draft for any process the documents have a chapter for, laid out from that chapter's own facts.

The spot-welding draft follows the data groups document 08 lists. For every other process there is no such list, but each process chapter has what a procedure has to fix:
its parameters (what each controls and what goes wrong when it is off), the consumables and gas it names, and the qualification it needs. This takes those, adds the fields
the documents say a preliminary WPS holds (document 31), and marks every value "[engineer to confirm]": a draft to start a procedure qualification from, never a qualified WPS.
Nothing here names a process or a document; a chapter added tomorrow gets a draft too.

    build(answerer, source, request) -> Draft        to_docx(draft, who) -> bytes
"""
import datetime
import io
import re
from dataclasses import dataclass, field

TO_CONFIRM = "[engineer to confirm]"
PWPS = re.compile(r"proposed recipe:\s*(?P<fields>[^.]+)\.", re.I)
QUALIFICATION = re.compile(r"qualif|consumable|shielding gas|process reference", re.I)
MAX_PARAMETERS = 14


@dataclass
class Draft:
    process: str
    source: str
    request: str
    rows: list = field(default_factory=list)          # [(group, item, what the documents say about it, value)]
    notes: list = field(default_factory=list)
    chunks: list = field(default_factory=list)

    @property
    def filled(self):
        return sum(1 for r in self.rows if r[3] != TO_CONFIRM)


def _pwps_fields(sections):
    """What the documents say a preliminary WPS holds ("material, thickness, joint design, process, ..."), from the sentence that lists it."""
    for section in sections:
        for block in section["blocks"]:
            if block["t"] == "p":
                m = PWPS.search(block["text"])
                if m:
                    parts = re.split(r",\s*(?:and\s+)?|\s+and\s+", m.group("fields"))
                    return [p.strip() for p in parts if 2 < len(p.strip()) < 70], block.get("chunk")
    return [], None


def build(answerer, source, request, today=None):
    facets, topic = answerer.facets, answerer.topics.name(source)
    units = [u for u in answerer.units if u.source == source]
    d = Draft(topic, source, request)
    today = today or datetime.date.today()
    d.rows += [("Identification", "WPS number", "", "[to be assigned]"), ("Identification", "Revision", "", "A"), ("Identification", "Status", "", "DRAFT"),
               ("Identification", "Date", "", today.isoformat()), ("Process", "Process", "The chapter this draft is taken from.", topic)]

    seen = set()
    for u in units:
        if u.kind != "row":
            continue
        controls = next((v for label, v in u.attrs if facets.concept_of(label) == "controls"), "")
        if not controls or u.entity.lower() in seen or len(seen) >= MAX_PARAMETERS:
            continue
        seen.add(u.entity.lower())
        wrong = next((v for label, v in u.attrs if facets.concept_of(label) == "consequence"), "")
        d.rows.append(("Parameters", u.entity, f"Controls: {controls}" + (f". If wrong: {wrong}" if wrong else ""), TO_CONFIRM))
        d.chunks.append(u.chunk)

    for u in units:
        if u.kind == "item" and QUALIFICATION.search(u.attrs[0][0]):
            group = "Qualification" if "qualif" in u.attrs[0][0] else "Consumables and gas"
            d.rows.append((group, u.attrs[0][0].capitalize(), u.attrs[0][1], TO_CONFIRM))
            d.chunks.append(u.chunk)

    fields, chunk = _pwps_fields(answerer.sections)
    for item in fields:
        if not any(item.lower() in r[1].lower() for r in d.rows):
            d.rows.append(("Fields a preliminary WPS holds (document 31)", item.capitalize(), "", TO_CONFIRM))
    d.chunks.append(chunk)
    d.chunks = [c for c in dict.fromkeys(d.chunks) if c is not None]
    if not seen:
        d.notes.append(f"The {topic} chapter has no table of parameters, so the parameter rows could not be listed.")
    d.notes.append("This is a starting point for a preliminary WPS (pWPS) and its procedure qualification, not a qualified procedure. Every value is for the engineer to fix; "
                   "what each parameter controls and what goes wrong when it is off is quoted from the chapter.")
    return d


def to_docx(d, who="", when=None):
    import docx
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    when = when or datetime.datetime.now()
    document = docx.Document()
    for section in document.sections:
        section.left_margin = section.right_margin = docx.shared.Cm(1.8)
    document.add_heading(f"Welding Procedure Specification (draft): {d.process}", 0)
    banner = document.add_paragraph()
    run = banner.add_run("DRAFT. Not qualified. A procedure qualification and engineer approval are required before this is used for production.")
    run.bold = True
    run.font.color.rgb = RGBColor(0xB4, 0x23, 0x18)
    document.add_paragraph(f"Prepared from the reference documents by Weld Assist{(' for ' + who) if who else ''} on {when:%Y-%m-%d %H:%M}. Request: {d.request[:200]}")

    table = document.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, ("Group", "Item", "What the documents say", "Value")):
        cell.text = ""
        cell.paragraphs[0].add_run(text).bold = True
    previous = None
    for group, item, said, value in d.rows:
        cells = table.add_row().cells
        cells[0].text = group if group != previous else ""
        cells[1].text, cells[2].text, cells[3].text = item, said, value
        previous = group
        if value == TO_CONFIRM:
            shade = OxmlElement("w:shd")
            shade.set(qn("w:val"), "clear")
            shade.set(qn("w:color"), "auto")
            shade.set(qn("w:fill"), "FFF3DF")
            cells[3]._tc.get_or_add_tcPr().append(shade)
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for r in paragraph.runs:
                    r.font.size = Pt(9)
    if d.notes:
        document.add_heading("Notes", level=2)
        for note in d.notes:
            document.add_paragraph(note, style="List Bullet")
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()
