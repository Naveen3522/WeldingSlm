"""What is X / what does X contain and lack: glossary and definition lookup, per-part table-row lookup, rows merged across documents."""
import re

from welding_ai.answering.evidence import (Answer, nice, sentences, title_of)
from welding_ai.retrieval import rag


class DefinitionHandlers:
    # Find the document's own definition of an entity: a glossary table row first, else the strongest defining sentence.
    def definition(self, entity, plan):
        et = [t for t in rag.tokens(entity) if t]
        if not et:
            return None
        bm = re.search(r"([\w\- ]{2,60})\(\s*" + re.escape(entity) + r"\s*\)", plan.get("core", ""))          # "resistance spot welding (RSW)": the glossary lists the full term
        full = set(et) | set(rag.tokens(bm.group(1))) if bm else None
        for loose in (False, True):                          # a glossary entry is the document's own definition; the entity as asked, then the full term written before its bracket
            if loose and not full:
                break
            found = None
            for T in self.tables:
                if not (len(T["b"]["header"]) == 2 and re.search(r"term|name|acronym|abbrev|word", T["b"]["header"][0], re.I) and re.search(r"defin|mean|descri", T["b"]["header"][1], re.I)):
                    continue
                for ri, keys in enumerate(T["keys"]):
                    if keys == (full if loose else set(et)):
                        if found is None or len(keys) < found[0]:
                            found = (len(keys), T, ri)
                if found and not loose:
                    break
            if found:
                _, T, ri = found
                txt = self._trim(sentences(T["b"]["rows"][ri][1]), 2 if plan.get("length") == "brief" else 4, 700)
                return {"text": txt, "sec": T["sec"], "b": T["b"], "v": 9}
        best = None
        for sc, sid, cov in self.search(et, 8):
            s = self.sec[sid]
            if cov < 0.99:
                continue
            tt = {t for t in rag.tokens(s["title"]) if not t.isdigit() and t != "chapter"}
            title_has = tt == set(et)
            defsec = bool(re.search(r"defin|what is|what are|overview|introduc|terminolog", s["title"], re.I))
            pos = 0
            for b in s["blocks"]:
                if b["t"] != "p" or len(b["text"]) < 60:
                    continue
                pos += 1
                if b["text"].lstrip().startswith(("•", "-", "*", "Chapter ")):
                    continue
                tk = set(rag.tokens(b["text"]))
                if not (all(t in tk for t in et) or (title_has and pos == 1)):
                    continue
                head = b["text"][:260]
                words = r"\W+".join(re.escape(w) for w in entity.lower().split())
                strong = bool(re.search(r"^[^.]{0,70}?" + words + r"[^.]{0,50}?\b(is|are|refers to|means|describes|denotes)\b", head.lower()))
                v = (3 if title_has else 0) + (2 if defsec else 0) + (2 if pos == 1 and (title_has or defsec) else 1 if pos == 2 and title_has else 0)                     + (4 if strong else 0) + (0.5 if re.search(r"\b(is|are|refers to|means)\b", head) else 0) + min(b["text"].count(entity), 3) * 0.1
                if best is None or v > best[0]:
                    best = (v, s, b)
        if not best or best[0] < 5.0:
            return None
        v, s, b = best
        sents = sentences(b["text"])
        if plan.get("length") == "brief":
            txt = self._trim(sents, 2, 320)
        elif plan.get("length") == "detail":
            txt = self._trim(sents, 6, 1100)
        else:
            txt = self._trim(sents, 3, 560)
        return {"text": txt, "sec": s, "b": b, "v": v}

    _COLS = [("How it is detected", r"detect|indicat|observ|where|discriminat"), ("Likely cause", r"cause|mechanism|root|source|reason"),
             ("Corrective action", r"action|remed|correct|fix|response|first|check")]

    # The rows keyed by `key_terms` from every document's own matching table, one row per document, columns aligned.
    def merged_rows(self, key_terms, ctx_terms=(), limit=6, need_ctx=0.05, extra=4):
        """The rows keyed by `key_terms` (e.g. 'Porosity', 'Expulsion') from every document's own defect / fault table,
        one row per document, with matching columns aligned (detection / cause / corrective action)."""
        found = {}
        for T in self.tables:
            hdr = T["b"]["header"]
            if len(hdr) < 3:
                continue
            for ri, keys in enumerate(T["keys"]):
                if keys and all(t in keys for t in key_terms) and len(keys) <= len(key_terms) + extra:
                    row = T["b"]["rows"][ri]
                    cols = []
                    for label, rx in self._COLS:
                        ci = next((c for c in range(1, len(hdr)) if re.search(rx, hdr[c], re.I) and row[c]), None)
                        cols.append(row[ci] if ci is not None else "")
                    if not any(cols):
                        continue
                    ctx = self._cover(ctx_terms, T["meta"] | set(rag.tokens(" ".join(row)))) if ctx_terms else 1.0
                    if ctx < need_ctx:
                        continue
                    k = (T["sec"]["source"], row[0].lower())
                    sc = ctx + 0.05 * sum(1 for c in cols if c)
                    if k not in found or sc > found[k][0]:
                        found[k] = (sc, T, ri, cols)
        best_per_doc = {}
        for k, v in found.items():
            if k[0] not in best_per_doc or v[0] > best_per_doc[k[0]][0]:
                best_per_doc[k[0]] = v
        picks = sorted(best_per_doc.values(), key=lambda x: -x[0])[:limit]
        if not picks:
            return None
        use = [i for i in range(3) if sum(1 for p in picks if p[3][i]) * 2 >= len(picks)]
        if not use:
            return None
        picks.sort(key=lambda x: x[1]["sec"]["source"])
        header = ["Document", "Item"] + [self._COLS[i][0] for i in use]
        rows = [[nice(p[1]["sec"]["source"]), p[1]["b"]["rows"][p[2]][0]] + [p[3][i] for i in use] for p in picks]
        return header, rows, picks

    # A table row whose key is the entity, shown as field/value pairs.
    def entity_row(self, et):
        """A table row whose key is the entity ('Porosity' in a defect catalogue), shown as field / value pairs."""
        best = None
        for T in self.tables:
            hdr = T["b"]["header"]
            if len(hdr) < 3:
                continue
            for ri, keys in enumerate(T["keys"]):
                if keys and all(t in keys for t in et) and len(keys) <= len(et) + 2:
                    sc = len(hdr) + sum(2 for h in hdr[1:] if re.search(r"defin|descri|mean|what|cause|detect|action|scope|covers", h, re.I))
                    if best is None or sc > best[0]:
                        best = (sc, T, ri)
        return best

    # Best (table, [row indexes]) whose row labels match the part's words, from a table that's about the entity.
    def _row_lookup(self, entity_toks, part_terms):
        """Best (table, [row indexes]) whose row labels / column labels match the part's words and that is about the entity."""
        best = None
        for T in self.tables:
            b = T["b"]
            if entity_toks and not all(t in T["all"] for t in entity_toks):
                continue
            about = (3.0 if entity_toks and all(t in T["meta"] for t in entity_toks) else 0.0)
            hits, weight = [], 0.0
            for ri, keys in enumerate(T["keys"]):
                m = [t for t in part_terms if t in keys]
                if m:
                    hits.append(ri)
                    weight += sum(self.idf(t) for t in m)
            if hits:
                sc = about + weight
                if best is None or sc > best[0]:
                    best = (sc, T, hits)
        if best and best[0] >= 3.0:
            return best
        return None

    # A row whose key is the entity and a column whose label matches the part (e.g. a standard row, "Scope" column).
    def _col_lookup(self, entity_toks, part_terms):
        """A row whose key is the entity and a column whose label matches the part (e.g. standard row, 'Scope' column)."""
        best = None
        for T in self.tables:
            hdr = [set(rag.tokens(h)) for h in T["b"]["header"]]
            cols = [ci for ci, h in enumerate(hdr) if ci > 0 and any(t in h for t in part_terms)]
            if not cols:
                continue
            for ri, keys in enumerate(T["keys"]):
                if entity_toks and all(t in keys for t in entity_toks):
                    sc = 5 + sum(self.idf(t) for t in part_terms if any(t in hdr[c] for c in cols))
                    if best is None or sc > best[0]:
                        best = (sc, T, ri, cols)
        return best

    # Which columns of a table to show: only the ones naming the entity, if that's a strict subset, else all of them.
    @staticmethod
    def _project(T, entity_toks):
        hdr = T["b"]["header"]
        cols = [ci for ci, h in enumerate(hdr) if ci > 0 and entity_toks and all(t in set(rag.tokens(h)) for t in entity_toks)]
        return [0] + cols if cols and len(cols) < len(hdr) - 1 else list(range(len(hdr)))

    # Build a definition (if asked) plus a table-row answer for each of the question's own sub-parts.
    def entity_parts(self, plan):
        """'what is WPS, what does it contain and what does it lack' -> definition + the rows that answer each part."""
        entity = plan["entity"]
        et = rag.tokens(entity)
        ans = Answer("entity_parts")
        if re.match(r"^(ISO|AWS|ASME|ASTM|IPC|EN|DIN|IEC|RWMA)\s?[A-Z]?\d", entity, re.I):
            er = self.entity_row(et)               # a standard: its own row of the standards table answers "what is it / what does it cover"
            if er:
                _, T, ri = er
                hdr, row = T["b"]["header"], T["b"]["rows"][ri]
                ans.table([hdr[0] or "Standard", row[0]], [[hdr[c], row[c]] for c in range(1, len(row)) if row[c]], title=title_of(T["sec"]),
                          source=T["sec"]["source"], chunk=T["b"].get("chunk"))
                self._cite_block(ans, T["sec"], T["b"])
                return ans
        if len(plan["parts"]) == 1 and not plan.get("pure_define"):
            return None          # a specific question that merely contains a term: the passage pipeline answers it
        own_parts = [p for p in plan["parts"] if p["own"]]
        want_def = any(not p["own"] for p in plan["parts"]) or not own_parts
        if plan["intent"] == "define" and not own_parts:
            want_def = True
        if want_def:
            d = self.definition(entity, plan)
            mr = self.merged_rows(et, limit=6, extra=2) if not own_parts and (not d or d["v"] < 6) else None
            if mr and len(mr[2]) < 2:
                mr = None
            er = self.entity_row(et) if not own_parts and not mr and not d else None
            if mr:
                if d:
                    ans.text(d["text"])
                    self._cite_block(ans, d["sec"], d["b"])
                ans.table(mr[0], mr[1], title=f"{entity[:1].upper() + entity[1:]} — as described in the documents")
                for p in mr[2]:
                    self._cite_block(ans, p[1]["sec"], p[1]["b"])
            elif er:
                _, T, ri = er
                hdr, row = T["b"]["header"], T["b"]["rows"][ri]
                ans.table([hdr[0] or "Item", row[0]], [[hdr[c], row[c]] for c in range(1, len(row)) if row[c]], title=title_of(T["sec"]), source=T["sec"]["source"], chunk=T["b"].get("chunk"))
                self._cite_block(ans, T["sec"], T["b"])
            elif d:
                ans.text(d["text"])
                self._cite_block(ans, d["sec"], d["b"])
            elif not own_parts:
                return None
            else:
                ans.missing.append(f"a definition of {entity}")
        rows_by_table = {}
        for p in own_parts:
            r = self._row_lookup(et, p["own"])
            if r:
                _, T, hits = r
                rows_by_table.setdefault(T["sid"], [T, set()])[1].update(hits)
                continue
            c = self._col_lookup(et, p["own"])
            if c:
                _, T, ri, cols = c
                hdr, row = T["b"]["header"], T["b"]["rows"][ri]
                ans.table(["Item", "Detail"], [[hdr[ci], row[ci]] for ci in cols], title=f"{row[0]}", source=T["sec"]["source"], chunk=T["b"].get("chunk"))
                self._cite_block(ans, T["sec"], T["b"])
                continue
            # a sentence that mentions the entity and the part's words
            found = None
            for sc, sid, cov in self.search(et + p["own"], 6):
                s = self.sec[sid]
                for b in s["blocks"]:
                    if b["t"] in ("p", "li"):
                        for score, st in self._sent_scores(et + p["own"], b["text"]):
                            if score >= 0.7 and (found is None or score > found[0]):
                                found = (score, st, s, b)
            if found:
                ans.text(found[1])
                self._cite_block(ans, found[2], found[3])
            else:
                ans.missing.append(" ".join(p["own"]))
        for _, (T, hits) in rows_by_table.items():
            cols = self._project(T, et)
            hdr, rows = T["b"]["header"], T["b"]["rows"]
            ans.table([hdr[c] for c in cols], [[rows[ri][c] for c in cols] for ri in sorted(hits)],
                      title=title_of(T["sec"]), source=T["sec"]["source"], chunk=T["b"].get("chunk"))
            self._cite_block(ans, T["sec"], T["b"])
            if plan.get("length") != "brief":                # a table in the documents that spells the point out (e.g. "data groups ... must contain")
                terms = [t for p in own_parts for t in p["own"]]
                for T2 in self.tables:
                    if T2 is T or not all(t in T2["meta"] for t in et):
                        continue
                    ttl = set(rag.tokens(T2["sec"]["path"]))
                    if any(t in ttl for t in terms) and len(T2["b"]["rows"]) >= 3:
                        ans.table(T2["b"]["header"], T2["b"]["rows"], title=title_of(T2["sec"]), source=T2["sec"]["source"], chunk=T2["b"].get("chunk"))
                        self._cite_block(ans, T2["sec"], T2["b"])
                        break
        if not ans.blocks:
            return None
        return ans
