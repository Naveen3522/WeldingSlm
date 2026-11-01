"""The rules a formula must meet before it is saved, and the tidying that makes a submitted formula uniform.

The main rule: every variable is defined and explained, every variable is used, and the formula reproduces its own worked example.
Nothing is saved that fails a rule; the problems come back as plain sentences for the person who typed it.
"""
import re

from welding_ai.formulas import expr
from welding_ai.formulas.calc import CalcError, calculate

SYMBOL = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,24}$")
TOLERANCE = 0.02                 # an example matches if the result is within 2% (documents round their worked examples)
MIN_MEANING, MIN_PURPOSE = 10, 20


def _text(x):
    return "" if x is None else str(x).strip()


def _number(x):
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return x                  # left as it is: reported as a problem


def _numbers(d):
    return {_text(k): _number(v) for k, v in d.items()} if isinstance(d, dict) else {}


# A submitted formula reduced to the known fields, with text trimmed and numbers converted.
def clean(body):
    body = body if isinstance(body, dict) else {}
    source = body.get("source") if isinstance(body.get("source"), dict) else {}
    out = {
        "name": _text(body.get("name")),
        "aliases": [_text(a) for a in (body.get("aliases") or []) if _text(a)][:12],
        "group": _text(body.get("group")),
        "summary": _text(body.get("summary")),
        "purpose": _text(body.get("purpose")),
        "display": _text(body.get("display")),
        "variables": [], "outputs": [], "checks": [], "examples": [],
        "assumptions": [_text(a) for a in (body.get("assumptions") or []) if _text(a)],
        "related": [_text(r) for r in (body.get("related") or []) if _text(r)],
        "source": {k: _text(source.get(k)) for k in ("document", "section", "reference", "anchor")},
    }
    for v in body.get("variables") or []:
        if isinstance(v, dict):
            out["variables"].append({
                "symbol": _text(v.get("symbol")), "name": _text(v.get("name")), "meaning": _text(v.get("meaning")), "unit": _text(v.get("unit")),
                "typical": _text(v.get("typical")), "where_to_get": _text(v.get("where_to_get")), "required": bool(v.get("required", True)),
                "min": _number(v.get("min")), "max": _number(v.get("max")), "default": _number(v.get("default"))})
    for o in body.get("outputs") or []:
        if isinstance(o, dict):
            decimals = o.get("decimals", 2)
            out["outputs"].append({"symbol": _text(o.get("symbol")), "name": _text(o.get("name")), "unit": _text(o.get("unit")),
                                   "expr": _text(o.get("expr")), "decimals": decimals if isinstance(decimals, int) and not isinstance(decimals, bool) else 2})
    for c in body.get("checks") or []:
        if isinstance(c, dict):
            out["checks"].append({"when": _text(c.get("when")), "message": _text(c.get("message"))})
    for e in body.get("examples") or []:
        if isinstance(e, dict):
            ex = {"title": _text(e.get("title")), "inputs": _numbers(e.get("inputs")), "expected": _numbers(e.get("expected")),
                  "from_document": bool(e.get("from_document", False)), "note": _text(e.get("note"))}
            if e.get("tol") not in (None, ""):
                ex["tol"] = _number(e.get("tol"))
            out["examples"].append(ex)
    if not out["display"] and out["outputs"]:
        out["display"] = "; ".join(f"{o['symbol']} = {o['expr']}" for o in out["outputs"])
    return out


# Every reason this formula cannot be saved, as sentences (empty list: it can).
def problems(body, require_example=True):
    p = []
    if not 3 <= len(body["name"]) <= 120:
        p.append("Name: give the formula a name (3 to 120 characters).")
    if len(body["purpose"]) < MIN_PURPOSE:
        p.append(f"Why it is needed: explain what the formula is for (at least {MIN_PURPOSE} characters).")
    if not body["source"]["reference"]:
        p.append("Source: say where the formula comes from (a document and section, or a standard and clause).")
    if not body["assumptions"]:
        p.append("Limits: state at least one assumption or limit of the formula.")

    symbols, seen = [], set()
    if not body["variables"]:
        p.append("Variables: a formula needs at least one input variable.")
    for v in body["variables"]:
        s = v["symbol"]
        label = f"Variable '{s or '?'}'"
        if not SYMBOL.match(s):
            p.append(f"{label}: the symbol must start with a letter and use only letters, digits and underscores.")
        elif s in expr.FUNCTIONS:
            p.append(f"{label}: '{s}' is the name of a function; choose another symbol.")
        elif s in seen:
            p.append(f"{label}: the symbol is used twice.")
        seen.add(s)
        symbols.append(s)
        if not v["name"]:
            p.append(f"{label}: give it a name.")
        if len(v["meaning"]) < MIN_MEANING:
            p.append(f"{label}: explain what it means (at least {MIN_MEANING} characters).")
        if not v["unit"]:
            p.append(f"{label}: give its unit (write - if it has none).")
        for key in ("min", "max", "default"):
            if v[key] is not None and not isinstance(v[key], float):
                p.append(f"{label}: {key} must be a number.")
        if isinstance(v["min"], float) and isinstance(v["max"], float) and v["min"] > v["max"]:
            p.append(f"{label}: min is larger than max.")

    defined, used = set(symbols), set()
    if not body["outputs"]:
        p.append("Outputs: a formula needs at least one output.")
    for o in body["outputs"]:
        s = o["symbol"]
        label = f"Output '{s or '?'}'"
        if not SYMBOL.match(s):
            p.append(f"{label}: the symbol must start with a letter and use only letters, digits and underscores.")
        elif s in defined:
            p.append(f"{label}: the symbol is already used by a variable or another output.")
        if not o["name"] or not o["unit"]:
            p.append(f"{label}: give it a name and a unit (write - if it has none).")
        if not 0 <= o["decimals"] <= 8:
            p.append(f"{label}: decimals must be between 0 and 8.")
        try:
            tree = expr.parse(o["expr"])
        except expr.ExprError as e:
            p.append(f"{label}: {e}")
        else:
            for n in sorted(expr.names(tree) - defined):
                p.append(f"{label}: the expression uses '{n}', which is not a variable or an earlier output.")
            used |= expr.names(tree)
        defined.add(s)

    for c in body["checks"]:
        try:
            tree = expr.parse(c["when"])
        except expr.ExprError as e:
            p.append(f"Warning rule '{c['when'] or '?'}': {e}")
            continue
        if not c["message"]:
            p.append(f"Warning rule '{c['when']}': write the message to show.")
        for n in sorted(expr.names(tree) - defined):
            p.append(f"Warning rule '{c['when']}': it uses '{n}', which is not a variable or an output.")
        used |= expr.names(tree)

    for s in symbols:
        if s and s not in used:
            p.append(f"Variable '{s}' is defined but never used in any expression.")

    if p:
        return p                                                     # the worked examples need a formula that is sound
    if require_example and not body["examples"]:
        p.append("Worked example: give at least one example with its expected result; the formula is tested against it before it is saved.")
    output_symbols = {o["symbol"] for o in body["outputs"]}
    for e in body["examples"]:
        label = f"Example '{e['title'] or '?'}'"
        if not e["expected"]:
            p.append(f"{label}: state the expected result.")
            continue
        if not set(e["expected"]) <= output_symbols:
            p.append(f"{label}: expected results must be named after outputs ({', '.join(sorted(output_symbols))}).")
            continue
        if any(not isinstance(x, float) for x in list(e["inputs"].values()) + list(e["expected"].values())):
            p.append(f"{label}: inputs and expected results must be numbers.")
            continue
        try:
            result = calculate(body, e["inputs"])
        except CalcError as err:
            p.append(f"{label}: it could not be calculated ({err.message}).")
            continue
        tol = e.get("tol") if isinstance(e.get("tol"), float) else TOLERANCE
        got = {o["symbol"]: o["value"] for o in result["outputs"]}
        for symbol, want in e["expected"].items():
            if symbol not in got:
                p.append(f"{label}: {symbol} was not calculated from these inputs.")
            elif abs(got[symbol] - want) > max(tol * abs(want), 1e-9):
                p.append(f"{label}: the formula gives {symbol} = {got[symbol]:.6g} but the example says {want:g}.")
    return p
