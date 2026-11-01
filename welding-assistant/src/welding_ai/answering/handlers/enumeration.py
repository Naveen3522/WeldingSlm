"""Whole-table and list answers (types, layers, states ...), a named row with the columns asked for, layout conversion."""
import re
from collections import Counter

from welding_ai.answering.evidence import (Answer, nice, title_of)
from welding_ai.retrieval import rag


class EnumerationHandlers:
    # Answer with the matched table (or the row the question names), falling back to a bulleted list.
    def enumerate_(self, plan, tindex):
        m = tindex.match(plan["core"]) if tindex else None
        if m and self.list_topic_cover(plan) >= m["meta_cover"] + self.LIST_OVER_TABLE:
            m = None                       # a list whose own title names more of the question than the table's does
        if m:
            t = m["table"]
            ans = Answer("table")
            src = next((T for T in self.tables if T["sec"]["source"] == t["source"] and T["b"]["header"] == t["header"] and T["b"]["rows"] == t["rows"]), None)
            rows, note = t["rows"], t.get("after", "")
            if m.get("rows_hit"):                 # the question names one item of the table: show that row, say how many there are
                rows, note = [t["rows"][ri] for ri in m["rows_hit"]], f"{len(m['rows_hit'])} of {len(t['rows'])} rows shown, the ones that match the question."
            self._emit_table(ans, plan, src["sec"] if src else None, t["header"], rows, t["title"], t["source"], src["b"].get("chunk") if src else (t.get("chunk_ids") or [None])[0], note)
            if src:
                self._cite_block(ans, src["sec"], src["b"])
            return ans
        return self.list_(plan)

    # The table(s) the question names by their own title.
    def table_by_title(self, plan, tindex):
        """The table the question names by its title (all documents that carry a table with that title)."""
        ms = tindex.match_title(plan["core"]) if tindex else []
        if not ms:
            return None
        ans = Answer("table")
        for m in ms[:4]:
            t = m["table"]
            src = next((T for T in self.tables if T["sec"]["source"] == t["source"] and T["b"]["header"] == t["header"] and T["b"]["rows"] == t["rows"]), None)
            title = re.sub(r"^\d+\.\s*", "", t["title"]) + (f" · {nice(t['source'])}" if len(ms) > 1 else "")
            self._emit_table(ans, plan, src["sec"] if src else None, t["header"], t["rows"], title, t["source"], src["b"].get("chunk") if src else (t.get("chunk_ids") or [None])[0], t.get("after", "") if len(ms) == 1 else "")
            if src:
                self._cite_block(ans, src["sec"], src["b"])
        return ans

    _CELL_Q = re.compile(r"^(?:what\s+(?:is|are)\s+|which\s+|meaning\s+)?(?:the\s+)?(.{2,60}?)\s+for\s+(.{2,90}?)\s*\??$", re.I)

    # Loose word match (prefix-tolerant) for matching column labels against a question's words.
    @staticmethod
    def _word_match(a, b):
        return a == b or (len(a) >= 4 and len(b) >= 4 and (a.startswith(b[:4]) or b.startswith(a[:4])) and (a.startswith(b) or b.startswith(a) or a[:5] == b[:5]))

    # "what is the <column> for <row label>": find the one cell where that row and column meet.
    def cell_lookup(self, plan):
        """"what is the <column> for <row label>": the one cell where that row and that column meet, from the document's own table."""
        if len(plan["parts"]) != 1:
            return None
        m = self._CELL_Q.match(plan["core"].strip())
        if not m:
            return None
        xw = [w for w in re.findall(r"[a-z]+", m.group(1).lower()) if w not in {"the", "a", "an", "of", "is", "are"}]
        y = set(rag.tokens(m.group(2)))
        if not xw or not y:
            return None
        best = None
        for T in self.tables:
            hdr = T["b"]["header"]
            if len(hdr) < 2:
                continue
            for ri, keys in enumerate(T["keys"]):
                if not keys or not (keys == y or (keys <= y and len(keys) >= 2 and len(y - keys) <= 1)):
                    continue
                for c in range(1, len(hdr)):
                    hw = re.findall(r"[a-z]+", hdr[c].lower())
                    hit = sum(1 for w in xw if any(self._word_match(w, h) for h in hw))
                    if hit < max(1, round(0.6 * len(xw))) or not T["b"]["rows"][ri][c].strip():
                        continue
                    sc = (3 if keys == y else 1) + 2 * hit / len(xw) - 0.1 * abs(len(hw) - len(xw))
                    if best is None or sc > best[0]:
                        best = (sc, T, ri, c)
        if not best:
            return None
        _, T, ri, c = best
        row = T["b"]["rows"][ri]
        ans = Answer("cell")
        ans.text(f"**{row[0]}** — *{T['b']['header'][c]}*\n{row[c]}")
        self._cite_block(ans, T["sec"], T["b"])
        return ans

    LIST_OVER_TABLE = 0.15
    LIST_TOPIC_MIN = 0.35        # a whole list answers only if the question is about what the list is about (title + lead-in), not just words found somewhere in its items

    # A bulleted list the documents introduce with a lead-in sentence, chosen by title + lead-in + items
    # (delegates to the procedure handler's list-finding logic with looser thresholds).
    def list_(self, plan):
        """A bulleted list the documents introduce with a lead-in sentence, chosen by title + lead-in + items."""
        p2 = dict(plan, format=plan.get("format") or "list")
        return self.procedure(p2, need=0.7, need_title=self.LIST_TOPIC_MIN)

    # Render a table as a table block, or as a list/steps block if the question asked for that layout.
    def _emit_table(self, ans, plan, sec, header, rows, title, source, chunk, note=""):
        fmt = plan.get("format")
        if fmt in ("list", "steps"):
            lines = []
            for i, r in enumerate(rows, 1):
                rest = [f"{header[c]}: {r[c]}" for c in range(1, len(r)) if r[c]] if len(header) > 2 else [r[1]] if len(r) > 1 else []
                body = f"**{r[0]}**" + (" — " + "; ".join(rest) if rest else "")
                lines.append((f"{i}. " if fmt == "steps" else "- ") + body)
            ans.text(f"**{re.sub(r'^\d+\.\s*', '', title)}**")
            ans.text("\n".join(lines))
        else:
            ans.table(header, rows, title=re.sub(r"^\d+\.\s*", "", title), source=source, chunk=chunk, note=note)

    # Find a table row the question names and the specific columns whose labels the question asks about.
    def row_columns(self, plan):
        """A row named in the question and the columns whose labels the question uses: 'why parent materials matter and
        what fields are used' -> row 'Parent materials', columns 'Suggested Fields' and 'Why It Matters'."""
        qt = set(plan["terms"])
        strict = not (plan.get("format") or plan["intent"] == "explain")
        if not hasattr(self, "_keycount"):
            self._keycount = Counter(frozenset(k) for T in self.tables for k in T["keys"] if k)
        best = None
        for T in self.tables:
            hdr = T["b"]["header"]
            if len(hdr) < 2:
                continue
            htoks = [set(rag.tokens(h)) for h in hdr]
            if strict:                       # plain question: only a column whose whole (two-word or longer) label is in the question
                cols = [c for c in range(1, len(hdr)) if len(htoks[c]) >= 2 and htoks[c] <= qt]
            else:
                cols = [c for c in range(1, len(hdr)) if (htoks[c] & qt) - {"weld", "use"}]
            if not cols:
                continue
            rows = [ri for ri, k in enumerate(T["keys"]) if k and k <= qt and self._keycount[frozenset(k)] <= 3]
            if not rows:
                continue
            keyw = sum(self.idf(t) for ri in rows for t in T["keys"][ri])
            sc = keyw + 2 * len(cols) + 2 * self._cover([t for t in qt if not any(t in T["keys"][ri] for ri in rows)], T["meta"])
            if best is None or sc > best[0]:
                best = (sc, T, rows, cols)
        if not best or best[0] < 6:
            return None
        _, T, rows, cols = best
        hdr = T["b"]["header"]
        ans = Answer("rowcol")
        header = [hdr[0]] + [hdr[c] for c in cols]
        body = [[T["b"]["rows"][ri][0]] + [T["b"]["rows"][ri][c] for c in cols] for ri in rows]
        if plan.get("format") in ("list", "steps"):
            ans.text(f"**{title_of(T['sec'])}**")
            ans.text("\n".join(f"**{r[0]}**" + "".join(f"\n- **{header[i]}:** {r[i]}" for i in range(1, len(r))) for r in body))
        else:
            ans.table(header, body, title=title_of(T["sec"]), source=T["sec"]["source"], chunk=T["b"].get("chunk"))
        self._cite_block(ans, T["sec"], T["b"])
        return ans
