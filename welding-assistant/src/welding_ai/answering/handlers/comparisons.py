"""Side-by-side comparison of two things, and whether documents disagree."""
import re

from welding_ai.answering.evidence import (Answer, _WEAK, clean, nice, title_of)
from welding_ai.retrieval import rag
from welding_ai.understanding import planner


class ComparisonHandlers:
    # Split "X vs Y" into its two sides, then answer with a shared comparison table or side-by-side definitions.
    def compare(self, plan):
        core = re.sub(r"\b(compare|comparison|compared|difference between|differences between|difference|distinguish|differ from|differs from)\b", " ", plan["core"], flags=re.I)
        halves = [h.strip(" ?.") for h in re.split(r"\b(?:and|vs\.?|versus|with|from|between|against|to)\b", core, flags=re.I) if h.strip(" ?.")]
        ents = [h for h in halves if planner.content_terms(h)][:2]
        if len(ents) < 2:
            return None
        et = [planner.content_terms(e) for e in ents]
        best = None
        for T in self.tables:
            hdr = [set(rag.tokens(h)) for h in T["b"]["header"][1:]]
            if len(hdr) < 2:
                continue
            found = [any(all(t in h for t in e) for h in hdr) for e in et]
            body = [all(t in T["all"] for t in e) for e in et]
            sc = 3 * sum(found) + sum(body) + (1 if all(found) else 0)
            if best is None or sc > best[0]:
                best = (sc, T)
        ans = Answer("compare")
        if best and best[0] >= 6:
            T = best[1]
            ans.text(f"**{title_of(T['sec'])}**")
            self._emit_table(ans, plan, T["sec"], T["b"]["header"], T["b"]["rows"], "", T["sec"]["source"], T["b"].get("chunk"))
            self._cite_block(ans, T["sec"], T["b"])
            return ans
        rows = []
        for e, terms in zip(ents, et):
            d = self.definition(" ".join(terms), dict(plan, intent="define", length="brief"))
            if d:
                rows.append([e, d["text"]])
                self._cite_block(ans, d["sec"], d["b"])
            else:
                ans.missing.append(e)
        if len(rows) >= 2:
            ans.table(["Term", "What the documents say"], rows)
            return ans
        return None

    # Compare how different documents state the same fact, flagging whether their stated values agree.
    def conflict(self, plan):
        terms = [t for t in plan["terms"] if t not in _WEAK and t not in {"give", "differ", "same", "agre", "consist", "state", "say", "differ"}]
        if not terms:
            return None
        rows, vals, seen_docs = [], [], set()
        for sc, sid, cov in self.search(terms, 14):
            s = self.sec[sid]
            if cov < 0.6:
                continue
            best = None
            for b in s["blocks"]:
                if b["t"] in ("p", "li"):
                    for score, st in self._sent_scores(terms, b["text"]):
                        if score >= 0.5 and re.search(r"\d", st) and (best is None or score > best[0]):
                            best = (score, st, b)
            if best and (s["source"], s["title"]) not in seen_docs:
                seen_docs.add((s["source"], s["title"]))
                rows.append([nice(s["source"]), title_of(s), best[1]])
                vals.append(set(re.findall(r"\d+(?:\.\d+)?\s?(?:kN|N|kA|A|ms|cycles?|mm|MPa|kg|bar|psi|l/min|%|°C|HV)\b", best[1])))
                self._cite_block(None or Answer("x"), s, best[2]) if False else None
        if len({r[0] for r in rows}) < 2:
            return None
        ans = Answer("conflict")
        nz = [v for v in vals if v]
        if len(nz) >= 2:
            ans.text("**The documents agree on the values found:** " + ", ".join(sorted(nz[0])) if all(v == nz[0] for v in nz) else "**The values differ between documents** — see the conditions in each statement.")
        ans.table(["Document", "Section", "Statement"], rows[:8])
        for sc, sid, cov in self.search(terms, 14)[:8]:
            s = self.sec[sid]
            for b in s["blocks"]:
                if b["t"] in ("p", "li") and any(r[2] == st for r in rows for st in [b["text"]] if False):
                    pass
        # cite by matching the statement text
        for r in rows[:8]:
            for s in self.by_src[next(k for k in self.by_src if nice(k) == r[0])]:
                for b in s["blocks"]:
                    if b["t"] in ("p", "li") and r[2] in clean(b["text"]):
                        self._cite_block(ans, s, b)
        return ans
