"""Builds the answer to a described situation, section by section, from the documents.

Every line is a table row or a passage quoted from a document, or a number the parameter guide's formulas gave. This module only decides which
sections apply to the situation and says how it read the description; it states no welding fact of its own. What it could not find, and what it
needs to know to narrow things down, are listed at the end instead of being papered over.

    assemble(scenario, knowledge, formulas) -> Answer
    parameters_answer(scenario, wanted, knowledge, formulas) -> Answer
"""
import re
from dataclasses import dataclass, field

from welding_ai.agents import parameters, slots, troubleshooting
from welding_ai.answering import answering

MAX_SOURCES = 8
MIN_COVER = 0.55                     # a retrieved passage must cover this much of the query to be quoted
PASSAGE_CHARS = 700
POOL = 25                            # passages searched before the chapters are picked

# which chapters answer a question about a process or a material (file-name prefixes of the documents)
PROCESS_DOCS = {"rsw": ("01_", "02_", "10_", "12_", "13_", "20_"), "gmaw": ("39_", "47_"), "gtaw": ("41_", "47_"), "fcaw": ("40_", "47_"),
                "smaw": ("38_", "47_"), "saw": ("43_", "47_"), "plasma": ("42_", "47_"), "laser": ("52_", "54_"), "fsw": ("57_",)}
MATERIAL_DOCS = {"mild": ("23_",), "hsla": ("23_",), "ahss": ("23_",), "stainless": ("24_",), "aluminium": ("25_",),
                 "titanium": ("26_",), "nickel": ("26_",), "copper": ("26_",), "cast_iron": ("26_",)}
SELECTION_DOCS = ("68_", "73_", "55_", "51_")


@dataclass
class Answer:
    route: str
    blocks: list = field(default_factory=list)          # [("text", markdown) | ("table", {"title", "header", "rows"})]
    sources: list = field(default_factory=list)         # hits for the sources panel; [n] in the text points at sources[n - 1]
    refused: str = ""                                   # why nothing could be answered, or ""


class Citer:
    """Hands out [n] numbers for the chunks an answer quotes, in order of first use."""

    def __init__(self, knowledge):
        self.knowledge, self.hits = knowledge, []

    def ref(self, chunk_id, focus=""):
        for n, h in enumerate(self.hits, 1):
            if h["chunk_id"] == chunk_id:
                return f"[{n}]"
        if len(self.hits) >= MAX_SOURCES:
            return ""
        self.hits.append(self.knowledge.hit(chunk_id, focus))
        return f"[{len(self.hits)}]"


def _title(source):
    return re.sub(r"^\d+_", "", source.rsplit(".", 1)[0]).replace("_", " ")


def passage(knowledge, query, prefixes=None, min_cover=MIN_COVER):
    """-> {"text", "chunk", "source", "heading"}: the best extractive answer to the query from the chapters named, or None when they give none."""
    index = knowledge.index
    hits = index.search(query, POOL)
    if prefixes:
        hits = [h for h in hits if h["source"].startswith(tuple(prefixes))]
    if not hits or hits[0]["coverage"] < min_cover:
        return None
    comp = answering.compose_extractive(index, query, hits[:5])
    if not comp["text"]:
        return None
    best = hits[comp["units"][0][0] - 1]
    text = re.sub(r"\s*\[\d+\]", "", comp["text"]).strip()
    if len(text) > PASSAGE_CHARS:
        text = text[:PASSAGE_CHARS].rsplit(" ", 1)[0] + "..."
    return {"text": text, "chunk": best["chunk_id"], "source": best["source"], "heading": best["heading"]}


LABELLED = re.compile(r";\s+(?=[A-Z][\w ./()-]{1,40}: )")


def _lines(text):
    """A table row quoted as "Label: value; Label: value" is shown one label to a line; ordinary sentences are left alone."""
    parts = LABELLED.split(text)
    return "\n".join("- " + p.strip() for p in parts) if len(parts) > 2 else text


def _where(found):
    return f"- From {_title(found['source'])}, {found['heading'].split(' > ')[-1]}."


def _sheet_words(s):
    parts = []
    for sheet in s.sheets:
        label = slots.MATERIALS[sheet.material][1] if sheet.material else ""
        parts.append(f"{sheet.thickness_mm:g} mm" + (f" {label}" if label else ""))
    return " and ".join(parts)


def _summary(s):
    bits = []
    if s.process:
        bits.append(slots.PROCESSES[s.process][1])
    if s.sheets:
        bits.append(_sheet_words(s))
    elif s.materials:
        bits.append(", ".join(slots.MATERIALS[m][1] for m in s.materials))
    if s.coating:
        bits.append("coated (" + s.coating_word + ")")
    if s.symptom:
        bits.append("problem: " + s.symptom)
    return "; ".join(bits)


def _reading_lines(s):
    lines = [f"- “{written}”: {read}" + (" *(my assumption: please check)*" if assumption else "") for written, read, assumption in s.readings]
    if s.sheets and not any("paired" in r[1] for r in s.readings):
        lines.append("- Sheets: " + _sheet_words(s))
    return lines


def _row_lines(f, cit, limit=troubleshooting.GENERIC_LIMIT):
    lines = []
    for row in f.rows[:limit]:
        lines.append(f"- **{row['imperfection']}** {cit.ref(row['chunk'], row['imperfection'])}")
        for label, key in (("Mechanism", "mechanism"), ("How to tell", "discriminators"), ("Corrective action", "action")):
            if row[key]:
                lines.append(f"- {label}: {row[key]}")
        if row["chapter"]:
            lines.append(f"- Chapters in the documents: {row['chapter']}")
    return lines


def parameters_answer(s, wanted, knowledge, formulas, cit=None):
    """The starting-values answer on its own (a plain parameter question), or as a section of the scenario answer (pass its Citer so the [n] agree)."""
    r = parameters.compute(s, wanted, formulas, knowledge)
    a = Answer("parameters", refused=r.refused)
    own = cit is None
    cit = cit or Citer(knowledge)
    if r.refused:
        a.blocks.append(("text", f"**Starting values for resistance spot welding**\n{r.refused}"))
        return a
    refs = " ".join(cit.ref(c) for c in r.chunks)
    a.blocks.append(("text", f"**Starting values for resistance spot welding: {_sheet_words(s) or 'sheets'}** {refs}".strip()))
    rows = []
    for q in r.quantities:
        value = f"{q['text']} {q['unit']}".strip() if q["unit"] else q["text"]
        rows.append([q["name"], value, q["steps"] + (f" ({q['extra']})" if q["extra"] else "")])
    if rows:
        a.blocks.append(("table", {"title": "Starting values", "header": ["Quantity", "Value", "How it was calculated"], "rows": rows}))
    lines = [f"- {n}" for n in r.notes]
    lines += [f"- Not given by the documents: {g}" for g in r.gaps]
    if r.caveats:
        lines.append("- What the guide says about this material (quoted):")
        lines += [f"- {label}: {text}" for label, text in r.caveats]
    lines.append("- These are first-trial estimates from a published rule of thumb. The guide says every one must be confirmed by destructive testing and, for production, "
                 "by procedure qualification; none of them replaces a qualified WPS.")
    a.blocks.append(("text", "\n".join(lines)))
    if own:
        a.sources = cit.hits
    return a


def assemble(s, knowledge, formulas, min_cover=MIN_COVER):
    """-> Answer for a described situation. Sections appear only when the situation gives something to look up."""
    a = Answer("scenario")
    cit = Citer(knowledge)
    gaps, ask = [], []
    process_phrase = slots.PROCESSES[s.process][1] if s.process else ""

    a.blocks.append(("text", f"**Here is what the documents say about your situation** ({_summary(s)})\n**How I read it**\n"
                             + "\n".join(_reading_lines(s) or ["- (nothing specific was recognised)"])))

    table_rows = False
    if s.symptom:
        f = troubleshooting.lookup(s.symptom, s.process, knowledge, s.materials)
        if f:
            table_rows = True
            lines = [f"**{s.symptom.capitalize()}: causes and what to do**"] + _row_lines(f, cit)
            if f.ask:
                ask.append(f.ask)
            for p in f.pairs[:3]:
                lines.append(f"- Telling it apart ({p['pair']}): {p['discriminator']} {cit.ref(p['chunk'])}")
            if f.spatter_order:
                lines.append("- The documents give this order for reducing spatter: " + "; ".join(f"{i}. {v}" for i, (v, _w, _y) in enumerate(f.spatter_order, 1))
                             + f" {cit.ref(f.chunks[-1])}")
            a.blocks.append(("text", "\n".join(lines)))
        else:
            gaps.append(f"The defect tables do not list “{s.symptom}” by that name.")
        if s.process and s.process != "rsw" and s.process in PROCESS_DOCS:
            found = passage(knowledge, f"{s.symptom} {process_phrase}", PROCESS_DOCS[s.process], min_cover)
            if found:
                a.blocks.append(("text", f"**In the {process_phrase.split(' (')[0]} chapter**\n{_lines(found['text'])} {cit.ref(found['chunk'], s.symptom)}\n{_where(found)}"))

    params_shown = False
    if s.process == "rsw" and s.sheets:
        p = parameters_answer(s, list(parameters.ALL), knowledge, formulas, cit)
        if p.refused:
            gaps.append(p.refused)
        else:
            params_shown = True
            a.blocks.append(("text", "**Starting values from the parameter guide** (to check your settings against)"))
            a.blocks.extend(p.blocks[1:])

    if s.process != "rsw" or not params_shown:                       # for spot welding the parameter guide's own caveats already cover the material
        for key in s.materials:
            if key not in MATERIAL_DOCS or not (s.process or s.symptom):
                continue
            label = slots.MATERIALS[key][1]
            found = passage(knowledge, f"{label} {s.symptom or 'weldability'} {process_phrase}".strip(), MATERIAL_DOCS[key], min_cover)
            if found:
                a.blocks.append(("text", f"**About {label}**\n{_lines(found['text'])} {cit.ref(found['chunk'], label)}\n{_where(found)}"))
            else:
                gaps.append(f"Nothing specific about {label}" + (f" and {s.symptom}" if s.symptom else "") + " was found.")

    if "process" in s.asks:
        what = " ".join(slots.MATERIALS[m][1] for m in s.materials) or "sheet"
        found = passage(knowledge, f"selecting a joining process for {what} sheet", SELECTION_DOCS, min_cover)
        if found:
            a.blocks.append(("text", f"**Choosing a process**\n{_lines(found['text'])} {cit.ref(found['chunk'], 'process selection')}\n{_where(found)}"))
        else:
            gaps.append("The documents give no direct process recommendation for this material and thickness.")

    for u in s.unclear:
        ask.append(u[0].upper() + u[1:] + "?")
    if not s.process and "process" not in s.asks:
        ask.append("Which welding process is it?")
    if s.process == "rsw" and not s.sheets:
        ask.append("What are the sheet thicknesses?")
    if s.symptom and not s.materials:
        ask.append("What material is it?")

    if gaps or ask:
        lines = ["**What I could not find, and what would narrow this down**"]
        lines += [f"- Not found: {g}" for g in gaps]
        lines += [f"- Please tell me: {q}" for q in ask]
        a.blocks.append(("text", "\n".join(lines)))
    a.blocks.append(("text", "Check every value against the cited document before you rely on it."))
    a.sources = cit.hits
    return a
