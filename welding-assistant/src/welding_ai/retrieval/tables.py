"""Whole-table retrieval and answer-format choice.

A question such as "what are the 4 different testing layers" or "what are the servo gun states" is answered by a
*table* in the documents, and the reader wants the table, not one row or a nearby paragraph. This module

  * indexes every table of every document as one object (title, section, document, column labels, lead-in text, rows);
  * decides from the wording whether the question asks for an enumeration / comparison (-> show the table) or for a
    single fact (-> the normal sentence / row answer);
  * picks the table whose title, section and column labels cover what the question names, using the same
    stemming, compound splitting ("servogun" = "servo gun") and number handling ("4" = "Four", four rows) as the index.
"""
from welding_ai import paths
import json
import math
import re
from collections import Counter
from pathlib import Path

from welding_ai.retrieval import rag

_NUMS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_ENUM = re.compile(
    r"\b(different|various|types?|kinds?|categories|category|classes|classification|list|all|every|each|compare|comparison|"
    r"differences?|options?|variants?|steps|stages|phases|layers?|levels?|states?|modes?|methods?|tests?|"
    r"how many|overview|summary|summari[sz]e|table)\b|\b(what|which)\s+are\b|\b\d+\b|\b(" + "|".join(_NUMS) + r")\b", re.I)
_SINGLE = re.compile(r"\b(what is|what's|define|meaning of|what does .* mean|how is .* used|how do i|how to|why)\b", re.I)
_FILLER = set("different various types type kinds kind list all every each compare comparison difference differences options option "
              "explain describe show give tell me please detail details the a an of in on for and or are is what which how many".split())

MIN_META_COVER = 0.99      # every content word of the question is named by the table's title / section / document / columns
MIN_SCORE = 3.0            # a single common word ("weld") in a table's name is not a match
MIN_LOOSE_COVER = 0.66     # tolerated when the question is plainly an enumeration and the table's rows carry the rest


# Does the wording ask for an enumeration or comparison (a table) rather than one definition / fact?
def wants_table(question):
    """Does the wording ask for an enumeration or comparison (a table) rather than one definition / fact?"""
    q = question.strip()
    if not _ENUM.search(q):
        return False
    if _SINGLE.search(q) and not re.search(r"\b(different|types?|kinds?|list|all|compare|comparison|differences?)\b", q, re.I):
        return False
    return True


class TableIndex:
    # Index every table's metadata (title/section/columns) and body (lead-in + rows) as term sets for matching.
    def __init__(self, tables, index):
        self.t = tables
        self.index = index
        self.meta, self.body, self.rows_tok, self.lead_tok, self.df = [], [], [], [], Counter()
        for t in tables:
            m = set(rag.tokens(" ".join([t["title"], t["heading"], t["source"].rsplit(".", 1)[0].replace("_", " "), " ".join(t["header"])])))
            b = set(rag.tokens(" ".join([t["lead"], t["after"]] + [" ".join(r) for r in t["rows"]])))
            self.meta.append(m)
            self.lead_tok.append(set(rag.tokens(t["lead"])))
            self.rows_tok.append([set(rag.tokens(" ".join(r))) for r in t["rows"]])
            self.body.append(b)
            for x in m | b:
                self.df[x] += 1
        self.n = len(tables)

    # IDF of a term over tables (meta + body combined).
    def idf(self, term):
        df = self.df.get(term, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5)) if df else math.log(1 + self.n) * 0.6

    # Tokenized content words of a question, with generic filler words removed.
    def _content_terms(self, question):
        words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-]*", question.lower()) if w not in _FILLER]
        return [t for t in dict.fromkeys(rag.tokens(" ".join(words)))]

    # Find the single best-matching table for an enumeration/comparison question, by metadata/body/row coverage.
    def match(self, question):
        """-> dict(table, score, cover, reason) or None."""
        if not wants_table(question):
            return None
        terms = self._content_terms(question)
        nums = {int(x) for x in re.findall(r"\b\d+\b", question)} | {_NUMS[w] for w in re.findall(r"\b(" + "|".join(_NUMS) + r")\b", question.lower())}
        terms = [t for t in terms if not t.isdigit()]
        if not terms:
            return None
        tot = sum(self.idf(t) for t in terms) or 1.0
        cands = []
        for i, t in enumerate(self.t):
            m, b = self.meta[i], self.body[i]
            in_meta = sum(self.idf(x) for x in terms if x in m)
            in_body = sum(self.idf(x) for x in terms if x not in m and x in b)
            cover_meta = in_meta / tot
            cover = (in_meta + in_body) / tot
            # the best single row that holds two or more of the question's words ("Cracks | 100 series - longitudinal ...")
            rb = max((sum(self.idf(x) for x in terms if x in rt) for rt in self.rows_tok[i] if sum(x in rt for x in terms) >= 2), default=0.0)
            score = 2.0 * in_meta + in_body
            if nums and len(t["rows"]) in nums:
                score += 0.6 * max(self.idf(x) for x in terms)        # "the 4 layers" -> the table with four rows
                cover += 0.05
            # a long question that quotes the sentence introducing the table points at that table; a short one matches a long lead by chance
            lead = sum(self.idf(x) for x in terms if x in self.lead_tok[i]) / tot if len(terms) >= 6 else 0.0
            cands.append((score, i, cover_meta, cover, max(rb / tot, lead)))
        cands = [c for c in cands if c[0] >= MIN_SCORE]
        # 1) a table whose own title / section / columns name everything asked; 2) else one whose single row or introducing sentence holds most of it; 3) else a loose match
        pick = max((c for c in cands if c[2] >= MIN_META_COVER), default=None, key=lambda c: c[0])
        if pick is None:
            pick = max((c for c in cands if c[3] >= MIN_META_COVER and c[4] >= 0.66), default=None, key=lambda c: (c[4], c[0]))
        if pick is None:
            pick = max((c for c in cands if c[3] >= MIN_LOOSE_COVER and c[2] >= 0.5), default=None, key=lambda c: c[0])
        if pick is None:
            return None
        score, i, cover_meta, cover, _ = pick
        loose = [x for x in terms if x not in self.meta[i]]            # words only the rows carry ("crack", "series")
        rows_hit = [ri for ri, rt in enumerate(self.rows_tok[i]) if len(loose) >= 2 and all(x in rt for x in loose)]
        if not 0 < len(rows_hit) < len(self.t[i]["rows"]):
            rows_hit = []
        return {"table": self.t[i], "score": round(score, 2), "cover": round(cover, 2), "meta_cover": round(cover_meta, 2), "rows_hit": rows_hit}


# Tokenized terms of a table title (numbering prefix stripped).
def _title_terms(title):
    return {t for t in rag.tokens(re.sub(r"^\d+\.\s*", "", title)) if not t.isdigit()}


# Find every table whose own title is what the question names (attached to TableIndex as .match_title).
def _match_title(self, question, min_query=0.85, min_title=0.9):
    """Tables whose own title is what the question names ("Case-Pattern Quick Reference", "AC vs. MFDC: A Real Technical Comparison").
    Several documents may carry a table with the same title: all of them are returned (best score first)."""
    terms = [t for t in self._content_terms(question) if not t.isdigit()]
    if len(terms) < 2:
        return []
    qset, tot_q = set(terms) | set(rag.tokens(question)), sum(self.idf(t) for t in terms) or 1.0        # "comparison" is a filler word for the content terms, but part of a title
    scored = []
    for i, t in enumerate(self.t):
        tt = _title_terms(t["title"])
        if len(tt) < 2:
            continue
        q_in = sum(self.idf(x) for x in terms if x in tt) / tot_q
        t_in = sum(self.idf(x) for x in tt if x in qset) / (sum(self.idf(x) for x in tt) or 1.0)
        if q_in >= min_query and t_in >= min_title:
            scored.append((q_in + t_in, i))
    if not scored:
        return []
    top = max(s for s, _ in scored)
    return [{"table": self.t[i], "score": round(s, 2), "cover": 1.0, "meta_cover": 1.0, "rows_hit": []} for s, i in sorted(scored, reverse=True) if s >= top - 0.05]


TableIndex.match_title = _match_title


# Load tables.json (if it exists) and build a TableIndex over it.
def load(index, path=None):
    path = Path(path) if path else paths.INDEX_DIR / "tables.json"
    if not path.exists():
        return None
    return TableIndex(json.loads(path.read_text(encoding="utf-8")), index)
