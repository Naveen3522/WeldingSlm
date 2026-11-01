"""The simple formula form: a name, one or more equations (each with a result symbol and unit), and a symbol and unit for each variable.

    {"name", "description"?, "equations": [{"label"?, "symbol", "unit", "expression"}], "variables": [{"symbol", "unit"}], "source"?: {"document", "anchor"}}

A single equation may also be given as "expression", "symbol" and "unit" at the top level. A later equation may use the result of an earlier one.

expand() turns that into the full formula the rest of the system uses, filling the descriptive fields with plain defaults, so the same rules still apply:
every symbol in the expression is a variable, and every variable is used. to_simple() goes the other way for editing.
"""
import re

MANUAL = "Added manually"                  # the badge on a formula nobody traced to a document


def is_simple(body):
    return isinstance(body, dict) and ("expression" in body or "equations" in body)


def _text(x):
    return "" if x is None else str(x).strip()


def _equations(body):
    """The equations of a simple form as [{label, symbol, unit, expression}], whichever way they were given."""
    given = body.get("equations")
    if not isinstance(given, list):
        given = [body]
    return [{"label": _text(e.get("label")), "symbol": _text(e.get("symbol")), "unit": _text(e.get("unit")) or "-", "expression": _text(e.get("expression"))}
            for e in given if isinstance(e, dict)]


def expand(body):
    """The full formula for a simple form (anything else is returned as it is)."""
    if not is_simple(body):
        return body
    name = _text(body.get("name"))
    equations = _equations(body)
    variables = [{"symbol": _text(v.get("symbol")), "unit": _text(v.get("unit")) or "-", "name": _text(v.get("name")) or _text(v.get("symbol"))} for v in (body.get("variables") or []) if isinstance(v, dict)]
    description = _text(body.get("description"))[:200]
    given = body.get("source") if isinstance(body.get("source"), dict) else {}
    document, anchor = _text(given.get("document")), _text(given.get("anchor"))
    return {
        "name": name, "group": _text(body.get("group")) or "Added formulas",
        "summary": description or f"Calculates {name}.", "purpose": f"Calculates {name} from the values entered.",
        "variables": [{"symbol": v["symbol"], "name": v["name"], "meaning": f"Value of {v['name']} in {v['unit']}" if v["unit"] != "-" else f"Value of {v['name']}", "unit": v["unit"], "required": True} for v in variables],
        "outputs": [{"symbol": e["symbol"], "name": e["label"] or (name if len(equations) == 1 else e["symbol"]), "unit": e["unit"], "expr": e["expression"], "decimals": 2} for e in equations],
        "assumptions": ["Entered by hand: check it against the source before relying on it."],
        "source": {"reference": document or MANUAL, "document": document, "section": "", "anchor": anchor if document else ""},
    }


def make_id(body, taken=()):
    """An id from the name: lowercase letters, digits and underscores, made unique with a number."""
    base = re.sub(r"[^a-z0-9]+", "_", _text(body.get("name")).lower()).strip("_")
    base = ("f_" + base if base[:1].isdigit() else base)[:36] or "formula"
    if len(base) < 3:
        base += "_calc"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}_{n}", n + 1
    return candidate


def to_simple(formula):
    """The simple form of a stored formula, or None if it cannot be shown in it (an output without a plain expression)."""
    if not formula["outputs"] or any(not o.get("expr") for o in formula["outputs"]):
        return None
    o = formula["outputs"][0]
    plain = f"Calculates {formula['name']}."
    return {"name": formula["name"], "description": "" if formula["summary"] == plain else formula["summary"], "expression": o["expr"], "symbol": o["symbol"], "unit": o["unit"],
            "equations": [{"label": "" if p["name"] in (formula["name"], p["symbol"]) else p["name"], "symbol": p["symbol"], "unit": p["unit"], "expression": p["expr"]} for p in formula["outputs"]],
            "variables": [{"symbol": v["symbol"], "unit": v["unit"]} for v in formula["variables"]],
            "source": {"document": formula["source"].get("document", ""), "anchor": formula["source"].get("anchor", "")}}
