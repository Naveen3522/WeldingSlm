"""Symptom -> mechanism, discriminators and corrective action, read straight from the defect index (document 74).

It quotes the table rows. When a symptom fits several rows (just "cracks"), it also quotes the document's own discriminator rows and asks the
question that tells them apart, instead of guessing which one the person has.
"""
import re
from dataclasses import dataclass, field

DOC = "74_"
RESISTANCE = "10. Resistance Welding Imperfections"
GROUPS = ("4. Group 1", "5. Group 2", "6. Group 3", "7. Group 4", "Group 5", "9. Group 6")
DISCRIMINATORS = "14. The Discriminator Tables"
SPATTER_ORDER = "Spatter — the cross-process treatment"
GENERIC_LIMIT = 4          # more rows than this: name them and ask which one, rather than quote them all

# canonical symptom -> (words in the imperfection title, words in the mechanism, confusable-pair words)
KEYS = {
    "porosity": (["porosity", "blowhole"], [], ["porosity"]),
    "cracking": (["crack", "tearing"], [], ["crack"]),
    "hydrogen cracking": ([], ["hydrogen"], ["hydrogen"]),
    "solidification cracking": (["centreline"], ["solidification"], ["solidification"]),
    "lack of fusion": (["lack of"], [], ["fusion"]),
    "lack of penetration": (["incomplete penetration"], [], ["penetration"]),
    "undercut": (["undercut"], [], ["undercut"]),
    "slag inclusion": (["slag"], [], ["slag"]),
    "spatter": (["spatter"], [], ["spatter"]),
    "burn-through": (["burn-through"], [], []),
    "expulsion": (["expulsion"], [], ["expulsion"]),
    "undersize nugget": (["undersize nugget"], [], ["nugget"]),
    "no nugget / stick weld": (["no nugget"], [], ["nugget"]),
    "excessive indentation": (["indentation"], [], []),
    "interfacial fracture": (["interfacial"], [], ["interfacial"]),
    "electrode wear / mushrooming": (["electrode wear"], [], []),
    "shunting": (["shunting"], [], []),
    "electrode misalignment": (["misalignment"], [], []),
}
RESISTANCE_ONLY = {"expulsion", "undersize nugget", "no nugget / stick weld", "excessive indentation", "interfacial fracture", "electrode wear / mushrooming", "shunting", "electrode misalignment"}
OTHER_PROCESS = {"fsw": r"fsw|wormhole", "laser": r"beam|keyhole", "rsw": r"resistance"}     # confusable pairs that belong to another process


@dataclass
class Finding:
    rows: list = field(default_factory=list)            # [{"imperfection", "mechanism", "discriminators", "action", "chapter", "chunk", "heading"}]
    pairs: list = field(default_factory=list)           # [{"pair", "discriminator", "chunk"}] the document's rules for telling similar defects apart
    ask: str = ""                                       # a question for the person, when the symptom fits several rows
    spatter_order: list = field(default_factory=list)   # [(variable, what it does, why this order)] for arc spatter
    chunks: list = field(default_factory=list)          # chunk ids the answer rests on

    def __bool__(self):
        return bool(self.rows)


def _cell(table, row, *starts):
    for column, value in zip(table["header"], row):
        if column.lower().startswith(starts):
            return value
    return ""


def _rows(knowledge, heading, symptom):
    table = knowledge.table(DOC, heading)
    if table is None:
        return []
    title_words, mech_words, _pair = KEYS[symptom]
    out = []
    for row in table["rows"]:
        title, mechanism = _cell(table, row, "imperfection").lower(), _cell(table, row, "mechanism").lower()
        if any(w in title for w in title_words) or any(w in mechanism for w in mech_words):
            out.append({"imperfection": _cell(table, row, "imperfection"), "mechanism": _cell(table, row, "mechanism"), "discriminators": _cell(table, row, "discriminators"),
                        "action": _cell(table, row, "corrective"), "chapter": _cell(table, row, "chapter"), "chunk": table["chunk_ids"][0], "heading": table["heading"]})
    return out


def lookup(symptom, process, knowledge, materials=()):
    """-> Finding for the symptom (no rows when the defect tables do not list it). A row about one metal ("... in aluminium") is kept only if that metal is named."""
    f = Finding()
    if symptom not in KEYS:
        return f
    resistance = symptom in RESISTANCE_ONLY or process == "rsw"
    if resistance:
        f.rows = _rows(knowledge, RESISTANCE, symptom)
    else:
        for heading in GROUPS:
            f.rows += _rows(knowledge, heading, symptom)
    f.rows = [r for r in f.rows if "aluminium" not in r["imperfection"].lower() or "aluminium" in materials]
    if not f.rows:
        return f

    _t, _m, pair_words = KEYS[symptom]
    table = knowledge.table(DOC, DISCRIMINATORS)
    if table and pair_words:
        skip = [rx for key, rx in OTHER_PROCESS.items() if key != process and not (key == "rsw" and resistance)]
        for row in table["rows"]:
            pair = row[0].lower()
            if any(w in pair for w in pair_words) and not any(re.search(rx, pair, re.I) for rx in skip):
                f.pairs.append({"pair": row[0], "discriminator": row[1], "chunk": table["chunk_ids"][0]})
    if symptom == "spatter" and not resistance:
        order = knowledge.table(DOC, SPATTER_ORDER)
        if order:
            f.spatter_order = [(r[1], r[2], r[3]) for r in order["rows"]]
            f.chunks.append(order["chunk_ids"][0])

    if len(f.rows) > GENERIC_LIMIT:
        names = [r["imperfection"] for r in f.rows]
        f.ask = ("Which of these matches what you see: " + "; ".join(names) + "? "
                 + ("The documents separate them by timing and where they are." if symptom == "cracking" else "That decides which corrective action applies."))
    f.chunks = list(dict.fromkeys([r["chunk"] for r in f.rows] + [p["chunk"] for p in f.pairs] + f.chunks))
    return f
