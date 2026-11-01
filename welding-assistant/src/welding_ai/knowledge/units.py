"""Facts read out of the documents' own structure: every table row, every "Card 1 - title / Symptom: ... / Likely causes: ..." group and every "Label: value" line
is an entity with named attributes. Nothing here knows a document by name or number: a new document that uses a table with a "Cause" column or a
"Likely causes:" line is understood the moment it is indexed.

    extract(sections) -> [Unit]

A Unit is one thing a document says something about: its `entity` (the first cell of a row, the title of a card, the section a labelled line sits in) and what it says,
as `attrs` [(label, value)], where the label is the column header or the line's own label ("failure consequence", "likely causes", "consumables").
"""
import re
from dataclasses import dataclass, field

CARD = re.compile(r"^(?:card|case|scenario|example|symptom)\s*\d+\s*[—–:\-]\s*(?P<title>.+)$", re.I)
LABEL = re.compile(r"^(?P<label>[A-Z][A-Za-z0-9 /&()\-,']{2,45}?):\s+(?P<value>\S.*)$", re.S)
SKIP_SECTION = re.compile(r"sources referenced|^in this chapter$|^contents|known gaps", re.I)
MAX_VALUE = 900
MAX_LABEL_WORDS = 4
MAX_HEADER_WORDS = 6
MIN_ITEM_LABEL_USES = 2
SENTENCE_START = re.compile(r"^(?:the|a|an|this|that|these|those|if|when|why|how|what|which|each|every|all|most|some|there|it|they|we|you|in|on|for|to|as|at|by|with|do|does|use|keep|check)\b", re.I)


@dataclass
class Unit:
    id: int
    source: str                      # the document file
    section: str                     # the section path ("Chapter 39 ... > 16. Troubleshooting Cards")
    title: str                       # the section's own title without its number
    kind: str                        # "row" (a table row), "card" (a titled group of labelled lines), "item" (one labelled line)
    entity: str                      # what the unit is about
    entity_type: str                 # the first column's header for a row ("defect", "parameter"); "card"; "item"
    attrs: list = field(default_factory=list)     # [(label, value)]
    chunk: int = None                # the passage this came from, for the citation
    table: str = ""                  # the table's heading, for a row

    def value(self, label):
        return next((v for lab, v in self.attrs if lab == label), "")


def clean_label(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w/&()\-, ']+", " ", text.lower())).strip(" :,-")


def _chunk(block):
    c = block.get("chunk")
    try:
        return int(c) if c is not None else None
    except (TypeError, ValueError):
        return None


def _section_title(section):
    return re.sub(r"^\d+\.\s*", "", section["title"]).strip()


def _row_units(section, block, start):
    header = [re.sub(r"\s+", " ", h).strip() for h in block["header"]]
    if len(header) < 2:
        return []
    out = []
    for row in block["rows"]:
        if not row or not row[0].strip():
            continue
        attrs = [(clean_label(h), c.strip()[:MAX_VALUE]) for h, c in zip(header[1:], row[1:]) if c.strip() and clean_label(h) and len(clean_label(h).split()) <= MAX_HEADER_WORDS]
        if attrs:
            out.append(Unit(start + len(out), section["source"], section["path"], _section_title(section), "row", row[0].strip(), clean_label(header[0]) or "item", attrs,
                            _chunk(block), _section_title(section)))
    return out


def _prose_units(section, blocks, start):
    """Cards and labelled lines of a section, in the order written."""
    out, card = [], None
    title = _section_title(section)

    def close():
        nonlocal card
        if card and card.attrs:
            out.append(card)
        card = None

    for b in blocks:
        text = re.sub(r"\s+", " ", b["text"]).strip()
        m = CARD.match(text)
        if m and b["t"] == "p":
            close()
            card = Unit(start + len(out), section["source"], section["path"], title, "card", m.group("title").strip(" ."), "card", [], _chunk(b), title)
            continue
        lab = LABEL.match(text)
        if lab:
            label, value = clean_label(lab.group("label")), lab.group("value").strip()[:MAX_VALUE]
            if not label or len(label.split()) > MAX_LABEL_WORDS or SENTENCE_START.match(label):
                continue                                        # a sentence with a colon in it, not a label
            if card is not None:
                card.attrs.append((label, value))
            else:
                out.append(Unit(start + len(out), section["source"], section["path"], title, "item", title, "item", [(label, value)], _chunk(b), title))
        elif card is not None and b["t"] == "p":
            close()                                                  # a paragraph that is not a labelled line ends the card
    close()
    return out


def extract(sections):
    """Every Unit of every section, numbered in reading order."""
    units = []
    for section in sections:
        if SKIP_SECTION.search(section["title"]):
            continue
        prose = []
        for b in section["blocks"]:
            if b["t"] == "table":
                units += _row_units(section, b, len(units))
            else:
                prose.append(b)
        units += _prose_units(section, prose, len(units))
    used = {}
    for u in units:
        if u.kind == "item":
            used[u.attrs[0][0]] = used.get(u.attrs[0][0], 0) + 1
    units = [u for u in units if u.kind != "item" or used[u.attrs[0][0]] >= MIN_ITEM_LABEL_USES]          # a label that only one line uses is a sentence, not a facet
    for i, u in enumerate(units):
        u.id = i
    return units
