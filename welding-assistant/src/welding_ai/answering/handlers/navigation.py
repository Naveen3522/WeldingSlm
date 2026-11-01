"""Questions about the structure of the documents: a heading typed as the question, a chapter (its summary and headings), which chapter
covers a topic, how many chapters there are.

Everything is read from the documents' own structure, never typed in:
  * the chapter catalogue = the tables of `00_Contents_and_Chapter_Index` with the columns Chapter, Title, Pages, Covers;
  * each chapter's headings = the sections recovered from the .docx (Word heading styles, or bold text set larger than the body);
  * a heading question = the words of the question are a heading (or the start of one) in one or more documents.
"""
import re

from welding_ai.answering.evidence import Answer, nice, title_of
from welding_ai.retrieval import rag
from welding_ai.retrieval.roles import is_nav_title

_WRAP = re.compile(r"^\s*(?:(?:what(?:'s| is| are)|explain|describe|tell me about|define|show me|give me|summari[sz]e)\s+)?(?:the\s+|a\s+|an\s+)?", re.I)
_CH_NUM = re.compile(r"\b(?:chapter|ch\.?)\s*0*(\d{1,2})\b", re.I)
_OVERVIEW = re.compile(r"\b(summar\w*|overview|outline|contents?|headings?|sections?|topics?|about|cover(?:s|ed|ing)?|contains?|includes?|inside|key points?|main points?|"
                       r"what(?:'s| is) in)\b", re.I)
_WHICH = re.compile(r"\b(?:which|what)\s+(?:chapters?|documents?)\b", re.I)
_COUNT = re.compile(r"\bhow many\s+(chapters?|volumes?|documents?)\b", re.I)
_LISTALL = re.compile(r"\b(?:list|show|give|what are)\b[^.?]{0,25}\b(?:all\s+)?(?:the\s+)?chapters\b|\btable of contents\b|\bwhat chapters (?:are|do)\b", re.I)
_NAVWORDS = {"chapter", "chapters", "document", "documents", "doc", "summary", "summarise", "summarize", "overview", "outline", "content", "contents", "heading",
             "headings", "section", "sections", "topic", "topics", "cover", "covers", "covered", "covering", "contain", "contains", "include", "includes", "inside",
             "key", "main", "point", "points", "list", "show", "give", "detail", "brief", "which", "on", "all", "set", "reference", "table", "tabular", "format",
             "steps", "bullet", "bullets", "tell", "me", "please"}
_NUMBERING = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+")


# Deduplicated, non-numeric tokens of a text.
def _terms(text):
    return [t for t in dict.fromkeys(rag.tokens(text)) if not t.isdigit()]


class NavigationHandlers:
    # ------------------------------------------------------------------ structure read from the documents
    # [(section, title tokens without the number)] of every non-navigation heading, built once and cached.
    def _headings_index(self):
        """[(section, title tokens without the number)] of every heading that is not a navigation list, built once."""
        if not hasattr(self, "_hd"):
            self._hd = []
            for s in self.sec:
                if s["level"] < 1 or is_nav_title(s["title"]):
                    continue
                toks = _terms(_NUMBERING.sub("", s["title"]))            # the same reduction as the question (repeats dropped)
                if toks:
                    self._hd.append((s, toks))
        return self._hd

    # Parse the "00_Contents_and_Chapter_Index" tables into {chapter number: chapter info dict}, cached.
    def _catalogue(self):
        """{chapter number: dict(number, title, pages, covers, volume, table, row, source)} from the catalogue tables."""
        if not hasattr(self, "_cat"):
            self._cat, self._vols = {}, []
            for T in self.tables:
                if not T["sec"]["source"].startswith("00_"):
                    continue
                hdr = [h.lower() for h in T["b"]["header"]]
                if hdr[:2] == ["ch.", "title"] and len(hdr) >= 4:
                    for ri, r in enumerate(T["b"]["rows"]):
                        if r[0].strip().isdigit():
                            n = int(r[0])
                            src = next((k for k in self.by_src if re.match(r"^0*%d_" % n, k)), None)
                            self._cat[n] = {"number": n, "title": r[1], "pages": r[2], "covers": r[3], "volume": _NUMBERING.sub("", T["sec"]["title"]).split("—")[-1].strip(), "T": T, "ri": ri, "source": src}
                elif hdr[:2] == ["volume", "subject"]:
                    self._vols = [r for r in T["b"]["rows"] if r[0].strip().isdigit()]
        return self._cat

    # Top-level, numbered, non-navigation section headings of one document.
    def _chapter_headings(self, source):
        return [s for s in self.by_src.get(source, []) if s["level"] == 1 and not is_nav_title(s["title"]) and re.match(r"^\s*\d+\.", s["title"])]

    # ------------------------------------------------------------------ a heading typed as the question
    # Answer directly when the question's wording IS a section heading (or the start of one), possibly across several documents.
    def heading_lookup(self, plan, exact_only=False):
        """"Traceability: What It Means in Practice?" / "Failure Catalog": the question is a heading (or the start of one). One heading gives
        that section; the same heading in several documents gives the list of them to choose from."""
        q = re.sub(r"[?!.]+\s*$", "", plan["question"].strip())
        q = _WRAP.sub("", q) if re.match(r"^\s*(?:what(?:'s| is| are)|explain|describe|tell me about|define|show me|give me)\b", q, re.I) else q
        qt = _terms(q)
        if not 2 <= len(qt) <= 10:
            return None
        exact, prefix = [], []
        for s, toks in self._headings_index():
            if toks == qt:
                exact.append(s)
            elif len(toks) > len(qt) and toks[:len(qt)] == qt:
                prefix.append(s)
        hits = exact or ([] if exact_only else prefix)
        if not hits or (exact_only and len(qt) < 3):
            return None
        if len(hits) == 1:
            return self._render_section(plan, hits[0])
        ans = Answer("headings")
        ans.text(f"**{len(hits)} sections have this heading.** Choose one:")
        rows = [[nice(s["source"]), _NUMBERING.sub("", s["title"])] for s in hits[:12]]
        ans.table(["Document", "Section"], rows, title="Sections with this heading")
        ans.clarify = [{"label": nice(s["source"]), "query": "explain " + _NUMBERING.sub("", s["title"])} for s in hits[:8]]
        for s in hits[:6]:
            first = next((b for b in s["blocks"] if b["t"] in ("p", "li")), None)
            if first:
                self._cite_block(ans, s, first)
        return ans

    # ------------------------------------------------------------------ chapters
    # Route a chapter/document-structure question to a count, catalogue table, "which chapter" search, or one chapter's summary.
    def chapter(self, plan):
        q = plan["question"]
        cat = self._catalogue()
        if not cat:
            return None
        m = _CH_NUM.search(q)
        rest = [t for t in _terms(_CH_NUM.sub(" ", q)) if t not in {rag.key(w) for w in _NAVWORDS}]
        if _COUNT.search(q) and not m:
            return self._count_answer(q, cat)
        if _LISTALL.search(q) and not m:
            return self._catalogue_table(plan, cat)
        if _WHICH.search(q) and not m:
            return self._which_chapters(plan, cat)
        if m:
            n = int(m.group(1))
            if n in cat and (not rest or _OVERVIEW.search(q)) and len(rest) <= 1:
                return self._chapter_answer(plan, cat[n])
            return None
        if _OVERVIEW.search(q) and re.search(r"\b(chapter|document|doc)\b", q, re.I):        # "the servo guns chapter", "summary of the welding safety document"
            qt = [t for t in _terms(q) if t not in {rag.key(w) for w in _NAVWORDS}]
            if qt:
                best = []
                for c in cat.values():
                    tt = set(rag.tokens(c["title"]))
                    if all(t in tt for t in qt):
                        best.append(c)
                if len(best) == 1:
                    return self._chapter_answer(plan, best[0])
                if 1 < len(best) <= 6:
                    return self._chapter_choices(best)
        return None

    # A single chapter's summary block: title, covers text, and its section headings (unless a brief summary was asked for).
    def _chapter_answer(self, plan, c):
        ans = Answer("chapter")
        T = c["T"]
        head = f"**Chapter {c['number']:02d} — {c['title']}**"
        meta = " · ".join(x for x in (c["volume"], f"{c['pages']} pages" if c["pages"] else "") if x)
        ans.text(head + (f" · *{meta}*" if meta else ""))
        ans.text(f"**Covers:** {c['covers']}")
        self._cite_block(ans, T["sec"], T["b"])
        heads = self._chapter_headings(c["source"]) if c["source"] else []
        short = re.search(chr(92) + "b(brief(ly)?|short(ly)?|quick(ly)?|concise(ly)?|one line|tl;?dr)" + chr(92) + "b", plan["question"], re.I)      # "summarize" alone still lists the headings
        if heads and not short:
            titles = [s["title"] for s in heads]
            if plan.get("format") == "table":
                ans.table(["No.", "Section"], [[re.match(r"^\s*(\d+)", t).group(1), _NUMBERING.sub("", t)] for t in titles], title=f"Sections of chapter {c['number']:02d}")
            else:
                ans.text(f"**Sections ({len(titles)}):**\n" + "\n".join(f"{i}. {_NUMBERING.sub('', t)}" for i, t in enumerate(titles, 1)))
            for s in heads[:3]:
                first = next((b for b in s["blocks"] if b["t"] in ("p", "li")), None)
                if first:
                    self._cite_block(ans, s, first)
        return ans

    # A disambiguation table + clarify options when several chapters match the question.
    def _chapter_choices(self, chapters):
        ans = Answer("chapter_choices")
        ans.text(f"**{len(chapters)} chapters match.** Choose one:")
        ans.table(["Chapter", "Title", "Covers"], [[f"{c['number']:02d}", c["title"], c["covers"]] for c in chapters], title="Chapters")
        ans.clarify = [{"label": f"{c['number']:02d} {c['title']}", "query": f"summarize chapter {c['number']}"} for c in chapters]
        c0 = chapters[0]
        self._cite_block(ans, c0["T"]["sec"], c0["T"]["b"])
        return ans

    # The full chapter catalogue as one table.
    def _catalogue_table(self, plan, cat):
        ans = Answer("catalogue")
        rows = [[f"{c['number']:02d}", c["title"], c["covers"]] for c in sorted(cat.values(), key=lambda c: c["number"])]
        ans.table(["Chapter", "Title", "Covers"], rows, title="Chapter catalogue")
        c0 = next(iter(cat.values()))
        self._cite_block(ans, c0["T"]["sec"], c0["T"]["b"])
        return ans

    # Answer "how many chapters/volumes/documents".
    def _count_answer(self, q, cat):
        ans = Answer("count")
        what = _COUNT.search(q).group(1).lower()
        if what.startswith("volume"):
            ans.text(f"The reference set has **{len(self._vols)} volumes**.")
            ans.table(["Volume", "Subject", "Chapters", "Pages"], [r[:4] for r in self._vols], title="Volumes")
        elif what.startswith("doc"):
            ans.text(f"The reference set has **{len(self.by_src)} documents**: the contents index and {len(cat)} chapters.")
        else:
            ans.text(f"The reference set has **{len(cat)} chapters** in {len(self._vols)} volumes.")
        c0 = next(iter(cat.values()))
        self._cite_block(ans, c0["T"]["sec"], c0["T"]["b"])
        return ans

    # "which chapter covers X": rank chapters by how much of X's words their title/Covers/headings contain.
    def _which_chapters(self, plan, cat):
        """"which chapter covers X": the chapters whose title, Covers text or headings hold the words of X."""
        qt = [t for t in _terms(plan["question"]) if t not in {rag.key(w) for w in _NAVWORDS} and t not in {"discuss", "deal", "explain", "talk", "about", "describ"}]
        if not qt:
            return None
        tot = sum(self.idf(t) for t in qt) or 1.0
        scored = []
        for c in cat.values():
            have = set(rag.tokens(c["title"] + " " + c["covers"]))
            for s in self._chapter_headings(c["source"]) if c["source"] else []:
                have.update(rag.tokens(s["title"]))
            cov = sum(self.idf(t) for t in qt if t in have) / tot
            if cov >= 0.6:
                scored.append((cov, c))
        if not scored:
            return None
        scored.sort(key=lambda x: (-x[0], x[1]["number"]))
        top = [c for cov, c in scored if cov >= scored[0][0] - 1e-9][:6] or [scored[0][1]]
        ans = Answer("which_chapter")
        ans.table(["Chapter", "Title", "Covers"], [[f"{c['number']:02d}", c["title"], c["covers"]] for c in top], title="Chapters that cover this")
        for c in top[:3]:
            self._cite_block(ans, c["T"]["sec"], c["T"]["b"])
        return ans
