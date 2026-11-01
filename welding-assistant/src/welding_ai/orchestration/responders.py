"""Builds the event streams for each way a request can end: composed answer, clarifying question, FAQ, cached answer,
"not covered". Each method yields UI events (see welding_ai.core.events for the vocabulary)."""
import re
import time

from welding_ai.answering import answering, composer
from welding_ai.answering.answering import NOT_FOUND
from welding_ai.orchestration.prompts import MODEL_SOURCE_LINE, NO_DIRECT, SIMPLE_PROMPT, out_of_scope
from welding_ai.retrieval import rag
from welding_ai.understanding import guard


# Resolve the current llm_stream function at call time (so tests/evaluation can swap the model client).
def _llm_stream():
    from welding_ai.orchestration import harness      # looked up at call time so tests / evaluation can swap the model client
    return harness.llm_stream


# Resolve the LLMError exception class lazily (avoids a hard import at module load).
def _LLMError():
    from welding_ai.llm.ollama import LLMError
    return LLMError


# Strip a leading "NN_" ordering prefix and turn underscores into spaces for a display title.
def _title(source):
    return re.sub(r"^\d+_", "", source.rsplit(".", 1)[0]).replace("_", " ")


class Responders:
    def __init__(self, harness):
        self.h = harness

    # Build source-hit dicts for the engine's cited chunks.
    def _cite_sources(self, cites):
        out = []
        for c in cites[:8]:
            ch = self.h.index.chunks[c["chunk"]]
            out.append({"chunk_id": c["chunk"], "source": ch["source"], "heading": ch["heading"], "text": ch["text"], "score": 1.0,
                        "coverage": 1.0, "entity_match": False, "units": []})
        return out

    # Stream an engine-composed answer's events (sources, text/table blocks, citations, clarify, optional AI note, done).
    def _serve_answer(self, ans, plan, q, fixes, notes, T, t0, t1, mode, model):
        fixes = [f for f in fixes if " " not in f[1]]          # splitting a joined word (servogun -> servo gun) is not worth a note
        if fixes:
            notes = notes + ["Searched for the corrected spelling: " + ", ".join(f"{a} → {b}" for a, b in fixes) + "."]
        srcs = self._cite_sources(ans.cites)
        T["retrieval_ms"] = round((time.time() - t1) * 1000, 2)
        yield {"type": "sources", "sources": srcs, "used": len(srcs), "words": rag.query_words(q), "match": "good",
               "key_details": [], "notes": notes, "qtype": ans.kind, "route": "composed"}
        for b in ans.blocks:
            if b["k"] == "text":
                yield {"type": "token", "t": b["md"] + "\n"}
            else:
                yield {"type": "table", "title": b["title"], "source": b["source"], "chunk": b["chunk"], "header": b["header"],
                       "rows": b["rows"], "note": b["note"]}
        if ans.missing:
            yield {"type": "token", "t": "The documents do not cover: " + "; ".join(ans.missing) + "."}
        yield {"type": "cite", "items": [{"source": c["source"], "heading": c["heading"], "chunk": c["chunk"]} for c in ans.cites[:6]]}
        if ans.clarify:
            yield {"type": "clarify", "options": ans.clarify}
        route = "composed"
        simple = self._simple(ans, plan, q, model, mode, T) if getattr(self.h.cfg, "use_simple", True) else None
        if simple:
            yield {"type": "simple", "blocks": simple["blocks"], "how": simple["how"]}
        elif mode == "answer":
            yield from self._ai_note(ans, q, model, T)
        T["total_s"] = round(time.time() - t0, 3)
        yield {"type": "done", "timings": dict(T), "route": route, "grounded": True, "confidence": "high", "match": "good",
               "answer_source": "documents", "kind": ans.kind, "intent": plan["intent"], "format": plan["format"], "parts": len(plan["parts"])}

    # The plain-English rewrite shown on top of the composed answer: the rule-based rewrite if it passes its checks against the
    # chunks, else the model's rewrite (only when AI is on) if that passes, else None (the composed answer stands alone).
    def _simple(self, ans, plan, q, model, mode, T):
        t = time.time()
        idx = self.h.index
        try:
            r = composer.compose(plan, ans, idx)
            if r and composer.validate(plan, r["blocks"], ans, idx, r["fit"])[0]:
                return r
            if mode == "answer":
                r = self._simple_model(ans, plan, q, model)
                if r:
                    return r
            return None
        except Exception:
            return None
        finally:
            T["simple_ms"] = round((time.time() - t) * 1000, 2)

    # Ask the model to rewrite the cited chunks in the asked layout; kept only if the rewrite passes the same checks.
    def _simple_model(self, ans, plan, q, model):
        parts = [f"[{i}] {self.h.index.focus(self.h.index.chunks[c['chunk']]['text'], q, 900)}" for i, c in enumerate(ans.cites[:5], 1)]
        if not parts:
            return None
        excerpts = "\n\n".join(parts)
        user = f"QUESTION: {q}\nINTENT: {plan['intent']}\nFORMAT: {plan.get('format') or ''}\nLENGTH: {plan.get('length') or ''}\n\nEXCERPTS:\n{excerpts}"
        opts = {"temperature": self.h.cfg.temperature, "top_p": 0.9, "top_k": self.h.cfg.top_k, "num_ctx": self.h.cfg.model_ctx.get(model, 3072),
                "num_predict": 380, "repeat_penalty": self.h.cfg.repeat_penalty}
        raw = ""
        try:
            for kind, val in _llm_stream()(model, [{"role": "system", "content": SIMPLE_PROMPT}, {"role": "user", "content": user}], opts, self.h.cfg.llm_timeout):
                if kind == "stats":
                    break
                raw += val
        except _LLMError():
            return None
        mm = MODEL_SOURCE_LINE.search(raw)
        raw = (raw[:mm.start()] if mm else raw).strip()
        if not raw or raw.startswith(NOT_FOUND[:30]):
            return None
        blocks = composer.parse_markdown(raw)
        ok, _ = composer.validate(plan, blocks, ans, self.h.index, True, evidence=excerpts + "\n" + ans.plain(), claims=True)
        return {"blocks": blocks, "how": "model"} if ok else None

    # Optional: stream the model's plain-words explanation of the composed material, verified and dropped if it fails.
    def _ai_note(self, ans, q, model, T):
        """Optional: the model explains the composed material in plain words, using only that material."""
        material = ans.plain()[:5200]
        msgs = [{"role": "system", "content": "You are the welding documentation assistant. Using ONLY the MATERIAL, answer the QUESTION in plain words "
                 "in 3 to 6 sentences that cover every part of the question. Copy numbers, units and names exactly. Do not add anything that is not in the MATERIAL. Do not write a Sources line. "
                 "If it does not answer the question, reply exactly: " + NOT_FOUND},
                {"role": "user", "content": f"MATERIAL:\n{material}\n\nQUESTION: {q}"}]
        opts = {"temperature": self.h.cfg.temperature, "top_p": 0.9, "top_k": self.h.cfg.top_k, "num_ctx": self.h.cfg.model_ctx.get(model, 3072), "num_predict": 260, "repeat_penalty": self.h.cfg.repeat_penalty}
        raw = ""
        yield {"type": "ai_start"}
        try:
            for kind, val in _llm_stream()(model, msgs, opts, self.h.cfg.llm_timeout):
                if kind == "stats":
                    break
                raw += val
                mm = MODEL_SOURCE_LINE.search(raw)
                if mm:
                    raw = raw[:mm.start()].rstrip()
                    break
                yield {"type": "ai", "t": val}
        except _LLMError() as e:
            yield {"type": "error", "message": str(e)}
            yield {"type": "ai_drop"}
            return
        ver = answering.verify(raw, material, q, claims=True)
        if not raw.strip() or raw.strip().startswith(NOT_FOUND[:30]) or not ver.get("ok"):
            yield {"type": "ai_drop"}

    # Stream a disambiguation ("which one do you mean") event for a vague question.
    def _serve_clarify(self, plan, notes, T, t0, t1):
        opts = self.h.engine.clarify(plan)
        T["total_s"] = round(time.time() - t0, 3)
        yield {"type": "sources", "sources": [], "used": 0, "words": [], "match": "n/a", "key_details": [], "notes": notes, "route": "clarify"}
        yield {"type": "token", "t": "That could mean several things in these documents. Which one do you want?"}
        yield {"type": "clarify", "options": opts}
        yield {"type": "done", "timings": dict(T), "route": "clarify", "grounded": False, "confidence": "n/a"}

    _STD = re.compile(r"\b(ISO|AWS|ASME|ASTM|IPC|EN|DIN|IEC|RWMA)\s?-?\s?([A-Z]?\d[\dA-Za-z.\-/]*)", re.I)

    # A standard number the documents do not contain -> the closest ones they do contain.
    def _std_suggest(self, q):
        """A standard number the documents do not contain (ISO 10477) -> the closest ones they do contain (ISO 10447)."""
        import difflib
        out = []
        known = [t for t in self.h.index.post if t.startswith("std")]
        for org, num in self._STD.findall(q):
            toks = [t for t in rag.tokens(f"{org} {num}") if t.startswith("std")]
            if not toks or any(t in self.h.index.post for t in toks):
                continue
            for c in difflib.get_close_matches(toks[0], known, n=3, cutoff=0.82):
                m = re.match(r"std([a-z]+)(\d.*)", c)
                if m:
                    out.append((f"{org.upper()} {num}", f"{m.group(1).upper()} {m.group(2).upper()}"))
            if not out:
                out.append((f"{org.upper()} {num}", None))
        return out

    # True when two standard numbers differ by one typo (a wrong digit, a swapped pair, a missing/extra digit).
    @staticmethod
    def _one_slip(a, b):
        """True when two standard numbers differ by one typo (a wrong digit, a swapped pair, a missing / extra digit)."""
        x, y = re.sub(r"[^A-Za-z0-9]", "", a).lower(), re.sub(r"[^A-Za-z0-9]", "", b).lower()
        if x == y:
            return True
        if len(x) == len(y):
            d = [i for i in range(len(x)) if x[i] != y[i]]
            return len(d) == 1 or (len(d) == 2 and d[1] == d[0] + 1 and x[d[0]] == y[d[1]] and x[d[1]] == y[d[0]])
        if abs(len(x) - len(y)) == 1:
            s_, l_ = (x, y) if len(x) < len(y) else (y, x)
            return any(l_[:i] + l_[i + 1:] == s_ for i in range(len(l_)))
        return False

    # Say the asked standard isn't in the documents, then either answer for the one close match or offer choices.
    def _serve_std_missing(self, missing, ctx):
        """The question names a standard number that is not in the documents (ISO 10477). Say so, then answer for the
        closest number the documents do contain (ISO 10447) when there is exactly one, or offer the choices."""
        asked = missing[0][0]
        sugg = list(dict.fromkeys(b for a, b in missing if b))
        ctx.T["total_s"] = round(time.time() - ctx.t0, 3)
        yield {"type": "sources", "sources": [], "used": 0, "words": [], "match": "none", "key_details": [], "notes": ctx.notes, "route": "not_found"}
        if len(sugg) != 1 or not self._one_slip(asked, sugg[0]):
            yield {"type": "token", "t": f"The documents do not mention {asked}." + (" Did you mean one of these?" if sugg else "")}
            if sugg:
                yield {"type": "clarify", "options": [{"label": s, "query": f"What does {s} cover and how is it used?"} for s in sugg]}
            yield {"type": "done", "timings": dict(ctx.T), "route": "not_found", "grounded": False, "confidence": "n/a", "match": "none"}
            return
        best = sugg[0]
        yield {"type": "token", "t": f"The documents do not mention {asked}. The closest number they contain is {best}, so this is about {best}:\n"}
        org, num = asked.split(" ", 1)
        pattern = re.escape(org) + r"\s?-?\s?" + re.escape(num)
        fixed = re.sub(pattern, best, ctx.question, count=1, flags=re.I)
        yield from self.h.stream(fixed, history=[], model=ctx.model, mode=ctx.mode, k=ctx.k)

    # Stream a not-found reply naming the words that appear nowhere in the documents.
    def _serve_unknown(self, unseen, plan, notes, T, t0):
        T["total_s"] = round(time.time() - t0, 3)
        lang = guard.language_hint(plan["question"])
        if lang:
            notes = notes + [("The documents are in English. Try asking the question in English." if lang == "a non-English language"
                              else f"This looks like {lang}. The documents are in English, so try asking in English.")]
        yield {"type": "sources", "sources": [], "used": 0, "words": [], "match": "none", "key_details": [], "notes": notes, "route": "not_found"}
        words = ", ".join("\u201c" + w + "\u201d" for w in unseen[:3])
        yield {"type": "token", "t": out_of_scope(len(self.h.sources)) + "\n\n(Not found anywhere in the documents: " + words + ".)"}
        yield {"type": "done", "timings": dict(T), "route": "not_found", "grounded": False, "confidence": "n/a", "match": "none", "unseen": unseen}

    # "Related topics" suffix text for a not-found reply, from the engine's related() lookup.
    def _related_text(self, q):
        try:
            plan = self.h.planner.analyze(q)
            rel = self.h.engine.related(plan) if self.h.engine else []
        except Exception:
            rel = []
        return ("\n\nRelated topics in the documents: " + "; ".join(rel) + ".") if rel else ""

    # Stream a verified FAQ's answer, remapping its citation numbers to the deduplicated source list.
    def _serve_faq(self, f, m, notes, T, t0, t1):
        uniq, pos = [], {}
        for i, cid in enumerate(f["sources"], 1):
            if cid not in uniq:
                uniq.append(cid)
            pos[i] = uniq.index(cid) + 1
        remap = lambda s: re.sub(r"\[(\d+)\]", lambda mo: f"[{pos.get(int(mo.group(1)), int(mo.group(1)))}]", s)
        points = [remap(p["text"]) for p in f["points"]]
        hits = [self.h._hit(cid) for cid in uniq]
        for u, hit in enumerate(hits, 1):      # show the part of each passage that the answer actually cites
            cited = " ".join(p for p in points if f"[{u}]" in p)
            if cited:
                hit["text"] = self.h.index.focus(hit["text"], cited, 900)
        T["faq_ms"] = round((time.time() - t1) * 1000, 2)
        yield {"type": "sources", "sources": hits, "used": len(hits), "words": [], "match": "good", "key_details": [], "notes": notes,
               "route": "faq", "faq": {"id": f["id"], "question": f["question"], "sim": m["sim"]}}
        yield {"type": "token", "t": f["answer"] + "\n" + "\n".join("- " + p for p in points)}
        T["total_s"] = round(time.time() - t0, 4)
        yield {"type": "done", "route": "faq", "answer_source": "faq", "grounded": True, "confidence": "high", "match": "good", "cached": True,
               "faq_id": f["id"], "summary": f["answer"], "points": points, "timings": dict(T)}

    # Stream a previously-cached answer.
    def _serve_cache(self, e, notes, T, t0, t1, question=None):
        hits = [self.h._hit(cid) for cid in e["chunk_ids"]]
        T["cache_ms"] = round((time.time() - t1) * 1000, 2)
        yield {"type": "sources", "sources": hits, "used": e["used"], "words": e.get("words", []), "match": e["match"],
               "key_details": e.get("key_details", []), "notes": notes, "route": "cache"}
        if question and e["route"] == "llm":                  # a cached AI answer is shown with the document text beside it, like a fresh one
            exact = answering.compose_extractive(self.h.index, question, hits[:e["used"]])["text"]
            if exact:
                yield {"type": "exact", "text": exact}
        yield {"type": "token", "t": e["answer"]}
        T["total_s"] = round(time.time() - t0, 4)
        yield {"type": "done", "route": "cache", "answer_source": e["route"], "grounded": True, "confidence": e.get("confidence", "medium"),
               "match": e["match"], "cached": True, "timings": dict(T)}

    # ---- non-streaming convenience (evaluation, scripts)
