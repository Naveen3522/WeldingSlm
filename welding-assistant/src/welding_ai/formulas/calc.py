"""Run a formula: inputs in, outputs out, with the worked steps, anything assumed, and the formula's own warnings.

The arithmetic is done here in Python, never by the language model.
"""
import math
from decimal import ROUND_HALF_UP, Decimal

from welding_ai.formulas import expr


class CalcError(ValueError):
    """`fields` maps an input symbol to what is wrong with it; `missing` lists required inputs that were not given."""

    def __init__(self, message, fields=None, missing=None):
        super().__init__(message)
        self.message, self.fields, self.missing = message, fields or {}, missing or []


def _number(raw):
    if isinstance(raw, bool):
        raise ValueError("not a number")
    if isinstance(raw, str):
        text = raw.strip().replace(" ", "")
        if text.count(",") == 1 and "." not in text:
            text = text.replace(",", ".")
        raw = float(text)
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("not finite")
    return value


# A value as shown: rounded half up to the output's decimals (6.25 -> 6.3, as a reader and the documents do; Python's round() gives 6.2),
# "-0.00" tidied to "0.00".
def fmt(value, decimals):
    exact = Decimal(repr(round(value, 12)))                       # the shortest decimal the float stands for, so 6.25 stays 6.25
    text = f"{exact.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP):.{decimals}f}"
    return text[1:] if text.startswith("-") and float(text) == 0 else text


# Six significant digits for the worked steps; whole numbers stay whole ("29", not "29.0").
def _short(value):
    short = float(f"{value:.6g}")
    return int(short) if short.is_integer() and abs(short) < 1e15 else short


def calculate(body, values):
    """-> {"inputs", "outputs", "skipped", "assumed", "warnings"}; raises CalcError for missing or invalid inputs."""
    variables = {v["symbol"]: v for v in body["variables"]}
    unknown = [k for k in values if k not in variables]
    if unknown:
        raise CalcError("Not an input of this formula: " + ", ".join(unknown), fields={k: "Not an input of this formula." for k in unknown})

    env, inputs, assumed, errors, missing = {}, [], [], {}, []
    for symbol, var in variables.items():
        raw = values.get(symbol)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if var.get("required", True):
                missing.append(symbol)
            elif var.get("default") is not None:
                env[symbol] = float(var["default"])
                assumed.append(f"{var['name']} was not given, so {env[symbol]:g} was used.")
                inputs.append({"symbol": symbol, "name": var["name"], "value": env[symbol], "unit": var["unit"], "assumed": True})
            continue
        try:
            number = _number(raw)
        except ValueError:
            errors[symbol] = "Enter a number."
            continue
        if var.get("min") is not None and number < var["min"]:
            errors[symbol] = f"Must be at least {var['min']:g}."
        elif var.get("max") is not None and number > var["max"]:
            errors[symbol] = f"Must be at most {var['max']:g}."
        else:
            env[symbol] = number
            inputs.append({"symbol": symbol, "name": var["name"], "value": number, "unit": var["unit"], "assumed": False})
    if errors or missing:
        problems = {**errors, **{m: "Required." for m in missing}}
        names = ", ".join(variables[s]["name"] for s in list(problems))
        raise CalcError(("Check these inputs: " if errors else "Still needed: ") + names, fields=problems, missing=missing)

    outputs, skipped = [], []
    for out in body["outputs"]:
        tree = expr.parse(out["expr"])
        lacking = sorted(expr.names(tree) - env.keys())
        if lacking:
            labels = ", ".join(variables[s]["name"] if s in variables else s for s in lacking)
            skipped.append({"symbol": out["symbol"], "name": out["name"], "needs": lacking, "message": f"{out['name']} needs: {labels}. Give it to get this value."})
            continue
        try:
            value = expr.evaluate(tree, env)
        except expr.ExprError as e:
            raise CalcError(f"{out['name']}: {e}")
        shown = {k: _short(v) for k, v in env.items()}
        env[out["symbol"]] = value
        outputs.append({"symbol": out["symbol"], "name": out["name"], "unit": out["unit"], "value": value,
                        "text": fmt(value, out.get("decimals", 2)), "steps": f"{out['symbol']} = {expr.substituted(tree, shown)}"})

    warnings = []
    for check in body.get("checks") or []:
        tree = expr.parse(check["when"])
        if expr.names(tree) <= env.keys() and expr.evaluate(tree, env):
            warnings.append(check["message"])
    return {"inputs": inputs, "outputs": outputs, "skipped": skipped, "assumed": assumed, "warnings": warnings}
