"""Numbered procedures and symptom -> cause -> check diagnosis."""
import re

from welding_ai.answering.evidence import (Answer, _DIAG_ACT, _DIAG_HDR, _WEAK, clean, sentences, title_of)
from welding_ai.retrieval import rag


class ProcedureHandlers:
    # Find candidate numbered-list runs and "Step | Action" tables, scored by how well their title/lead-in/body cover the terms.
    def _procedures(self, terms):
        out = []
        for s in self.sec:
            if s["id"] in self.nav_sec:          # a contents list points to the headings, it is not a procedure
                continue
            blocks = s["blocks"]
            # list-item runs
            i = 0
            while i < len(blocks):
                if blocks[i]["t"] == "li":
                    j = i
                    while j < len(blocks) and blocks[j]["t"] == "li":
                        j += 1
                    if j - i >= 3:
                        lead = next((blocks[k]["text"] for k in range(i - 1, -1, -1) if blocks[k]["t"] == "p"), "")
                        run = blocks[i:j]
                        tk = set(rag.tokens(s["path"] + " " + s["source"].rsplit(".", 1)[0].replace("_", " ")))          # the section title and the document it belongs to
                        body = set().union(*[set(rag.tokens(b["text"])) for b in run]) | set(rag.tokens(lead))
                        c_title = self._cover(terms, tk | set(rag.tokens(lead)))          # what the list is about: its section title and the sentence that introduces it
                        c_all = self._cover(terms, tk | body)
                        out.append((c_title * 2 + c_all, c_all, s, run, lead, c_title))
                    i = j
                else:
                    i += 1
            for bi, b in enumerate(blocks):                 # tables that are procedures ("Step | Action | ...")
                if b["t"] == "table" and re.search(r"\bstep\b", b["header"][0], re.I):
                    tk = set(rag.tokens(s["path"] + " " + s["source"].rsplit(".", 1)[0].replace("_", " ")))          # the section title and the document it belongs to
                    body = set(rag.tokens(" ".join(" ".join(r) for r in b["rows"])))
                    out.append((self._cover(terms, tk) * 2 + self._cover(terms, tk | body), self._cover(terms, tk | body), s, b, "", self._cover(terms, tk)))
        out.sort(key=lambda x: -x[0])
        return out

    # How much of the question the best-matching list's own title/lead-in/document name covers.
    def list_topic_cover(self, plan):
        """How much of the question the best list's own title, lead-in and document name cover (0 when there is no list)."""
        terms = [t for t in plan["terms"] if t not in _WEAK and t not in {"step", "procedur", "process", "instruct", "guid", "method"}]
        cands = [c for c in self._procedures(terms)[:5] if not isinstance(c[3], dict)] if terms else []
        return cands[0][5] if cands else 0.0

    LEAD_COVER, LEAD_MIN_TERMS = 0.75, 3

    # Answer when the question restates the sentence that introduces a list; the list itself is the answer.
    def lead_list(self, plan):
        """The question restates the sentence that introduces a list ("the conditions that make secondary shock dangerous:"): the answer is that
        list. Only a strong match counts: the introducing sentence must hold nearly all the question's weighted terms."""
        terms = [t for t in plan["terms"] if t not in _WEAK and t not in {"step", "procedur", "process", "instruct", "guid", "method"}]
        if len(terms) < self.LEAD_MIN_TERMS:
            return None
        for _, _, _, run, lead, _t in self._procedures(terms)[:3]:
            if isinstance(run, dict) or not lead:
                continue
            intro = next((x for x in reversed(sentences(lead)) if x.endswith(":")), "")
            if intro and self._cover(terms, set(rag.tokens(intro))) >= self.LEAD_COVER:
                return self.procedure(dict(plan, format=plan.get("format") or "list"), need=self.LEAD_COVER, need_title=0.0)
        return None

    # Render the best-matching numbered-list or step-table candidate as a procedure/list/steps answer.
    def procedure(self, plan, need=0.7, need_title=0.0):
        terms = [t for t in plan["terms"] if t not in _WEAK and t not in {"step", "procedur", "process", "instruct", "guid", "method"}]
        if not terms:
            return None
        cands = self._procedures(terms)
        cands = [c for c in cands if c[5] >= need_title]
        if not cands or cands[0][1] < need:
            return None
        _, cov, s, run, lead, _t = cands[0]
        ans = Answer("procedure")
        title = title_of(s)
        ans.text(f"**{title}**")
        if isinstance(run, dict):                            # a step table
            self._emit_table(ans, plan, s, run["header"], run["rows"], title, s["source"], run.get("chunk"))
            self._cite_block(ans, s, run)
            return ans
        items = [clean(b["text"]) for b in run]
        intro = ""
        if lead:
            ls = sentences(lead)
            intro = next((x for x in reversed(ls) if x.endswith(":") or re.search(r"\b(order|sequence|steps?|procedure|following|walks?)\b", x, re.I)), "")
        if intro and len(intro) < 320:
            ans.text(intro)
        if plan.get("format") == "table":
            hd = ("Step", "Action", "Detail") if plan["intent"] in ("procedure", "diagnose") else ("#", "Item", "Detail")
            rows = []
            for i, it in enumerate(items, 1):
                m = re.match(r"^([^.!?]{3,60}?)(?::| — | – | - )\s+(.+)$", it)      # a real label, not a colon in the middle of a sentence
                rows.append([str(i)] + ([m.group(1), m.group(2)] if m else [it, ""]))
            if all(not r[2] for r in rows):
                ans.table([hd[0], hd[1]], [[r[0], r[1]] for r in rows], title=title, source=s["source"], chunk=run[0].get("chunk"))
            else:
                ans.table(list(hd), rows, title=title, source=s["source"], chunk=run[0].get("chunk"))
        elif plan.get("format") == "list":
            ans.text("\n".join("- " + it for it in items))
        else:
            ans.text("\n".join(f"{i}. {it}" for i, it in enumerate(items, 1)))
        for b in run:
            self._cite_block(ans, s, b)
        return ans

    # Combine a matching procedure (if any) with cause/check rows from a fault/defect table for the question's terms.
    def diagnose(self, plan):
        terms = [t for t in (plan.get("subject") or plan["terms"]) if t not in _WEAK]          # "stop" in "how do I stop porosity" asks for an action, it is not the defect
        if not terms:
            return None
        ans = Answer("diagnose")
        proc = self.procedure(dict(plan, terms=terms, format=plan.get("format")), need=0.7, need_title=0.6)
        if proc:
            ans = proc
            ans.kind = "diagnose"
        keyed = [t for t in terms if any(t in k for T in self.tables if len(T["b"]["header"]) >= 3 for k in T["keys"])]
        if keyed:
            kt = max(keyed, key=self.idf)
            mr = self.merged_rows([kt], [t for t in terms if t != kt], limit=6)
            if mr and (len(mr[2]) >= 2 or not proc):
                if not proc:
                    ans.text(f"**{kt[:1].upper() + kt[1:]}** — causes and checks from the documents")
                ans.table(mr[0], mr[1])
                for p in mr[2]:
                    self._cite_block(ans, p[1]["sec"], p[1]["b"])
                return ans
        best = None
        tot = sum(self.idf(t) for t in terms) or 1.0
        for T in self.tables:
            hdr = T["b"]["header"]
            if not (any(_DIAG_HDR.search(h) for h in hdr) and any(_DIAG_ACT.search(h) for h in hdr)) and not (len(hdr) == 2 and _DIAG_HDR.search(hdr[0])):
                continue
            rows = []
            tcov = self._cover(terms, set(rag.tokens(T["sec"]["path"])))
            for ri, r in enumerate(T["b"]["rows"]):
                rk = set(rag.tokens(r[0]))
                rt = rk | set(rag.tokens(" ".join(r)))
                m = [t for t in terms if t in rt or t in T["meta"]]
                w = sum(self.idf(t) for t in m) / tot
                key_hit = sum(1 for t in terms if t in rk)
                if w >= 0.5 and (key_hit >= 1 or len(m) >= 2):
                    rows.append((w + 0.3 * key_hit + 1.5 * tcov, ri))
            if rows:
                rows.sort(reverse=True)
                sc = rows[0][0] + 0.1 * len(rows)
                if best is None or sc > best[0]:
                    best = (sc, T, sorted(ri for _, ri in rows[:12 if tcov >= 0.6 else 4]))
        if best and best[0] >= (0.7 if not proc else 2.5):
            _, T, ris = best
            if not proc:
                ans.text(f"**{title_of(T['sec'])}**")
            ans.table(T["b"]["header"], [T["b"]["rows"][ri] for ri in ris], title="" if not proc else "Related causes and checks", source=T["sec"]["source"], chunk=T["b"].get("chunk"))
            self._cite_block(ans, T["sec"], T["b"])
            if plan.get("format") in ("list", "steps"):
                blk = ans.blocks.pop()
                self._emit_table(ans, plan, T["sec"], blk["header"], blk["rows"], blk["title"], blk["source"], blk["chunk"])
        if not ans.blocks:
            return None
        return ans
