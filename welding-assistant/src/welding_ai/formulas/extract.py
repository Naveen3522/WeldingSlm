"""Finds the formulas in the documents themselves, on every reindex, so the registry follows the documents: add a chapter with a formula and it appears; delete or
change the chapter and it goes or changes. The agents read the registry, so they get the same formulas.

Two shapes are read, each as a line of its own in a passage:
    Q = I² R t            then   where Q is heat energy in joules, I is welding current in amperes, ...
    Heat input (kJ/mm) = (Arc voltage × Welding current × 60) ÷ (Travel speed in mm/min × 1000)       (the quantities are words)
A formula is only kept when the registry's own checks pass: every symbol is defined (it has a name, from the "where" list or the words used) and used, the result is a real
number for test values, and no symbol is a function name. What fails is left out and reported, never half-loaded.

    from_documents(chunks, seed) -> (entries, report)     entries are what FormulaStore.sync_documents takes
"""
import re

from welding_ai.formulas import calc, expr, simple, validate

OPS = {"×": "*", "·": "*", "÷": "/", "−": "-", "–": "-", "^": "**", "²": "**2", "³": "**3", "√": "sqrt"}
_LINE = re.compile(r"^\s*(?P<lhs>[^=\n]{1,60}?)\s=\s(?P<rhs>[^=\n]{3,200}?)\s*$")
_WHERE = re.compile(r"\bwhere\b(?P<defs>.{10,700}?)(?:\n\s*\n|\Z)", re.I | re.S)
_DEF = re.compile(r"\b(?P<sym>[A-Za-z][A-Za-z0-9_]{0,3})\s+(?:is|are|=)\s+(?:the\s+|an?\s+)?(?P<what>[^,;.]+?)"
                  r"(?:\s+(?:in|measured in|expressed in)\s+(?P<unit>[A-Za-zµΩ°/%²³.\- ]{1,24}?))?(?=\s*(?:,|;|\.\s|\.$|\band\s+[A-Za-z][A-Za-z0-9_]{0,3}\s+(?:is|are)\b|$))")
_UNIT_NAMES = {"amperes": "A", "ampere": "A", "amps": "A", "volts": "V", "volt": "V", "ohms": "ohm", "ohm": "ohm", "seconds": "s", "second": "s", "joules": "J", "joule": "J",
               "watts": "W", "millimetres": "mm", "millimeters": "mm", "kilonewtons": "kN", "newtons": "N", "percent": "%", "hertz": "Hz", "cycles": "cycles"}
_IMPLICIT = re.compile(r"(?<=[A-Za-z0-9)])\s+(?=[A-Za-z(])")


def _slug(text, limit=34):
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return ("f_" + s if s[:1].isdigit() else s)[:limit].strip("_")


def _clean_expression(text):
    out = text
    for a, b in OPS.items():
        out = out.replace(a, b)
    out = re.sub(r"\bsqrt\s*(?=[A-Za-z0-9])", "sqrt(", out) if "sqrt" in out and "sqrt(" not in out else out
    return re.sub(r"\s+", " ", out).strip()


def _symbolic(rhs, definitions):
    """The expression for a symbol formula: single letters multiplied when written side by side (I² R t -> I**2 * R * t). None when it cannot be read safely."""
    text = _clean_expression(rhs)
    if re.search(r"[^\w\s+\-*/().,]", text):
        return None
    text = re.sub(r"\b([A-Za-z])(?=\s+[A-Za-z]\b)", r"\1 *", text)
    text = re.sub(r"(?<=\*\*\d)\s+(?=[A-Za-z(])", " * ", text)
    text = re.sub(r"(?<=[A-Za-z0-9)])\s+(?=[A-Za-z(])", " * ", text)
    return re.sub(r"\*\s+\*\s+", "** ", text).replace("* *", "**")


def _unit(raw):
    if not raw:
        return "-"
    raw = raw.strip(" .")
    return _UNIT_NAMES.get(raw.lower(), raw if len(raw) <= 12 else "-")


def _definitions(text_after):
    out = {}
    m = _WHERE.search(text_after)
    if not m:
        return out
    for d in _DEF.finditer(m.group("defs")):
        sym = d.group("sym")
        if sym.lower() in ("is", "are", "and", "the", "of", "in", "a") or sym in out:
            continue
        out[sym] = {"name": d.group("what").strip(), "unit": _unit(d.group("unit"))}
    return out


def _words_formula(lhs, rhs):
    """'Heat input (kJ/mm) = (Arc voltage × Welding current × 60) ÷ (Travel speed in mm/min × 1000)': words are the quantities. -> (result, unit, expression, variables) or None."""
    head = re.match(r"^(?P<name>[A-Za-z][A-Za-z ]{2,40}?)\s*(?:\((?P<unit>[^)]{1,14})\))?$", lhs.strip())
    if not head:
        return None
    variables, expression = {}, ""
    for token in re.finditer(r"\(|\)|[×÷*/+\-−]|\d+(?:\.\d+)?|[A-Za-z][A-Za-z ]*?(?:\s+in\s+[A-Za-z/%]+)?(?=\s*(?:[×÷*/+\-−()]|$))", rhs):
        t = token.group(0)
        if re.match(r"^[A-Za-z]", t):
            name, _, unit = t.partition(" in ")
            name = name.strip()
            if not name or len(name.split()) > 4:
                return None
            sym = _slug(name, 24)
            variables[sym] = {"name": name, "unit": _unit(unit) if unit else "-"}
            expression += sym
        else:
            expression += " " + OPS.get(t, t) + " "
    return head.group("name").strip(), _unit(head.group("unit")), re.sub(r"\s+", " ", expression).strip(), variables


def _passes(entry):
    body = validate.clean(simple.expand(entry))
    if validate.problems(body, require_example=False):
        return None
    try:
        calc.calculate(body, {v["symbol"]: 2 for v in body["variables"]})
    except Exception:
        return None
    return body


def _candidate(chunk, line, after):
    """One formula entry (the 'simple' form) for an equation line of a passage, or None."""
    m = _LINE.match(line)
    if not m or not re.search(r"[A-Za-z]", m.group("lhs")):
        return None
    lhs, rhs = m.group("lhs").strip(), m.group("rhs").strip().rstrip(".")
    section = chunk["heading"].split(" > ")[-1]
    defs = _definitions(after)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,3}", lhs):                         # Q = I² R t  (+ where ...)
        expression = _symbolic(rhs, defs)
        if not expression:
            return None
        used = [s for s in dict.fromkeys(re.findall(r"[A-Za-z_]\w*", expression)) if s != "sqrt"]
        if lhs not in defs or any(s not in defs for s in used):
            return None
        name = defs[lhs]["name"][:1].upper() + defs[lhs]["name"][1:]
        entry = {"name": name, "description": f"{lhs} = {rhs}", "equations": [{"symbol": lhs, "unit": defs[lhs]["unit"], "expression": expression}],
                 "variables": [{"symbol": s, "unit": defs[s]["unit"]} for s in used]}
        if any(s in defs for s in used):
            entry["meanings"] = {s: defs[s]["name"] for s in used}
    else:                                                                       # words as quantities
        parsed = _words_formula(lhs, rhs)
        if not parsed:
            return None
        name, unit, expression, variables = parsed
        if not variables:
            return None
        entry = {"name": name[:1].upper() + name[1:], "description": f"{lhs} = {rhs}", "equations": [{"symbol": _slug(name, 24), "unit": unit, "expression": expression}],
                 "variables": [{"symbol": s, "unit": v["unit"]} for s, v in variables.items()], "meanings": {s: v["name"] for s, v in variables.items()}}
    entry["id"] = _slug(entry["name"])
    entry["source"] = {"document": chunk["source"], "anchor": line.strip()}
    entry["group"] = "From the documents"
    return entry


def _entry_form(entry):
    """The simple form the registry stores, with each variable's plain-words name filled in."""
    meanings = entry.pop("meanings", {})
    out = dict(entry)
    out["variables"] = [dict(v, name=meanings.get(v["symbol"], v["symbol"])) for v in entry["variables"]]
    return out


def still_in_documents(source, chunks):
    """True when a hand-checked formula's quoted line is still in its document (so a deleted or rewritten chapter retires it)."""
    document, anchor = source.get("document"), re.sub(r"\s+", " ", source.get("anchor", ""))
    return bool(document and anchor and any(c["source"] == document and anchor in re.sub(r"\s+", " ", c["text"]) for c in chunks))


def from_documents(chunks, seed=()):
    """-> (entries for FormulaStore.sync_documents, report). The hand-checked seed formulas stay while their quoted line is still in the documents; the rest are read from the
    passages (everything the registry's checks accept)."""
    report = {"seed_kept": [], "seed_dropped": [], "found": [], "rejected": 0}
    entries, taken, names = [], set(), set()
    for entry in seed:
        if still_in_documents(entry.get("source", {}), chunks):
            entries.append(entry)
            taken.add(entry["id"])
            names.add(entry["name"].lower())
            report["seed_kept"].append(entry["id"])
        else:
            report["seed_dropped"].append(entry["id"])
    seen_lines = {re.sub(r"\s+", "", e.get("source", {}).get("anchor", "")) for e in entries}
    for chunk in chunks:
        text = chunk["text"].replace("\r", "")
        lines = text.split("\n")
        for n, line in enumerate(lines):
            flat = re.sub(r"\s+", "", line)
            if " = " not in line or len(line) > 220 or any(flat.startswith(x) or x.startswith(flat) for x in seen_lines if x):         # a seed's quoted line may be only the start of the line
                continue
            entry = _candidate(chunk, line, "\n".join(lines[n + 1:n + 6]))
            if entry is None:
                continue
            form = _entry_form(entry)
            if form["id"] in taken or form["name"].lower() in names or not _passes(form):
                report["rejected"] += 0 if form["id"] in taken else 1
                continue
            taken.add(form["id"])
            names.add(form["name"].lower())
            seen_lines.add(re.sub(r"\s+", "", line))
            entries.append(form)
            report["found"].append(form["id"])
    return entries, report
