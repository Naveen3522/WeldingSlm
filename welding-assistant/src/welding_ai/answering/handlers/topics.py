"""Merged topic answers, one named section, whole-document overview, clarifying options, related topics."""
import re

from welding_ai.answering.evidence import (Answer, _SKIP_TITLE, _WEAK, clean, nice, sentences, title_of)
from welding_ai.retrieval import rag


class TopicHandlers:
    # Fallback answer: pull the best-covering sentences from the top-matching sections, merged into a topic overview.
    def topic(self, plan, need=0.6):
        terms = [t for t in plan["terms"] if t not in _WEAK] or plan["terms"]
        if not terms:
            return None
        res = self.search(terms, 8)
        need = max(need, 0.8)
        if not res or res[0][2] < need:
            return None
        top_cov = res[0][2]
        n_sec = {"brief": 3, "detail": 6}.get(plan.get("length"), 4)
        per = {"brief": 1, "detail": 3}.get(plan.get("length"), 2)
        ans = Answer("topic")
        seen_sents, used = [], 0
        for sc, sid, cov in res:
            if used >= n_sec:
                break
            if cov < max(need, top_cov * 0.8) or sc < 0.55 * res[0][0]:
                continue
            s = self.sec[sid]
            if _SKIP_TITLE.search(s["title"]) or (s["level"] == 0 and len(terms) > 1):
                continue
            cand = []
            for b in s["blocks"]:
                if b["t"] in ("p", "li"):
                    for score, st in self._sent_scores(terms, b["text"]):
                        if score >= 0.6 and 50 <= len(st) <= 420 and not st.endswith(":") and len(set(rag.tokens(st)) & set(terms)) >= min(2, len(terms)):
                            cand.append((score, st, b))
            cand.sort(key=lambda x: -x[0])
            picked = []
            for score, st, b in cand:
                if all(self._dedupe([st, o])[:2] == [st] or True for o in seen_sents) and self._dedupe(seen_sents + [st])[-1] == st:
                    picked.append((st, b))
                    seen_sents.append(st)
                if len(picked) >= per:
                    break
            if not picked:
                continue
            used += 1
            order = {id(b): i for i, b in enumerate(s["blocks"])}
            picked.sort(key=lambda x: order[id(x[1])])
            ans.text(f"**{title_of(s)}** · {nice(s['source'])}\n" + " ".join(p[0] for p in picked) if False else f"**{title_of(s)}** · *{nice(s['source'])}*")
            ans.text("\n".join("- " + p[0] for p in picked) if len(picked) > 1 else picked[0][0])
            for _, b in picked:
                self._cite_block(ans, s, b)
            tb = next((T for T in self.tables if T["sid"] == sid and self._cover(terms, T["meta"]) >= 0.5 and len(T["b"]["rows"]) <= 12), None) \
                if plan.get("length") == "detail" or plan.get("format") == "table" else None
            if tb:
                self._emit_table(ans, plan, s, tb["b"]["header"], tb["b"]["rows"], title_of(s), s["source"], tb["b"].get("chunk"))
        if not ans.blocks:
            return None
        return ans

    TITLE_WORD_IDF = 3.0        # "Dilution", "Pneumatics": rare enough to name a section on their own; "Safety", "Process" are not

    # Find the section whose own title matches the question's words, for an "explain <section>" question.
    def section_view(self, plan):
        """'Explain <section title> in <document>': the section itself - its text, lists and tables - in reading order."""
        qt = set(plan["terms"]) | set(rag.tokens(plan["question"]))       # also the words as typed (a title may say "Lifecycle" where the plan says "cycle")
        best = None
        for s in self.sec:
            tt = {t for t in rag.tokens(s["title"]) if not t.isdigit()}          # "3. The Reference-Curve Lifecycle": the number is not typed
            if s["level"] < 1 or not tt or not tt <= qt:
                continue
            if len(tt) < 2 and self.idf(next(iter(tt))) < self.TITLE_WORD_IDF:   # a one-word title counts only when that word is distinctive enough to name the section
                continue
            doc = set(rag.tokens(nice(s["source"])))
            extra = qt - tt - doc
            rank = (len(tt), len(qt & doc))                 # the longest title first; among equal titles, the document the question names
            if len(extra) <= 2 and (best is None or rank > best[0]):
                best = (rank, s)
        if not best:
            return None
        return self._render_section(plan, best[1])

    # Render a whole section (text, lists, tables) in reading order, favoring paragraphs relevant to the question.
    def _render_section(self, plan, s):
        """A whole section (text, lists, tables) in reading order."""
        ans = Answer("section")
        ans.text(f"**{title_of(s)}** · *{nice(s['source'])}*")
        n_p = {"brief": 1, "detail": 8}.get(plan.get("length"), 4)
        asked = set(plan["terms"])               # what the question is about; a later paragraph that speaks to it is worth more than the next one in order
        used, extra, lis, owed = 0, 0, [], False   # `owed`: the paragraph just shown introduces the next one ("the formula is written:"), so it must follow

        # Flush any pending run of list items as one bulleted text block.
        def flush():
            if lis:
                ans.text("\n".join("- " + clean(b["text"]) for b in lis))
                lis.clear()
        for b in s["blocks"]:
            if b["t"] == "li":
                lis.append(b)
                self._cite_block(ans, s, b)
                continue
            flush()
            wanted = b["t"] == "p" and (used < n_p or owed or (extra < n_p and asked & set(rag.tokens(b["text"]))))
            if wanted:                          # the section's opening, then any later paragraph that speaks to the question, each with what it introduces
                extra += used >= n_p and not owed
                used += 1
                ans.text(self._trim(sentences(b["text"]), 3, 600))
                self._cite_block(ans, s, b)
                owed = b["text"].rstrip().endswith(":")
            elif b["t"] == "table" and len(b["rows"]) <= 14:
                ans.table(b["header"], b["rows"], source=s["source"], chunk=b.get("chunk"))
                self._cite_block(ans, s, b)
        flush()
        return ans

    # Whole-document summary: the named document's own top-level sections, each with its opening sentences.
    def doc_overview(self, plan):
        terms = [t for t in plan["terms"] if t not in _WEAK and t not in {"document", "doc", "chapter", "file", "docx", "section", "whole", "entir", "qualification"} or t == "qualification"]
        terms = [t for t in terms if t not in {"document", "doc", "chapter", "file", "docx", "section", "whole", "entir"}]
        if not terms:
            return None
        best = None
        for src in self.by_src:
            dt = set(rag.tokens(src.rsplit(".", 1)[0].replace("_", " ")))
            c = self._cover(terms, dt)
            if best is None or c > best[0]:
                best = (c, src)
        if not best or best[0] < 0.99:
            return None
        src = best[1]
        secs = [s for s in self.by_src[src] if s["level"] >= 1 and any(b["t"] == "p" for b in s["blocks"])]
        if len(secs) < 2:
            return None
        ans = Answer("overview")
        n_sent = 1 if plan.get("length") == "brief" else 3 if plan.get("length") == "detail" else 2
        rows = []
        for s in secs:
            p = next((b for b in s["blocks"] if b["t"] == "p"), None)
            if p:
                rows.append([title_of(s), self._trim(sentences(p["text"]), n_sent, 380 if n_sent < 3 else 700)])
                self._cite_block(ans, s, p)
        ans.text(f"**{nice(src)}** — {len(rows)} sections")
        if plan.get("format") == "table":
            ans.table(["Section", "Summary"], rows, source=src)
        else:
            ans.text("\n".join(f"{i}. **{r[0]}** — {r[1]}" for i, r in enumerate(rows, 1)))
        return ans

    # Build disambiguation options for a vague question: distinct section titles about the words asked, one per topic.
    def clarify(self, plan):
        """Options for a vague question: section titles that are about the words asked, one per topic."""
        terms = plan["terms"]
        skip = re.compile(r"sources referenced|in this chapter|contents|glossary|references|quick reference|scope:|what .* means", re.I)
        opts, seen = [], set()
        ranked = []
        for sc, sid, cov in self.search(terms, 60):
            s = self.sec[sid]
            if s["level"] < 1 or skip.search(s["title"]):
                continue
            tc = self._cover(terms, set(rag.tokens(s["title"])))
            ranked.append((tc, sc, s))
        ranked.sort(key=lambda x: (-x[0], -x[1]))
        for tc, sc, s in ranked:
            key = title_of(s).lower()
            if key in seen or len({o["doc"] for o in opts}) >= 4 and s["source"] in {o["doc"] for o in opts}:
                continue
            seen.add(key)
            opts.append({"label": f"{title_of(s)} — {nice(s['source'])}", "query": f"Explain {title_of(s)} in {nice(s['source'])}", "doc": s["source"]})
            if len(opts) >= 4:
                break
        return [{"label": o["label"], "query": o["query"]} for o in opts]

    # A few related section titles for a "not found" reply's "Related topics" suffix.
    def related(self, plan, k=3):
        terms = [t for t in plan["terms"] if t not in _WEAK]
        out = []
        for sc, sid, cov in self.search(terms, 12):
            s = self.sec[sid]
            if cov >= 0.5 and s["level"] >= 1:
                out.append(f"{title_of(s)} ({nice(s['source'])})")
            if len(out) >= k:
                break
        return out
