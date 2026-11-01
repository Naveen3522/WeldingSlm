"""A table row the question is about, shown with every column that answers what is asked ("what does it mean and what do I do")."""
import re

from welding_ai.answering.evidence import Answer, title_of
from welding_ai.retrieval import rag

# what the question asks -> column labels that hold it
ATTR_HEADERS = {"meaning": r"mean|interpret|indicat|signif", "action": r"action|fix|remed|correct|respons|resolv|solution|counter|prevent",
                "cause": r"cause|reason|root|source|mechanism", "detect": r"detect|check|inspect|identif|observ|evidence"}
MAX_COLS, MAX_ROWS = 5, 4
MIN_KEY_COVER, MIN_KEY_HITS = 0.4, 2


class RowHandlers:
    # Rows of a table whose first cell matches the question's subject, with all their columns (or the asked ones when the table is wide).
    def attribute_rows(self, plan):
        """'a material tensile strength breaks, what does it mean and what action' -> the rows about tensile breaks from the failure table,
        with Meaning AND Action (not only the column the question happened to name)."""
        attrs = plan.get("attrs") or []
        subject = [t for t in plan.get("subject", []) if not t.isdigit()]
        if len(attrs) < 2 or len(subject) < 2:          # one asked attribute is a cell / situation lookup, which the older handlers already answer exactly
            return None
        best, seen = None, []
        for T in self.tables:
            hdr = T["b"]["header"]
            if len(hdr) < 3:
                continue
            if not any(re.search(ATTR_HEADERS[a], h, re.I) for a in attrs for h in hdr[1:]):
                continue
            ctx = self._cover(subject, T["meta"])
            scored = []
            for ri, keys in enumerate(T["keys"]):
                hits = [t for t in subject if t in keys]
                cover = self._cover(subject, keys)
                if len(hits) < MIN_KEY_HITS or cover < MIN_KEY_COVER:
                    continue
                row = set(rag.tokens(" ".join(T["b"]["rows"][ri])))
                scored.append((cover + 0.3 * self._cover(subject, row) + 0.3 * ctx, ri))
            if scored:
                scored.sort(reverse=True)
                seen.append((scored[0][0], T["sec"]["source"]))
                if best is None or scored[0][0] > best[0]:
                    best = (scored[0][0], T, scored)
        if not best:
            return None
        top, T, scored = best
        if len({src for sc, src in seen if sc >= 0.75 * top}) > 1:
            return None                     # the same subject is described in several documents: the cross-document handlers compare them
        ris = sorted(ri for sc, ri in scored[:MAX_ROWS] if sc >= 0.75 * top)
        hdr, rows = T["b"]["header"], T["b"]["rows"]
        asked = [c for c in range(1, len(hdr)) if any(re.search(ATTR_HEADERS[a], hdr[c], re.I) for a in attrs)]
        cols = list(range(len(hdr))) if len(hdr) <= MAX_COLS else [0] + asked
        ans = Answer("rowcol")
        if "meaning" in attrs and not any(re.search(ATTR_HEADERS["meaning"], hdr[c], re.I) for c in range(1, len(hdr))):
            hits = [t for ri in ris for t in subject if t in T["keys"][ri]]          # the table has no Meaning column: the document's own definition of the subject answers it
            d = self.definition(max(hits, key=self.idf), plan) if hits else None
            if d:
                ans.text(d["text"])
                self._cite_block(ans, d["sec"], d["b"])
        header = [hdr[c] for c in cols]
        body = [[rows[ri][c] for c in cols] for ri in ris]
        if plan.get("format") in ("list", "steps"):
            ans.text(f"**{title_of(T['sec'])}**")
            ans.text("\n".join(f"**{r[0]}**" + "".join(f"\n- **{header[i]}:** {r[i]}" for i in range(1, len(r)) if r[i]) for r in body))
        else:
            ans.table(header, body, title=title_of(T["sec"]), source=T["sec"]["source"], chunk=T["b"].get("chunk"))
        self._cite_block(ans, T["sec"], T["b"])
        return ans
