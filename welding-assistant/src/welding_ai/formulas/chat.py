"""The formula agent inside the chat: a question that asks for a calculation is answered by the calculator, in the same events as any answer.

    FormulaChat.claim(ctx)    -> a list of events when the question is a calculation request, else None (the normal pipeline carries on)

A question is a calculation request when it names a formula and either gives numbers or says "calculate". A plain "what is heat input?"
is not: it goes to the documents. A short reply that only gives numbers ("I = 260, v = 350") is read together with the earlier questions,
so the user can answer "I still need ..." in the next message.
"""
import re

from welding_ai.core import events
from welding_ai.formulas import ask as agent
from welding_ai.retrieval import rag

CALC_VERB = re.compile(r"\b(calculate|calculation|compute|work out|figure out|find the value)\b", re.I)
SHORT_REPLY = 160          # a follow-up this short that holds a number is read together with the earlier questions
EARLIER = 2                # how many earlier questions it may be joined with
SHOWN_LIMITS = 2           # assumptions shown under a result (all of them are on the Formulas page)
FILLER = {rag.key(w) for w in ("calculate", "calculation", "compute", "work", "figure", "find", "value", "use", "using", "now", "then", "also", "please")}


def wants_calculation(question, reply):
    if reply["state"] == "answered":
        return True
    if reply["state"] in ("need_values", "invalid", "choose"):
        return bool(CALC_VERB.search(question)) or bool(reply["extracted"])
    return False


def _value(x):
    return f"{x:.6g}"


def _example(formula, symbols):
    shown = formula["examples"][0]["inputs"] if formula["examples"] else {}
    return ", ".join(f"{s} = {_value(shown[s]) if s in shown else '…'}" for s in symbols)


class FormulaChat:
    def __init__(self, usable, locate):
        """usable() -> the formulas people may use; locate(document, anchor) -> the index of the chunk holding the quoted passage."""
        self.usable, self.locate = usable, locate

    # The question as asked, then joined with the earlier ones when it is a short reply that only gives numbers.
    @staticmethod
    def _candidates(ctx):
        yield ctx.q, False
        earlier = [m["content"] for m in ctx.history if m.get("role") == "user" and m.get("content")]
        if len(ctx.q) <= SHORT_REPLY and re.search(r"\d", ctx.q):
            for n in range(1, EARLIER + 1):
                if len(earlier) >= n:
                    yield " ".join(earlier[-n:] + [ctx.q]), True

    # A reply continues a calculation only if it brings no topic of its own: every word in it belongs to the formula
    # ("I = 260", "the voltage is 28"). "calculate t8/5 cooling time for 20 mm plate" has a topic, so it is a new question.
    @staticmethod
    def _continues(question, formula):
        own = {t for t in rag.tokens(question) if not any(c.isdigit() for c in t)} - FILLER
        known = set()
        for text in [formula["name"]] + formula["aliases"] + [x for v in formula["variables"] + formula["outputs"] for x in (v["name"], v["symbol"], v["unit"])]:
            known |= set(rag.tokens(text))
        return own <= known

    def claim(self, ctx):
        """ctx.via == "formula" (the /calculate command) means the person asked for the calculator, so it answers even without the word "calculate"."""
        via = getattr(ctx, "via", "")
        formulas = self.usable()
        if not formulas or (via and via != "formula"):
            return None
        by_id = {f["id"]: f for f in formulas}
        for question, joined in self._candidates(ctx):
            reply = agent.ask(formulas, question)
            if joined and not (reply["formula_id"] in by_id and self._continues(ctx.q, by_id[reply["formula_id"]])):
                continue
            if wants_calculation(question, reply) or (via == "formula" and reply["formula_id"] in by_id):
                return self._build(ctx, question, reply, by_id.get(reply["formula_id"]), by_id)
        return self._list(ctx, formulas) if via == "formula" else None

    # /calculate with no formula named, or one that matches nothing: the formulas that can be used.
    def _list(self, ctx, formulas):
        names = "\n".join(f"- {f['name']}" for f in formulas)
        text = ("I could not tell which formula to use. Say what to calculate and give the values, for example `/calculate heat input at 28 V, 260 A, 350 mm/min`.\n\n"
                f"Formulas you can use:\n{names}")
        return [events.no_sources("formula_ask", ctx.notes, match="good"), events.token(text),
                ctx.done(route="formula_ask", grounded=True, confidence="high", match="good", answer_source="formula")]

    # ------------------------------------------------------------------ building the answer
    def _source(self, ctx, formula):
        """-> (hit for the sources panel, cite item) for the passage the formula was taken from, or (None, None)."""
        src = formula["source"]
        cid = self.locate(src["document"], src["anchor"]) if src.get("document") and src.get("anchor") else None
        if cid is None:
            return None, None
        hit = ctx.h._hit(cid, 1.0)
        hit["text"] = ctx.h.index.focus(hit["text"], formula["name"], 900)
        return hit, {"source": hit["source"], "heading": hit["heading"], "chunk": cid}

    def _build(self, ctx, question, reply, formula, by_id):
        state = reply["state"]
        if state == "choose":
            options = [{"label": c["name"], "query": f"{question.rstrip(' ?.')} ({c['name']})"} for c in reply["candidates"]]
            return [events.no_sources("formula_ask", ctx.notes, match="good"), events.token(reply["message"]), events.clarify(options),
                    ctx.done(route="formula_ask", grounded=True, confidence="high", match="good", answer_source="formula")]

        hit, cite = self._source(ctx, formula)
        out = [events.sources([hit] if hit else [], 1 if hit else 0, [], "good", ctx.notes, "formula", qtype="formula")]
        name = formula["name"]
        if state == "answered":
            out += self._answer(formula, reply, cite["chunk"] if cite else None)
        else:
            out += self._ask_for_more(formula, reply)
        if cite:
            out.append(events.cite([cite]))
        route = "formula" if state == "answered" else "formula_ask"
        out.append(ctx.done(route=route, grounded=True, confidence="high", match="good", answer_source="formula", formula_id=formula["id"], formula=name, formula_status=formula.get("status", "")))
        return out

    def _answer(self, formula, reply, chunk):
        result, variables = reply["result"], {v["symbol"]: v for v in formula["variables"]}
        lines = [f"**{formula['name']}**"]
        for o in result["outputs"]:
            lines.append(f"- **{o['name']} = {o['text']} {o['unit']}**: `{o['steps']}`")
        notes = []
        notes += [s["message"] for s in result["skipped"]]
        notes += result["assumed"] + result["warnings"]
        if reply["extracted"]:
            read = "; ".join(f"{m['symbol']} = {_value(m['value'])} (from “{m['text']}”)" for m in reply["extracted"])
            notes.append(f"Numbers read from your question: {read}. Check them before you rely on the result.")
        if reply["conflicts"]:
            notes.append("You gave two different values for " + ", ".join(reply["conflicts"]) + ", so neither was used.")
        notes += [f"Limit: {a}" for a in formula["assumptions"][:SHOWN_LIMITS]]
        rows = [[i["symbol"], i["name"], _value(i["value"]) + (" (assumed)" if i["assumed"] else ""), variables[i["symbol"]]["unit"]] for i in result["inputs"]]
        out = [events.token("\n".join(lines) + "\n"),
               events.table("Inputs used", formula["source"].get("document", ""), chunk, ["Symbol", "Input", "Value", "Unit"], rows)]
        if notes:
            out.append(events.token("\n".join("- " + n for n in notes)))
        return out

    def _ask_for_more(self, formula, reply):
        variables = {v["symbol"]: v for v in formula["variables"]}
        if reply["missing"]:
            needed = ", ".join(f"{variables[s]['name']} ({s}, {variables[s]['unit']})" for s in reply["missing"])
            text = (f"**{formula['name']}**\nTo calculate this I still need: {needed}.\n"
                    f"Send them in your next message, for example: `{_example(formula, reply['missing'])}`.")
        else:
            text = f"**{formula['name']}**\n{reply['message']}"
        if reply["extracted"]:
            text += "\nNumbers read so far: " + "; ".join(f"{m['symbol']} = {_value(m['value'])}" for m in reply["extracted"]) + "."
        if reply["conflicts"]:
            text += "\nYou gave two different values for " + ", ".join(reply["conflicts"]) + ", so neither was used."
        return [events.token(text)]
