"""The harness: the facade over the pipeline. It owns the services (index, engine, FAQ, cache, model client) and runs each
question through the chain of steps in `steps.py`.

`Harness.stream()` yields UI events; `Harness.run()` collects them into one result (used by the evaluation).
Public names re-exported here (Config, llm_stream, NOT_FOUND, FaqMatcher, ...) keep older imports working.
"""
import json

from welding_ai import paths
from welding_ai.answering import engine as engine_mod
from welding_ai.answering.answering import NOT_FOUND  # noqa: F401  (re-exported)
from welding_ai.core import events
from welding_ai.core.config import Config
from welding_ai.core.tuning import apply_tuned_thresholds
from welding_ai.llm.ollama import OLLAMA, LLMError, llm_stream, warm  # noqa: F401  (re-exported; the evaluation swaps llm_stream here)
from welding_ai.orchestration.cache import AnswerCache
from welding_ai.orchestration.faq import FaqMatcher
from welding_ai.orchestration import provenance
from welding_ai.orchestration.prompts import MODEL_SOURCE_LINE, NO_DIRECT, SYSTEM_PROMPT  # noqa: F401  (re-exported)
from welding_ai.orchestration.responders import Responders
from welding_ai.orchestration.steps import Ctx, default_steps
from welding_ai.retrieval import rag, retrieval, tables
from welding_ai.services.clients import LocalPlanner, RemoteEngine, RemotePlanner

ROOT = paths.ROOT


class Harness:
    # Wire up every service (index, FAQ matcher, table index, planner/engine per mode, cache, responders, step chain).
    def __init__(self, index=None, cfg=None, steps=None):
        self.cfg = cfg or Config()
        apply_tuned_thresholds()                        # the deployed thresholds, whoever builds the harness
        self.index = index or retrieval.load()
        try:
            self.faqs = json.loads((paths.INDEX_DIR / "faq.json").read_text(encoding="utf-8"))
        except Exception:
            self.faqs = []
        self.faq = FaqMatcher(self.index, self.faqs, self.cfg) if self.faqs else None
        self.tables = tables.load(self.index) if getattr(self.cfg, "use_tables", True) else None
        if self.cfg.mode == "distributed":              # understanding + answering are separate services; the same ports, remote adapters
            self.planner = RemotePlanner(self.cfg.understanding_url)
            self.engine = RemoteEngine(self.cfg.answer_url) if getattr(self.cfg, "use_engine", True) else None
        else:
            self.planner = LocalPlanner()
            try:
                self.engine = engine_mod.Engine(self.index, tindex=self.tables) if getattr(self.cfg, "use_engine", True) else None
            except Exception:
                self.engine = None
        self.cache = AnswerCache(self.cfg.cache_path, self.cfg.cache_max)
        self.sources = {c["source"] for c in self.index.chunks}
        self.responders = Responders(self)
        self.formulas = self.agents = self.facts = self.tools = None  # the providers: see attach()
        self.similarity = None                          # closeness in meaning of passages to a question (retrieval/similarity.py); the server sets it
        self.steps = steps or default_steps(self.cfg)

    def attach(self, formulas=None, agents=None, facts=None, tools=None):
        """The server's providers, each with claim(ctx): the calculator (formulas.chat.FormulaChat), the agents (agents.chat.AgentChat) and the facts of the documents
        (answering.facts.FactAnswerer). Without them (the evaluation) every question goes through the plain pipeline."""
        self.formulas, self.agents, self.facts, self.tools = formulas, agents, facts, tools
        return self

    # ---- helpers
    # Build a source-hit dict for a chunk id (used when a cached/FAQ answer needs to re-present its sources).
    def _hit(self, cid, coverage=1.0):
        c = self.index.chunks[cid]
        return {"chunk_id": cid, "source": c["source"], "heading": c["heading"], "text": c["text"], "score": 0.0, "coverage": coverage}

    # Concatenate the focused excerpt of each hit into the model's context, stopping at the char budget.
    def _build_context(self, hits, question, budget=None):
        parts, size, budget = [], 0, budget or self.cfg.context_chars
        for n, h in enumerate(hits, 1):
            section = h["heading"].split(" > ")[-1]
            block = f"[{n}] (document: {h['source']} | section: {section})\n{self.index.focus(h['text'], question, self.cfg.excerpt_chars)}"
            if size + len(block) > budget and parts:
                break
            parts.append(block)
            size += len(block)
        return "\n\n".join(parts), len(parts)

    # Build the chat message list (system prompt + recent history + excerpts/question) for the LLM call.
    def _messages(self, q, context, history, style=""):
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in history[-4:]:
            if m.get("role") in ("user", "assistant") and m.get("content"):
                msgs.append({"role": m["role"], "content": m["content"][:1200]})
        msgs.append({"role": "user", "content": f"EXCERPTS:\n{context}\n\nQUESTION: {q}" + (f"\n\nSTYLE: {style}" if style else "")})
        return msgs

    # Merge the previous question into a dependent follow-up's search query.
    def _retrieval_query(self, q, history, dependent=False):
        """A question that cannot stand alone is searched together with the previous question (spelling-corrected, like the question itself)."""
        prev = [m["content"] for m in history if m.get("role") == "user" and m.get("content")]
        if dependent and prev:
            return self.index.correct(prev[-1])[0] + " " + q
        return q

    # Resolve the extractive answer's cited units back to their source chunk/section, for the UI's "key details".
    def _key_details(self, comp, hits):
        out = []
        for n, text in comp["units"]:
            h = hits[n - 1]
            out.append({"n": n, "chunk_id": h["chunk_id"], "source": h["source"], "section": h["heading"].split(" > ")[-1], "text": text})
        return out

    # A few sample questions for the UI (drawn from the FAQ list) or a fallback if there are none.
    def examples(self):
        return [f["question"] for f in self.faqs[:8:3]] or ["What are the metal transfer modes in GMAW?"]

    # ---- the pipeline
    # Run the question through the step chain, streaming events; the first step that returns events ends the request.
    def stream(self, question, history=None, model=None, mode="answer", k=None, via="", nested=False):
        ctx = Ctx(h=self, question=question, history=history or [], model=model or self.cfg.default_model, mode=mode, k=k or self.cfg.k, via=via, nested=nested)
        for step in self.steps:
            if step.stage:
                yield events.stage(*step.stage)
            out = step.run(ctx)
            if out is not None:
                for ev in out:
                    if ev["type"] == "done":                    # every answer says where its wording came from
                        ev["provenance"] = provenance.describe(ev.get("route"), ev.get("answer_source"), mode == "answer")
                    yield ev
                return

    # Collect stream() events into one non-streaming result dict (used by the evaluation harness and scripts).
    def run(self, question, history=None, model=None, mode="answer", k=None):
        res = {"question": question, "answer": "", "route": None, "sources": [], "used": 0, "match": None, "notes": [], "key_details": [],
               "error": None, "replaced": None, "done": {}, "timings": {}, "points": None}
        for ev in self.stream(question, history, model, mode, k):
            t = ev["type"]
            if t == "sources":
                res.update(sources=ev["sources"], used=ev["used"], match=ev["match"], notes=ev["notes"], key_details=ev.get("key_details", []), qtype=ev.get("qtype"))
            elif t == "token":
                res["answer"] += ev["t"]
            elif t == "table":
                res.setdefault("tables", []).append(ev)
                res["answer"] += "\n" + " | ".join(ev["header"]) + "\n" + "\n".join(" | ".join(r) for r in ev["rows"]) + "\n"
            elif t == "simple":                          # kept apart from "answer" so evaluation of the composed answer is unchanged
                res["simple"] = ev
            elif t == "clarify":
                res["clarify"] = ev["options"]
            elif t == "replace":
                res["replaced"] = {"llm_answer": res["answer"], "reason": ev["reason"]}
                res["answer"] = ev["text"]
            elif t == "error":
                res["error"] = ev["message"]
            elif t == "done":
                res["done"] = ev
                res["route"] = ev.get("route")
                res["timings"] = ev.get("timings", {})
                res["points"] = ev.get("points")
        res["answer"] = res["answer"].strip()
        return res
