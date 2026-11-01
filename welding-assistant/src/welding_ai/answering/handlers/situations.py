"""'The gun shows Ready - what does it mean': a value from a state / status table, plus what follows it."""
import re

from welding_ai.answering.evidence import (Answer, _SEQ, _WEAK, title_of)
from welding_ai.retrieval import rag


class SituationHandlers:
    # "The gun shows Ready - what does it mean": look up the named value in a state/status table and its meaning.
    def situation(self, plan):
        ql = plan["q"].lower()
        terms = [t for t in plan["terms"] if t not in _WEAK]
        best = None
        for T in self.tables:
            rows = T["b"]["rows"]
            for ri, r in enumerate(rows):
                if not (re.search(r"state|status|stage|mode|phase|code|condition|level", T["b"]["header"][0], re.I) or _SEQ.search(T["sec"]["path"] + " " + T["lead"])):
                    break
                k = r[0].strip().lower()
                if len(k) < 3 or len(k) > 40 or not re.search(r"(?<![\w-])" + re.escape(k) + r"(?![\w-])", ql):
                    continue
                kt = set(rag.tokens(k))
                rest = [t for t in terms if t not in kt]
                cov = self._cover(rest, T["meta"] | set(rag.tokens(T["lead"]))) if rest else 0.0
                if rest and cov >= 0.5:
                    sc = cov + 0.1 * len(k)
                    if best is None or sc > best[0]:
                        best = (sc, T, ri)
        if not best:
            return None
        _, T, ri = best
        b, s = T["b"], T["sec"]
        hdr, row = b["header"], b["rows"][ri]
        ans = Answer("situation")

        # Render a table row as "**key** — field: value; ...".
        def describe(r):
            if len(r) == 2:
                return f"**{r[0]}** — {r[1]}"
            return f"**{r[0]}** — " + "; ".join(f"{hdr[c]}: {r[c]}" for c in range(1, len(r)) if r[c])
        ans.text(describe(row))
        if _SEQ.search(s["path"] + " " + T["lead"]) and ri + 1 < len(b["rows"]) and plan.get("length") != "brief":
            ans.text("**Next in the sequence:** " + describe(b["rows"][ri + 1]))
        ans.text(f"*From the table “{title_of(s)}”.*")
        self._cite_block(ans, s, b)
        return ans
