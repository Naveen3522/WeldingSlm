"""The pipeline as a chain of steps (chain of responsibility).

Every step gets the request context. It returns None to pass the request on, or an iterable of events that ends it.
The order is set in `default_steps()`; add, remove or reorder steps there without touching the others.

    Guard -> Safety -> OddInput -> SmallTalk -> OutOfScope -> AgentTopic -> Manual -> Formula -> Agent -> Understand -> FaqAndCache -> EngineAfterFaq -> StrictMode -> PassageAnswer
"""
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from welding_ai.agents import topics
from welding_ai.answering import answering
from welding_ai.answering.answering import NOT_FOUND
from welding_ai.core import events
from welding_ai.orchestration.prompts import MODEL_SOURCE_LINE, NO_DIRECT, out_of_scope
from welding_ai.retrieval import rag
from welding_ai.understanding import guard, planner

FOLLOWUP_WORDS = 3          # a question that opens like a follow-up ("why", "and", "so") is one only if it has this few content words or fewer
_FOLLOWUP = re.compile(r"^\s*(and|also|but|so|then|why|how so|what about|how about)\b", re.I)
_PRONOUN = re.compile(r"\b(it|its|this|that|they|them|their|those|these)\b", re.I)
_PRICE = re.compile(r"[$€£₹]|\b(?:USD|EUR|GBP|INR|dollars?|euros?|rupees?)\b")


# Resolve the orchestration.harness module at call time (so tests/evaluation can swap in a fake llm_stream).
def _llm():
    from welding_ai.orchestration import harness        # resolved at call time: the evaluation swaps the model client
    return harness


@dataclass
class Ctx:
    h: Any                                  # the Harness (services: index, engine, tables, faq, cache, cfg, responders)
    question: str
    history: List[dict]
    model: str
    mode: str
    k: int
    t0: float = field(default_factory=time.time)
    T: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    q: str = ""                             # the sanitised question
    plan: Optional[dict] = None
    eng_first: bool = False
    nested: bool = False                    # a part of a bigger question answered on its own: the steps that split questions into parts do not run again
    agent: str = ""                          # the agent the question was sent to (kept for the search to prefer that agent's passages)
    parts: list = field(default_factory=list)   # the parts of a question that asks several things, each searched on its own
    via: str = ""                            # an agent chosen on purpose (the Agents page, or a slash command): formula, parameters, scenario, troubleshoot, wps

    # Build the final "done" event, stamping total elapsed time.
    def done(self, **kw):
        self.T["total_s"] = round(time.time() - self.t0, 3)
        return events.done(self.T, **kw)

    # True if the index supports entity/identifier-lookup detection and this query is one.
    def entity_query(self, q):
        return getattr(self.h.index, "entity_query", lambda _q: False)(q)

    @property
    def prev_question(self):
        return next((m["content"] for m in reversed(self.history) if m.get("role") == "user" and m.get("content")), "")

    @property
    def standalone(self):
        """A question that can be understood on its own. Only a short follow-up ("and its cost?", "why?") depends on the
        previous turn; every other question in a conversation gets the full pipeline, exactly like the first one."""
        if not self.history:
            return True
        n = len(rag.tokens(self.q))
        # "why?" / "and its cost?" are follow-ups; "Why does expulsion happen in spot welding?" has a topic of its own and stands alone
        if n <= 2 or (n <= FOLLOWUP_WORDS and _FOLLOWUP.search(self.q)) or (n <= 4 and _PRONOUN.search(self.q)):
            return False
        return self.h.index.has_anchor(self.h.index.correct(self.q)[0])          # no word of the documents' own topics: it only makes sense after the previous turn


class Step:
    stage = None                            # (id, label) shown on the page while this step works; None for steps that finish at once

    def run(self, ctx):
        return None


class GuardStep(Step):
    """Strip markup, remove injected instructions, refuse empty input."""

    # Sanitize the raw question; short-circuit with an error/refusal event if it's empty or a blocked injection.
    def run(self, ctx):
        g = guard.sanitize(ctx.question, content_tokens=rag.tokens)
        ctx.T["guard_ms"] = round((time.time() - ctx.t0) * 1000, 2)
        ctx.notes = list(g["notes"])
        if g["blocked"] == "empty":
            return [events.error("Please type a question."), ctx.done(route="guard_empty", grounded=False)]
        if g["blocked"] == "injection":
            return [events.no_sources("guard_injection", ctx.notes, match="none"),
                    events.token("I can only answer questions about the welding documents. I can't change how I work or share my instructions."),
                    ctx.done(route="guard_injection", grounded=False, confidence="n/a")]
        ctx.q = g["text"]
        return None


class SafetyStep(Step):
    """A request to defeat a safety device is answered like any other question: from the documents only. A note says the documents give no
    way to bypass a safety device, so what follows is what they say about the safeguard."""

    # Add a safety note (never blocks) if the question asks to bypass/defeat a safety device.
    def run(self, ctx):
        if guard.is_safety_bypass(ctx.q):
            ctx.notes.append(guard.SAFETY_NOTE)
        return None


class OutOfScopeStep(Step):
    """Treating an injury, or naming a brand or vendor, is not in the welding documents: one of the randomized \"not found\" replies, with no search."""

    # Refuse with a "not found" reply if the question is a medical or brand-name request, with no search.
    def run(self, ctx):
        note = "" if guard.is_medical_treatment(ctx.q) else " The documents name no brands, vendors or products." if guard.is_brand_request(ctx.q) else None
        if note is None:
            return None
        return [events.no_sources("not_found", ctx.notes, match="none"), events.token(out_of_scope(len(ctx.h.sources)) + note),
                ctx.done(route="not_found", grounded=False, confidence="n/a", match="none")]


def _norm(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def misfiled(h, agent, question):
    """-> a line when this question is a saved pair (added by a person) that is filed under another agent, else None. `h.pair_areas` is {normalised question: area}, kept by the server."""
    areas = getattr(h, "pair_areas", None) or {}
    filed = areas.get(_norm(question))
    name = topics.AGENTS.get(agent, ("",))[0]
    if not filed or filed in ("Chat", "General", name):
        return None
    return f"This saved question is filed under {filed}, not {name}. Ask an administrator to correct it."


class AgentTopicStep(Step):
    """A question sent to a named agent (Fault Finder, Procedure Advisor) that belongs to another one gets a single line saying so (agents/topics.py). One that fits goes on
    through the ordinary steps."""

    def run(self, ctx):
        if ctx.via not in topics.AGENTS:
            return None
        facts = getattr(ctx.h, "facts", None)
        seen = facts.understand(ctx.q, ctx.history) if facts is not None else None
        line = topics.redirect(ctx.via, seen.tasks) if seen is not None else None
        ctx.agent, ctx.via = ctx.via, ""
        if line is None:                                           # a saved pair asked on an agent it is not filed under: say so, so the person can ask an administrator to correct it
            line = misfiled(ctx.h, ctx.agent, ctx.q)
        if line is None:
            return None
        return [events.no_sources("wrong_agent", ctx.notes, match="none"), events.token(line), ctx.done(route="wrong_agent", grounded=False, confidence="n/a", match="none")]


class OddInputStep(Step):
    """Keyboard-mash, code-like or letterless input never reaches the search or the model."""

    # Refuse if the input is keyboard-mash/code-like rather than a readable question.
    def run(self, ctx):
        if not guard.is_odd_input(ctx.q):
            return None
        return [events.no_sources("odd_input", ctx.notes), events.token(guard.ODD_REPLY), ctx.done(route="odd_input", grounded=False, confidence="n/a")]


class SmallTalkStep(Step):
    # Reply with a canned small-talk response (greeting/thanks/bye/about) if detected, else pass through.
    def run(self, ctx):
        intent = guard.detect_intent(ctx.q)
        if not intent:
            return None
        return [events.no_sources("intent", ctx.notes), events.token(guard.intent_reply(intent, ctx.h.examples())),
                ctx.done(route="intent", grounded=False, confidence="n/a", intent=intent)]


class ProviderStep(Step):
    """A question one of the server's providers can take over is answered by it, from the documents, in the same events as any answer. A provider is anything with
    claim(ctx) -> events or None: the calculator (formulas/chat.py), the facts of the documents for a question that asks several things (answering/facts.py: "why does it
    happen, what are its consequences and which standard governs it", answered ask by ask) and the agents (agents/chat.py: a described situation, a spot-welding value, a
    WPS request). The server attaches them with Harness.attach; the evaluation has none, so it is unaffected."""

    def __init__(self, name, label):
        self.name, self.stage = name, (name, label)

    # Answer with the provider's events when it takes the question; pass every other question on.
    def run(self, ctx):
        provider = getattr(ctx.h, self.name, None)
        return provider.claim(ctx) if provider is not None else None


class UnderstandStep(Step):
    """Understand the question (intent, layout, parts); stop early for unknown words / standards and vague questions;
    answer at once when a layout or several parts were asked for."""
    stage = ("understand", "Reading the question")

    # intents that are lookups of a named standard ("how is ISO 4063 used"): answered from the standard's own row, not by the engine
    ENTITY_LOOKUP = ("topic", "define", "comply", "explain")

    # Plan the question, resolve follow-up context, and short-circuit for missing standards, unknown words,
    # vague questions, or an engine answer when the question explicitly asked for a layout/multiple parts.
    def run(self, ctx):
        h, cfg = ctx.h, ctx.h.cfg
        if not h.engine or ctx.mode == "search":
            return None
        t1 = time.time()
        q_t, fixes_t = h.index.correct(ctx.q) if cfg.use_correct else (ctx.q, [])
        plan = ctx.plan = h.planner.analyze(q_t)
        standalone = ctx.standalone
        wants_layout = bool(plan["format"] or plan["length"] or len(plan["parts"]) > 1 or plan["intent"] not in self.ENTITY_LOOKUP)
        if standalone and ctx.entity_query(ctx.q) and not wants_layout and not plan.get("nav"):
            ctx.plan = None
            return None
        if not standalone:                                   # a short follow-up: read it together with the previous question
            prev = h.planner.analyze(h.index.correct(ctx.prev_question)[0]) if ctx.prev_question else None
            if prev is None:
                ctx.plan = None
                return None
            plan = ctx.plan = planner.with_context(plan, prev)
        missing = h.responders._std_suggest(q_t)
        if missing:
            return h.responders._serve_std_missing(missing, ctx)
        unseen = h.index.unseen_words(q_t)
        if unseen:
            return h.responders._serve_unknown(unseen, plan, ctx.notes, ctx.T, ctx.t0)
        ctx.eng_first = bool(plan["format"] or plan["length"] or len(plan["parts"]) > 1 or not standalone)
        if plan["vague"]:
            return h.responders._serve_clarify(plan, ctx.notes, ctx.T, ctx.t0, t1)
        if ctx.eng_first:
            ans = h.engine.answer(plan, h.tables)
            if ans:
                return h.responders._serve_answer(ans, plan, q_t, fixes_t, ctx.notes, ctx.T, ctx.t0, t1, ctx.mode, ctx.model)
        return None


class FaqAndCacheStep(Step):
    """Verified FAQ answers and previously verified model answers, for stand-alone questions."""
    stage = ("known", "Looking for a known answer")

    # Try a verified FAQ match, then a cached answer, for a stand-alone (non-entity-lookup) question.
    def run(self, ctx):
        h, cfg = ctx.h, ctx.h.cfg
        if not ctx.standalone or ctx.entity_query(ctx.q):
            return None
        t1 = time.time()
        if cfg.use_faq and h.faq:
            m = h.faq.match(ctx.q)
            if h.faq.accept(m):
                return h.responders._serve_faq(h.faqs[m["idx"]], m, ctx.notes, ctx.T, ctx.t0, t1)
        if cfg.use_cache:
            hit = h.cache.get(ctx.q)
            if hit and hit.get("model") in (None, ctx.model) and (ctx.mode != "verbatim" or hit.get("route") in ("extractive", "fallback_extractive")):
                return h.responders._serve_cache(hit, ctx.notes, ctx.T, ctx.t0, t1, question=ctx.q)
        ctx.T["cache_ms"] = round((time.time() - t1) * 1000, 2)
        return None


class EngineAfterFaqStep(Step):
    """Plain questions: the engine gets a turn after the FAQ."""

    # Give the engine a turn on plain (non-layout-first) questions after the FAQ/cache found nothing.
    def run(self, ctx):
        if ctx.plan is None or ctx.eng_first:
            return None
        ans = ctx.h.engine.answer(ctx.plan, ctx.h.tables)
        if not ans:
            return None
        return ctx.h.responders._serve_answer(ans, ctx.plan, ctx.plan["question"], [], ctx.notes, ctx.T, ctx.t0, time.time(), ctx.mode, ctx.model)


class StrictModeStep(Step):
    """With `legacy_fallback` off, a question the engine cannot answer is reported as not covered."""

    # Refuse (rather than falling through to the passage pipeline) when legacy_fallback is off and the engine had no answer.
    def run(self, ctx):
        if ctx.plan is None or ctx.h.cfg.legacy_fallback or not ctx.standalone:
            return None
        return [events.sources([], 0, rag.query_words(ctx.q), "none", ctx.notes, "not_found"),
                events.token(out_of_scope(len(ctx.h.sources)) + ctx.h.responders._related_text(ctx.q)),
                ctx.done(route="not_found", grounded=False, confidence="n/a", match="none")]


class PassageAnswerStep(Step):
    """Passage pipeline: retrieve -> gate -> (instant extract | model reads the excerpts -> verify -> fallback)."""
    stage = ("search", "Searching the documents")

    # The final fallback: retrieve passages, gate on match quality, then extract/compose/model-answer.
    def run(self, ctx):
        h, cfg = ctx.h, ctx.h.cfg
        q, history, notes, plan = ctx.q, ctx.history, ctx.notes, ctx.plan
        t1 = time.time()
        q_fixed, fixes = h.index.correct(q) if cfg.use_correct else (q, [])
        fixes = [f for f in fixes if " " not in f[1]]
        if fixes:
            notes.append("Searched for the corrected spelling: " + ", ".join(f"{a} → {b}" for a, b in fixes) + ".")
        hits, match, merged = self._retrieve(ctx, q_fixed, fixes, history)
        lang = guard.language_hint(q)
        if match != "good" and lang:
            notes.append("The documents are in English. Try asking the question in English." if lang == "a non-English language"
                         else f"This looks like {lang}. The documents are in English, so try asking in English.")
        if plan is not None and plan.get("wants_price") and hits and not _PRICE.search(" ".join(x["text"] for x in hits[:3])):
            return [events.sources([], 0, rag.query_words(q_fixed), "none", notes, "not_found"),
                    events.token(out_of_scope(len(ctx.h.sources)) + " The documents give no prices or costs."),
                    ctx.done(route="not_found", grounded=False, confidence="n/a", match="none")]
        return self._answer(ctx, merged, hits, match, t1)

    # The passages the previous answer was built from, by chunk id from the client or by re-searching its text.
    @staticmethod
    def _previous_passages(ctx, question, history):
        """The passages the previous answer was built from: the ids the page sent with it, else found by searching for the answer's own text.
        Empty when the previous answer did not come from the documents (a refusal)."""
        h = ctx.h
        last = next((m for m in reversed(history) if m.get("role") == "assistant" and m.get("content")), None)
        if last is None:
            return []
        ids = [c for c in (last.get("chunks") or []) if isinstance(c, int) and 0 <= c < len(h.index.chunks)][:6]
        if ids:
            return [{"chunk_id": c, "source": h.index.chunks[c]["source"], "heading": h.index.chunks[c]["heading"], "text": h.index.chunks[c]["text"],
                     "score": 0.0, "coverage": round(h.index._coverage(question, c), 3), "entity_match": False, "units": []} for c in ids]
        found = h.index.search(last["content"][:500], 3)
        return found if found and found[0]["coverage"] >= h.cfg.weak else []

    # Search for passages, trying the previous answer's own passages first for a dependent follow-up.
    def _retrieve(self, ctx, q_fixed, fixes, history):
        """Search for the passages. A question that depends on the previous turn is looked up in this order:
        1. the passages the previous answer came from (found by searching for the answer's own text): "why does the order matter" after an answer
           about the order of screening steps;
        2. the question on its own, if that lands in a document the previous answer also came from;
        3. the previous question added to the question: a question about "it" or "that" needs it.
        A question that stands alone is simply searched."""
        h, cfg = ctx.h, ctx.h.cfg
        merged = h._retrieval_query(q_fixed, history, dependent=not ctx.standalone)

        def search(query):
            hits = h.index.search(h.index.expand(query) if cfg.use_expand else query, ctx.k)
            top = hits[0]["coverage"] if hits else 0.0
            match = "none" if top < cfg.hard_min else ("weak" if top < cfg.weak else "good")
            if fixes and match == "weak":
                match = "none"      # a spelling correction may rescue a good match but must never create a weak one
            return hits, match

        if merged != q_fixed:
            shown = self._previous_passages(ctx, q_fixed, history)
            if shown:
                if answering.compose_extractive(h.index, q_fixed, shown)["text"]:
                    return shown, "good", q_fixed
                own_hits, own_match = search(q_fixed)
                if own_match == "good" and own_hits[0]["source"] in {x["source"] for x in shown}                         and answering.compose_extractive(h.index, q_fixed, own_hits[:ctx.k])["text"]:
                    return own_hits, own_match, q_fixed
        hits, match = search(merged)
        return hits, match, merged

    # Given retrieved hits, decide the route: no match, search-only, extractive, or model-answered.
    def _answer(self, ctx, q_fixed, hits, match, t1):
        h, cfg = ctx.h, ctx.h.cfg
        model, mode, T, notes, plan = ctx.model, ctx.mode, ctx.T, ctx.notes, ctx.plan
        context, used = h._build_context(hits, q_fixed)
        comp = answering.compose_extractive(h.index, q_fixed, hits[:used]) if hits and match != "none" else {"text": "", "units": [], "qtype": None, "top_score": 0}
        key_details = h._key_details(comp, hits[:used]) if comp["units"] else []
        T["retrieval_ms"] = round((time.time() - t1) * 1000, 2)
        yield events.sources(hits, used, rag.query_words(q_fixed), match, notes, "retrieval", key_details=key_details, qtype=comp["qtype"])

        if not hits or match == "none":
            yield events.token(out_of_scope(len(h.sources)) + h.responders._related_text(q_fixed))
            yield ctx.done(route="not_found", grounded=False, confidence="n/a", match=match)
            return
        if mode == "search":
            yield ctx.done(route="search_only", grounded=True, match=match)
            return

        policy = "extractive" if mode == "verbatim" else cfg.policy        # "verbatim" = instant answer straight from the documents
        if hits and hits[0].get("entity_match") and comp["text"]:
            policy = "extractive"           # a question about a named standard is answered with the document's own table row
        if match == "weak" and cfg.weak_policy == "extractive" and policy != "extractive":
            policy = "extractive"
        if policy == "extractive":
            if comp["text"]:
                yield events.token(comp["text"])
                yield ctx.done(route="extractive", grounded=True, confidence="medium" if match == "good" else "low", match=match, answer_source="extractive")
            else:
                alt = h.engine.topic(plan) if (plan is not None and h.engine) else None
                if alt and not h.engine.paragraph_support(plan, alt):
                    alt = None                        # a section that mentions the words in different places is not an answer
                if alt:
                    yield from h.responders._serve_answer(alt, plan, q_fixed, [], notes, T, ctx.t0, t1, mode, model)
                    return
                yield from self._nothing_direct(ctx, q_fixed, match)
            return

        if cfg.require_support and not comp["text"]:      # no passage sentence supports the question: skip the model entirely
            yield from self._nothing_direct(ctx, q_fixed, match)
            return
        yield from self._model_answer(ctx, q_fixed, hits, used, context, comp, key_details, match, policy)

    # No passage directly answers the question: point to the closest passages if on-topic, else a not-found reply.
    @staticmethod
    def _nothing_direct(ctx, q_fixed, match):
        """No passage answers the question. About the documents' topic: point to the closest passages. Not about it: a randomized not-found reply."""
        if ctx.h.index.has_anchor(q_fixed):
            yield events.token(NO_DIRECT)
            yield ctx.done(route="no_direct_answer", grounded=False, confidence="n/a", match=match)
        else:
            yield events.token(out_of_scope(len(ctx.h.sources)))
            yield ctx.done(route="not_found", grounded=False, confidence="n/a", match=match)

    # Stream the LLM's answer, verify it against the excerpts, and fall back to the extractive answer if it fails.
    def _model_answer(self, ctx, q_fixed, hits, used, context, comp, key_details, match, policy):
        h, cfg = ctx.h, ctx.h.cfg
        q, history, model, T = ctx.q, ctx.history, ctx.model, ctx.T
        harness = _llm()
        if comp["text"]:
            yield events.exact(comp["text"])                  # the document's own wording, always shown with the model's
        raw, sent, t_first, stats, err = "", "", None, {}, None
        opts = {"temperature": cfg.temperature, "top_p": 0.9, "top_k": cfg.top_k, "num_ctx": cfg.model_ctx.get(model, 3072), "num_predict": cfg.num_predict, "repeat_penalty": cfg.repeat_penalty}
        t_llm = time.time()
        try:
            for kind, val in harness.llm_stream(model, h._messages(q_fixed, context, history), opts, cfg.llm_timeout):
                if kind == "stats":
                    stats = val
                    break
                t_first = t_first or time.time()
                raw += val
                cand, stop = raw, False
                if raw.lstrip().startswith(NOT_FOUND):        # keep only the refusal sentence
                    cand, stop = NOT_FOUND, True
                mm = MODEL_SOURCE_LINE.search(cand)            # the page shows the real sources
                if mm:
                    cand, stop = cand[:mm.start()].rstrip(), True
                if len(cand) > len(sent):
                    yield events.token(cand[len(sent):])
                    sent = cand
                if stop:
                    break
        except harness.LLMError as e:
            err = str(e)
        T["llm_first_token_s"] = round((t_first - t_llm), 3) if t_first else None
        T["llm_total_s"] = round(time.time() - t_llm, 3)
        answer = sent.strip()

        if err:                                                # degrade gracefully to the verbatim answer
            yield events.error(err)
            yield events.replace(comp["text"] or NO_DIRECT, "The model is unavailable, so the closest passages are shown.")
            yield ctx.done(route="extractive_degraded", grounded=bool(comp["text"]), confidence="low", match=match, answer_source="extractive")
            return

        t2 = time.time()
        ver = answering.verify(answer, context, q_fixed, claims=True) if answer else {"ok": False, "reasons": ["empty answer"], "refusal": False}
        T["verify_ms"] = round((time.time() - t2) * 1000, 2)
        route, source, conf = "llm", "model", ("high" if match == "good" else "medium")
        if answer and not ver.get("refusal") and comp["text"] and cfg.agree_min > 0:
            ag = answering.agreement(answer, comp["text"], q_fixed)
            ver["agreement"] = round(ag, 3)
            if ag < cfg.agree_min:
                ver["ok"] = False
                ver.setdefault("reasons", []).append("answer disagrees with the closest passage sentences")
        if ver.get("refusal"):
            route, source, conf = "llm_refusal", "model", "n/a"
        elif policy == "llm_verified" and not ver["ok"]:
            if comp["text"]:
                yield events.replace(comp["text"], "The AI summary did not match the documents closely enough, so the closest passages are shown instead.")
                answer = comp["text"]
                route, source, conf = "fallback_extractive", "extractive", ("medium" if match == "good" else "low")
            else:
                yield events.replace(NO_DIRECT, "The AI summary could not be verified against the documents.")
                answer = NO_DIRECT
                route, source, conf = "no_direct_answer", "none", "n/a"
        if cfg.require_support and route == "llm" and not comp["text"]:
            yield events.replace(NO_DIRECT, "No passage sentence matched the question closely enough to support the AI summary.")
            answer = NO_DIRECT
            route, source, conf = "no_direct_answer", "none", "n/a"
        final_ok = route in ("llm", "fallback_extractive") and match == "good" and not history and bool(answer)
        if final_ok and cfg.use_cache:
            h.cache.put(q, {"answer": answer, "route": route, "model": model, "match": match, "chunk_ids": [x["chunk_id"] for x in hits],
                            "used": used, "key_details": key_details, "confidence": conf, "words": rag.query_words(q_fixed)})
        yield ctx.done(route=route, answer_source=source, grounded=route != "llm_refusal", confidence=conf, match=match, verification=ver,
                       tok_per_s=(round(stats["eval_count"] / (stats["eval_duration"] / 1e9), 1) if stats.get("eval_count") else None),
                       prompt_tokens=stats.get("prompt_eval_count"), numbers_not_in_excerpts=ver.get("numbers_missing", [])[:12])


# The pipeline's ordered step chain. With `cfg.grounded` every question about the documents is answered by the model from the passages most like it (grounded.py); the
# calculator, the hand-written answers and the WPS draft / spot-welding values stay as tools. Without it, the older chain that builds answers from the documents' own text.
def default_steps(cfg=None):
    if cfg is not None and getattr(cfg, "grounded", False):
        from welding_ai.orchestration.grounded import GroundedStep
        return [GuardStep(), SafetyStep(), OddInputStep(), SmallTalkStep(), OutOfScopeStep(), AgentTopicStep(), ProviderStep("manual", "Understanding the question"),
                ProviderStep("formulas", "Checking the formulas"), ProviderStep("tools", "Checking for a draft or a value"), GroundedStep()]
    return [GuardStep(), SafetyStep(), OddInputStep(), SmallTalkStep(), OutOfScopeStep(), AgentTopicStep(), ProviderStep("manual", "Understanding the question"), ProviderStep("formulas", "Checking the formulas"), ProviderStep("facts", "Reading what is asked"), ProviderStep("agents", "Checking the agents"), UnderstandStep(), FaqAndCacheStep(), EngineAfterFaqStep(), StrictModeStep(), PassageAnswerStep()]
