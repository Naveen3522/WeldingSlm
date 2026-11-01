"""The formula agent: question in, formula and numbers out.

    1. find the formula     words in the question against each formula's names, aliases and variables (same stemming as the search index)
    2. find the numbers     "V = 28", "28 V", "voltage 28": only when the match is unambiguous
    3. calculate            Python does the arithmetic (calc.py); anything missing is asked for, never guessed
It answers "not covered" when no formula matches, and asks "which one?" when two match equally (the two carbon equivalents, for example).
"""
import re

from welding_ai.formulas.calc import CalcError, calculate
from welding_ai.retrieval import rag

NUMBER = r"[-+]?\d+(?:[.,]\d+)?"
CONFIDENT, CLEAR_MARGIN, POSSIBLE = 0.55, 0.15, 0.35      # score to answer, lead over the runner-up to answer, score to be offered as a choice
NAME_MIN = 0.34                                           # share of a name or alias that must appear in the question for the formula to be considered
GENERIC = {"weld", "welding", "total", "thinnest", "sheet", "stack", "metal", "effective", "joint", "melted", "arc", "factor", "area"}


def _tokens(text):
    return set(rag.tokens(text))


def _number(text):
    return float(text.replace(",", "."))


# Words that are units ("mm", "kJ", "wt"): they appear in every other question and say nothing about which formula is meant.
def _unit_terms(formulas):
    return {t for f in formulas for v in f["variables"] + f["outputs"] for t in _tokens(v["unit"])}


# How well the question matches a formula's identity: its name, aliases, variable names and symbols.
# The descriptive text (summary, purpose) is deliberately left out: it mentions neighbouring topics ("heat input ... how fast the joint cools"),
# and a question about cooling time must not borrow a formula because of them.
def _score(formula, question_terms, units):
    if not question_terms:
        return 0.0
    names = [formula["name"]] + formula["aliases"]
    best_name = max((len(_tokens(n) - units & question_terms) / len(_tokens(n) - units) for n in names if _tokens(n) - units), default=0.0)
    if best_name < NAME_MIN:
        return 0.0                                          # variable names can support a match but never make one ("time" is in several formulas)
    vocabulary = _tokens(" ".join(names + [v["name"] for v in formula["variables"]] + [v["symbol"] for v in formula["variables"]])) - units
    coverage = len(question_terms & vocabulary) / len(question_terms)
    return round(0.55 * best_name + 0.45 * coverage, 3)


def rank(formulas, question):
    """-> [(score, formula)] best first. Numbers and unit words in the question are ignored: they say nothing about which formula is meant."""
    units = _unit_terms(formulas)
    terms = {t for t in _tokens(question) if not any(c.isdigit() for c in t)} - units
    return sorted(((_score(f, terms, units), f) for f in formulas), key=lambda x: -x[0])


def extract(formula, text):
    """-> {"values": {symbol: float}, "matches": [{symbol, value, text}], "conflicts": [symbol]} for the numbers the text gives unambiguously.

    Four readings of the text; a variable is filled only if every reading that found a number for it agrees. Two different numbers for one
    variable leave it out (listed as a conflict) so the person is asked, rather than a guess being calculated:
        1  "V = 28", "V: 28"             the symbol, case-sensitive, then = or :
        2  "28 V", "350 mm/min"          a number and a unit that only one variable has
        3  "voltage 28", "thickness of 1.4"   a word from the variable's name, then the number
        4  "Mn 1.2"                      the bare symbol then the number (skipped after a number when the symbol is also a unit here: "28 V 260 A")
    """
    variables = formula["variables"]
    tiers = {1: {}, 2: {}, 3: {}, 4: {}}                                 # tier -> symbol -> [(value, matched text)]

    def put(tier, symbol, raw, matched):
        tiers[tier].setdefault(symbol, []).append((_number(raw), matched.strip()))

    units = {}
    for v in variables:
        key = v["unit"].replace(" ", "").lower()
        if key not in ("", "-"):
            units.setdefault(key, []).append(v["symbol"])

    for v in variables:
        sym = rf"(?<![A-Za-z0-9_]){re.escape(v['symbol'])}(?![A-Za-z0-9_])"
        for m in re.finditer(rf"{sym}\s*(?:=|:|\bis\b)\s*({NUMBER})", text):
            put(1, v["symbol"], m.group(1), m.group(0))
        for m in re.finditer(rf"{sym}\s*({NUMBER})", text):
            after_a_number = re.search(r"\d\s*$", text[:m.start()])
            if not (after_a_number and v["symbol"].lower() in units):
                put(4, v["symbol"], m.group(1), m.group(0))
    lowered = text.lower()
    for key, symbols in units.items():
        if len(symbols) == 1:
            for m in re.finditer(rf"({NUMBER})\s*{re.escape(key)}(?![A-Za-z0-9/])(?!\s*[=:])", lowered):      # "260 v=350": that v is a symbol, not a unit
                put(2, symbols[0], m.group(1), m.group(0))

    for v in variables:
        for word in {w for w in re.findall(r"[a-z]{4,}", v["name"].lower()) if w not in GENERIC}:
            for m in re.finditer(rf"\b{word}\b\s*(?:=|:|of|is|at)?\s*({NUMBER})", lowered):
                put(3, v["symbol"], m.group(1), m.group(0))

    matches, conflicts = [], []
    for v in variables:
        symbol = v["symbol"]
        seen = [(tier, value, shown) for tier in (1, 2, 3, 4) for value, shown in tiers[tier].get(symbol, [])]
        if not seen:
            continue
        if len({value for _, value, _ in seen}) > 1:
            conflicts.append(symbol)
        else:
            matches.append({"symbol": symbol, "value": seen[0][1], "text": seen[0][2]})
    return {"values": {m["symbol"]: m["value"] for m in matches}, "matches": matches, "conflicts": conflicts}


def _candidates(ranked):
    return [{"id": f["id"], "name": f["name"], "score": s} for s, f in ranked[:4] if s >= POSSIBLE]


def ask(formulas, question, formula_id=None, values=None):
    """-> {"state": "answered" | "need_values" | "invalid" | "choose" | "not_covered", "candidates", "formula_id", "extracted", "conflicts", "missing", "result", "message"}."""
    by_id = {f["id"]: f for f in formulas}
    ranked = rank(formulas, question)
    reply = {"state": "not_covered", "candidates": _candidates(ranked), "formula_id": None, "extracted": [], "conflicts": [], "missing": [], "result": None, "message": ""}

    named = [f for f in formulas if f["name"].lower() in question.lower()]       # the full name typed out (a choice made from the "which one?" buttons)
    if formula_id in by_id:
        chosen = by_id[formula_id]
    elif named:
        chosen = max(named, key=lambda f: len(f["name"]))
    elif not ranked or ranked[0][0] < POSSIBLE:
        reply["message"] = "None of the formulas in the documents (or added by an administrator) matches this question."
        return reply
    elif ranked[0][0] >= CONFIDENT and (len(ranked) < 2 or ranked[0][0] - ranked[1][0] >= CLEAR_MARGIN):
        chosen = ranked[0][1]
    else:
        reply["state"] = "choose"
        reply["message"] = "More than one formula fits. Which do you mean?"
        return reply

    found = extract(chosen, question)
    merged = {**found["values"], **{k: v for k, v in (values or {}).items() if v not in (None, "")}}
    reply.update(formula_id=chosen["id"], extracted=found["matches"], conflicts=found["conflicts"])
    try:
        reply["result"] = calculate(chosen, merged)
        reply["state"] = "answered"
    except CalcError as e:
        reply["state"] = "need_values" if e.missing and not (set(e.fields) - set(e.missing)) else "invalid"
        reply["missing"] = e.missing
        reply["message"] = e.message
    return reply
