"""Resistance spot welding starting values from the Material and Thickness Parameter Guide (document 20) and its formulas.

Geometry (electrode face, nugget, spacing, edge distance) comes from the registry formulas in millimetres. Force, time and current come from
the document's *imperial* first-trial rule, so the stack is converted to inches first (the unit the earlier AI summary dropped) and the 20%
reduction above 0.080 in is applied exactly as the document's own table does. Where the document gives no number (HSLA, AHSS, zinc-coated, and
aluminium force and geometry) the answer says so and quotes the document's caveat instead of inventing one.
"""
import re
from dataclasses import dataclass, field

from welding_ai.agents.slots import INCH
from welding_ai.formulas.calc import CalcError, calculate

DOC = "20_"
SUM_T_DECIMALS = 4                  # the document's tables round the stack to 0.0001 in (2.0 mm -> 0.0787 in)
REDUCE_ABOVE_IN, REDUCTION = 0.080, 0.80
ALUMINIUM_CURRENT, ALUMINIUM_TIME = 3.0, 1 / 3
SWEEP_MM = (0.4, 3.0)               # ISO 14373's range in the documents (0.4 to 3 mm); each sheet must lie inside it
LBF_TO_N = 4.4482216
NEEDS = {                           # which formulas each asked-for quantity needs
    "electrode": ("electrode_face_diameter",), "nugget": ("min_nugget_diameter",), "spacing": ("min_weld_spacing",),
    "edge": ("min_nugget_diameter", "min_edge_distance"), "force": ("first_trial_estimate",), "time": ("first_trial_estimate",), "current": ("first_trial_estimate",),
}
ALL = ("electrode", "nugget", "spacing", "edge", "force", "time", "current")
WORDS = {
    "force": r"\bforce\b|squeeze\s+(?:force|pressure)", "time": r"weld(?:ing)?\s+time|\bcycles?\b|duration", "current": r"\bcurrent\b|\bamps?\b|amperage",
    "electrode": r"electrode\s+(?:face\s+)?(?:diameter|size)|(?:face|tip)\s+diameter|tip\s+size", "nugget": r"nugget",
    "spacing": r"spacing|\bpitch\b", "edge": r"edge\s+distance",
}
RSW_CONTEXT = re.compile(r"spot|resistance|electrode|nugget|cycles?|squeeze|\bRSW\b", re.I)
GENERIC = re.compile(r"parameters?|settings?|schedule|starting\s+(?:values?|point)|first[\s-]*trial", re.I)


def asked(text):
    """-> the quantities the question asks for (all of them for "parameters" or "settings")."""
    found = [q for q, rx in WORDS.items() if re.search(rx, text, re.I)]
    return found or (list(ALL) if GENERIC.search(text) else [])


def in_context(text, process):
    return process == "rsw" or bool(RSW_CONTEXT.search(text))


@dataclass
class Result:
    refused: str = ""                                    # why nothing was calculated (a plain sentence), or ""
    quantities: list = field(default_factory=list)       # [{"name", "value", "unit", "steps", "formula"}]
    notes: list = field(default_factory=list)            # things the reader must know, each a plain sentence
    caveats: list = field(default_factory=list)          # [(label, document text)] quoted from the guide
    gaps: list = field(default_factory=list)             # what the documents do not give
    stack_in: float = 0.0
    family: str = ""
    used: list = field(default_factory=list)             # the formula ids used
    chunks: list = field(default_factory=list)           # the chunks of document 20 the values and caveats rest on


def _calc(formulas, formula_id, values):
    f = formulas.get(formula_id)
    if f is None:
        raise KeyError(formula_id)
    return calculate(f, values)


def _out(result, symbol):
    return next(o for o in result["outputs"] if o["symbol"] == symbol)


def _quantity(name, o, unit=None, value=None, text=None, extra=""):
    return {"name": name, "value": o["value"] if value is None else value, "text": text or o["text"], "unit": unit or o["unit"], "steps": o["steps"], "extra": extra}


def family_of(scenario):
    """-> (family key, the families named). Mild steel is the baseline when nothing is named."""
    named = list(scenario.materials)
    steel = [m for m in named if m in ("mild", "hsla", "ahss")]
    if "aluminium" in named and steel:
        return "mixed", named
    if len(steel) > 1:
        return "mixed", named
    if named and not steel and "aluminium" not in named:
        return named[0], named            # stainless, titanium ...: not covered by this guide
    if "aluminium" in named:
        return "aluminium", named
    if "ahss" in named:
        return "ahss", named
    if "hsla" in named:
        return "hsla", named
    return ("zinc" if scenario.coating else "mild"), named


def compute(scenario, wanted, formulas, knowledge):
    """Starting values for the situation. `wanted`: the quantities to give; `formulas`: {id: formula body} from the registry."""
    r = Result()
    sheets = [s.thickness_mm for s in scenario.sheets]
    if not sheets:
        r.refused = "I need the sheet thickness (for example 1.0 mm, or 1.0 mm and 1.5 mm for two different sheets)."
        return r
    low, high = SWEEP_MM
    if any(not low <= t <= high for t in sheets):
        r.refused = (f"The guide covers sheets from {low:g} to {high:g} mm (ISO 14373's range). "
                     f"A sheet of {max(sheets, key=lambda t: abs(t - (low + high) / 2)):.2f} mm is outside it, so no values are given.")
        return r

    r.family, named = family_of(scenario)
    if r.family not in ("mild", "hsla", "ahss", "zinc", "aluminium", "mixed"):
        r.refused = (f"The guide gives starting values for mild / low-carbon steel (with notes for HSLA, AHSS and zinc-coated steel) and aluminium. "
                     f"It gives none for {', '.join(named)}.")
        return r

    if len(sheets) == 1:                                    # a weld joins at least two sheets; the guide's tables assume a 2-sheet equal stack
        sheets = sheets * 2
        r.notes.append(f"One thickness was given, so the stack is read as two equal sheets of {sheets[0]:g} mm (the stack the guide's table assumes). "
                       "If the sheets differ, give both thicknesses.")
    thinnest, stack_mm = min(sheets), sum(sheets)
    r.stack_in = round(stack_mm / INCH, SUM_T_DECIMALS)
    aluminium = r.family == "aluminium"

    geometry = [q for q in wanted if q in ("electrode", "nugget", "spacing", "edge")]
    if geometry and aluminium:
        r.gaps.append("Electrode, nugget, spacing and edge-distance rules: the guide's coefficients are sourced for low-carbon steel, and it carries no independently sourced coefficient for aluminium.")
    elif geometry:
        try:
            if "electrode" in geometry:
                o = _out(_calc(formulas, "electrode_face_diameter", {"t": thinnest}), "d_e")
                r.quantities.append(_quantity("Electrode face diameter", o))
                r.used.append("electrode_face_diameter")
            if "nugget" in geometry or "edge" in geometry:
                res = _calc(formulas, "min_nugget_diameter", {"t": thinnest})
                lo, hi = _out(res, "d_w_min"), _out(res, "d_w_max")
                if "nugget" in geometry:
                    r.quantities.append({"name": "Minimum nugget diameter", "value": lo["value"], "text": f"{lo['text']} to {hi['text']}", "unit": "mm",
                                         "steps": f"{lo['steps']}; {hi['steps']}", "extra": ""})
                    r.used.append("min_nugget_diameter")
                if "edge" in geometry:
                    for label, d in (("from the smaller nugget", lo), ("from the larger nugget", hi)):
                        e = _out(_calc(formulas, "min_edge_distance", {"d_w": d["value"]}), "e_min")
                        r.quantities.append(_quantity(f"Minimum edge distance ({label})", e))
                    r.used.append("min_edge_distance")
            if "spacing" in geometry:
                r.quantities.append(_quantity("Minimum spacing between welds", _out(_calc(formulas, "min_weld_spacing", {"t": thinnest}), "s_min")))
                r.used.append("min_weld_spacing")
        except (KeyError, CalcError) as e:
            r.refused = "A formula this answer needs is not available (" + (e.message if isinstance(e, CalcError) else str(e)) + ")."
            return r
        if len(sheets) > 1:
            r.notes.append(f"The geometry rules use the thinnest sheet ({thinnest:g} mm).")

    estimate = [q for q in wanted if q in ("force", "time", "current")]
    if estimate:
        try:
            res = _calc(formulas, "first_trial_estimate", {"sumT": r.stack_in})
        except (KeyError, CalcError) as e:
            r.refused = "A formula this answer needs is not available (" + (e.message if isinstance(e, CalcError) else str(e)) + ")."
            return r
        r.used.append("first_trial_estimate")
        force, cycles, current = _out(res, "force_lbf"), _out(res, "weld_cycles"), _out(res, "current_A")
        reduced = r.stack_in > REDUCE_ABOVE_IN
        current_value = current["value"] * (REDUCTION if reduced else 1.0)
        if "force" in estimate and not aluminium:
            r.quantities.append({"name": "Electrode force", "value": force["value"], "text": f"{force['value']:,.0f} lbf", "unit": f"({force['value'] * LBF_TO_N / 1000:.2f} kN)",
                                 "steps": force["steps"], "extra": "converted at 1 lbf = 4.448 N"})
        elif "force" in estimate:
            r.gaps.append("Electrode force for aluminium: the guide quantifies aluminium's current and time only, not its force.")
        base_time, base_current = cycles["value"], current_value
        if aluminium:
            t_val, c_val = base_time * ALUMINIUM_TIME, base_current * ALUMINIUM_CURRENT
        else:
            t_val, c_val = base_time, base_current
        if "time" in estimate:
            r.quantities.append({"name": "Weld time", "value": t_val, "text": f"{t_val:.1f} cycles (60 Hz)", "unit": f"(about {t_val / 60 * 1000:.0f} ms)", "steps": cycles["steps"] + (" then x 1/3 for aluminium" if aluminium else ""),
                                 "extra": ""})
        if "current" in estimate:
            steps = current["steps"] + (f" then x {REDUCTION:g} because the stack is above {REDUCE_ABOVE_IN:.3f} in" if reduced else "") + (" then x 3 for aluminium" if aluminium else "")
            r.quantities.append({"name": "Weld current", "value": c_val, "text": f"{c_val:,.0f} A", "unit": "", "steps": steps, "extra": ""})
        r.notes.append(f"Stack thickness ΣT = {stack_mm:g} mm = {r.stack_in:.4f} in (the sum of all the sheets, converted to inches because the guide's force, time and current rule is in inches).")

    guide = knowledge.table(DOC, "6. Material-Family Adjustment")
    if r.family in ("hsla", "ahss", "zinc", "mixed"):
        keys = {"hsla": ["HSLA"], "ahss": ["AHSS"], "zinc": ["Zinc"], "mixed": ["AHSS" if "ahss" in named else "HSLA"]}[r.family]
        for key in keys:
            row = knowledge.row(guide, key)
            if row:
                col = next(c for c in guide["header"] if c.startswith("Force"))
                r.caveats.append((row[guide["header"][0]], row[col]))
                basis = next(c for c in guide["header"] if c.startswith("Basis"))
                r.caveats.append(("Basis", row[basis]))
        if scenario.coating and r.family != "zinc":
            zinc = knowledge.row(guide, "Zinc")
            if zinc:
                col = next(c for c in guide["header"] if c.startswith("Force"))
                r.caveats.append(("Zinc-coated steel", zinc[col]))
        if r.family == "mixed":
            r.notes.append("The sheets are different materials. The guide gives no schedule for a mixed stack, so treat these values only as an order of magnitude and qualify the stack.")
    if aluminium and estimate:
        r.notes.append("Aluminium: about 3 times the steel current and about one third of the steel time (the guide's multiplier), applied to the same stack.")
    if r.family == "mild" and not named:
        r.notes.append("No material was named, so the mild / low-carbon steel baseline is shown.")

    sections = []
    if geometry and not aluminium:
        sections.append("3. Thickness-Driven")
    if estimate:
        sections += ["2. How the Numbers", "4. Indicative Force"] + (["5. Applying the Aluminum"] if aluminium else [])
    if r.caveats or aluminium:
        sections.append("6. Material-Family Adjustment")
    for heading in sections:
        t = knowledge.table(DOC, heading)
        if t and t["chunk_ids"][0] not in r.chunks:
            r.chunks.append(t["chunk_ids"][0])
    return r
