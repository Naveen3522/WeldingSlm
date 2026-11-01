"""The agents inside the chat: a question that needs more than one lookup is answered section by section, in the same events as any answer.

    AgentChat.claim(ctx) -> a list of events when an agent should answer, else None (the normal pipeline carries on)

Who answers is decided by `decide`, deliberately narrowly, so questions the pipeline already answers well ("Why does expulsion happen in spot
welding?") stay with it:

    wps         "draft a WPS for ..."                                  -> the WPS draft (document 08's data groups) with a Word download
    parameters  a spot-welding value asked for (force, time, current, electrode, nugget, spacing) for a sheet -> starting values (document 20)
    scenario    a described situation: three or more details, or two with "I am / I get" or a "which process", and a problem or question
A short reply that only adds details ("the sheets are 1.2 mm and galvanised") continues the earlier agent question.
"""
import datetime
import re

from welding_ai.agents import parameters, scenario, slots, wps, wps_generic
from welding_ai.agents.knowledge import Knowledge
from welding_ai.core import events

WPS_REQUEST = re.compile(r"\b(?:draft|create|generate|write|prepare|make|build|produce)\b.{0,40}\b(?:WPS|welding\s+procedure\s+specification)\b|"
                         r"\b(?:WPS|welding\s+procedure\s+specification)\b.{0,30}\b(?:draft|template)\b", re.I)
SHORT_REPLY = 160
EARLIER = 2
VIA = {"parameters": "parameters", "scenario": "scenario", "troubleshoot": "scenario", "wps": "wps"}      # what the Agents page and the slash commands ask for -> the agent


def decide(text, s):
    """-> "wps", "parameters", "scenario" or None."""
    if WPS_REQUEST.search(text):
        return "wps"
    wanted = parameters.asked(text)
    arc = bool(s.process) and s.process != "rsw"
    has_basis = bool(s.sheets or s.materials or s.coating)             # something to work from: "What is a weld nugget?" is a definition, not a calculation
    if wanted and has_basis and parameters.in_context(text, s.process) and not arc and not s.symptom and "fix" not in s.asks and "process" not in s.asks:
        return "parameters"
    asking = bool(s.symptom or s.asks or s.first_person)
    if asking and (s.parts() >= 3 or (s.parts() >= 2 and (s.first_person or "process" in s.asks))):
        return "scenario"
    return None


def _statement(text, s):
    """True for a short message that adds details but asks nothing (so it may continue an earlier agent question)."""
    return len(text) <= SHORT_REPLY and s.parts() >= 1 and not s.asks and "?" not in text


class AgentChat:
    def __init__(self, usable, drafts=None):
        """usable() -> the formulas people may use; drafts: a DraftStore for generated files (None: no downloads)."""
        self.usable, self.drafts = usable, drafts

    # (the text to read, the earlier questions it is joined with). A short statement of details is also read with what came before it.
    def _candidates(self, ctx):
        yield ctx.q, ""
        earlier = [m["content"] for m in ctx.history if m.get("role") == "user" and m.get("content")]
        if _statement(ctx.q, slots.read(ctx.q)):
            for n in range(1, EARLIER + 1):
                if len(earlier) >= n:
                    before = " ".join(earlier[-n:])
                    yield before + " " + ctx.q, before

    def claim(self, ctx):
        """ctx.via names an agent chosen on the Agents page (or by a slash command): that agent answers, whatever `decide` would say."""
        h, via = ctx.h, getattr(ctx, "via", "")
        if getattr(h, "tables", None) is None or via == "formula":
            return None
        if via in VIA:
            return self._answer(ctx, VIA[via], slots.read(ctx.q), ctx.q)
        for text, before in self._candidates(ctx):
            s = slots.read(text)
            route = decide(text, s)
            if route is None or (before and decide(before, slots.read(before)) is None):        # a reply continues only an earlier agent question
                continue
            if route == "scenario" and h.faq and h.faq.accept(h.faq.match(ctx.q)):
                return None                                                                        # a verified FAQ answer is better than an assembled one
            return self._answer(ctx, route, s, text)
        return None

    # ------------------------------------------------------------------ building the answer
    def _answer(self, ctx, route, s, text):
        h = ctx.h
        kn = Knowledge(h.tables.t, h.index)
        formulas = {f["id"]: f for f in self.usable()}
        if route == "wps":
            return self._wps(ctx, s, kn, formulas)
        if route == "parameters":
            a = scenario.parameters_answer(s, parameters.asked(text), kn, formulas)
        else:
            a = scenario.assemble(s, kn, formulas)
        return self._events(ctx, a, route, confidence="high" if route == "parameters" else "medium")

    def _events(self, ctx, a, route, confidence, extra=None):
        hits = a.sources
        out = [events.sources(hits, len(hits), [], "good", ctx.notes, route, qtype=route)]
        first_source, first_chunk = (hits[0]["source"], hits[0]["chunk_id"]) if hits else ("", 0)
        for kind, body in a.blocks:
            if kind == "text":
                out.append(events.token(body + "\n"))
            else:
                out.append(events.table(body["title"], first_source, first_chunk, body["header"], body["rows"]))
        if hits:
            out.append(events.cite([{"source": x["source"], "heading": x["heading"], "chunk": x["chunk_id"]} for x in hits]))
        out += extra or []
        out.append(ctx.done(route=route, grounded=True, confidence=confidence, match="good", answer_source="agent"))
        return out

    def _wps(self, ctx, s, kn, formulas):
        d = wps.build(s, kn, formulas)
        if d.refused and s.process not in ("", "rsw"):
            generic = self._generic_wps(ctx, kn)
            if generic is not None:
                return generic
        a = scenario.Answer("wps", sources=[kn.hit(c) for c in d.chunks[:scenario.MAX_SOURCES]])
        if d.refused:
            a.blocks.append(("text", f"**WPS draft**\n{d.refused}"))
            return self._events(ctx, a, "wps_ask", confidence="high")
        names = sorted({r[0] for r in d.rows if not r[4]})
        lines = [f"**WPS draft for resistance spot welding** (status: {d.status or 'DRAFT'}) [1]",
                 f"- {d.filled} of {len(d.rows)} fields were filled from what you described and the parameter guide; the other {len(d.rows) - d.filled} say “{wps.TO_CONFIRM}”.",
                 "- Starting values in the Parameters group are first-trial estimates for qualification trials, not qualified values."]
        if names:
            lines.append("- Groups with fields left for the engineer: " + ", ".join(names) + ".")
        lines += [f"- {n}" for n in d.notes]
        if d.standards:
            lines.append("- The standards the document names: " + "; ".join(name for name, _use in d.standards) + ".")
        lines.append("- This is not a qualified procedure. The document requires a procedure qualification before a WPS is approved for production.")
        a.blocks.append(("text", "\n".join(lines)))
        extra = []
        if self.drafts is not None:
            token = self.drafts.save(wps.to_docx(d), f"WPS-draft-{datetime.date.today():%Y%m%d}.docx")
            extra.append(events.download("Download the WPS draft (.docx)", f"/api/agents/wps/{token}"))
        return self._events(ctx, a, "wps", confidence="medium", extra=extra)

    def _generic_wps(self, ctx, kn):
        """A draft for the process chapter the request names, from that chapter's own parameters (None when there is no fact layer or no such chapter)."""
        facts = getattr(ctx.h, "facts", None)
        found = facts.topics.detect(ctx.q) if facts else {}
        if not found:
            return None
        source = max(found, key=found.get)
        d = wps_generic.build(facts, source, ctx.q)
        a = scenario.Answer("wps", sources=[kn.hit(c) for c in d.chunks[:scenario.MAX_SOURCES]])
        groups = sorted({r[0] for r in d.rows if r[3] == wps_generic.TO_CONFIRM})
        lines = [f"**WPS draft for {d.process}** (status: DRAFT) [1]",
                 f"- {len(d.rows) - d.filled} of {len(d.rows)} fields say “{wps_generic.TO_CONFIRM}”: the {d.process} chapter gives what each parameter controls, not values to qualify with.",
                 "- Groups with fields left for the engineer: " + ", ".join(groups) + "."]
        lines += [f"- {n}" for n in d.notes]
        a.blocks.append(("text", chr(10).join(lines)))
        header = ["Group", "Item", "What the documents say", "Value"]
        a.blocks.append(("table", {"title": f"WPS draft: {d.process}", "header": header, "rows": [list(r) for r in d.rows]}))
        extra = []
        if self.drafts is not None:
            token = self.drafts.save(wps_generic.to_docx(d), f"WPS-draft-{d.process.replace(' ', '-')}-{datetime.date.today():%Y%m%d}.docx")
            extra.append(events.download("Download the WPS draft (.docx)", f"/api/agents/wps/{token}"))
        return self._events(ctx, a, "wps", confidence="medium", extra=extra)


class AgentTools:
    """The part of the agents that is a tool, not an answer: a WPS draft with its Word download and the starting values for a spot weld (a table of numbers). A described
    situation ("I get porosity when MIG welding...") is not claimed here: the model answers it from the passages like any other question."""
    ROUTES = ("wps", "parameters")

    def __init__(self, agents):
        self.agents = agents

    def claim(self, ctx):
        h = ctx.h
        if getattr(h, "tables", None) is None or getattr(ctx, "via", "") == "formula":
            return None
        for text, before in self.agents._candidates(ctx):
            s = slots.read(text)
            route = decide(text, s)
            if route in self.ROUTES and not (before and decide(before, slots.read(before)) is None):
                return self.agents._answer(ctx, route, s, text)
        return None
