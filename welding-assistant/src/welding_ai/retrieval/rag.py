"""Small BM25 search engine for the welding passages (pure standard library, no downloads).

Design notes
- Numbers and standards matter here, so tokens like "1.25", "iso-17635", "6061-t6" are kept whole
  AND split into their parts, so a query for "ISO 17635" and one for "17635" both match.
- Each passage also indexes its heading trail and document title (weighted), because many questions
  are phrased by topic ("friction stir welding ... NDT") while the exact words sit in a table.
- Re-ranking adds a bonus for query word pairs that appear next to each other in the passage.
- `coverage` = share of the (rare-word-weighted) query terms found in the passage; the server uses it
  to decide when nothing in the documents really matches.
"""
from welding_ai import paths
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

PROTECT = set("bullets bulleted tabular tabulate tabulated briefly concisely thoroughly summarise summarize lifecycle diagnose troubleshoot".split())
STOP = set("""a an the of in on for to and or is are was were be been being what which who whom whose when
where why how does do did can could should would may might shall must with without by at from as that this
these those it its into than then there their about per if any all each other such not no yes also used use
using between within under over via vs i we you our your us me my tell give list explain describe define done want please whether make made need needs needed just quick hey hi hello thanks
ok okay okey kindly pls plz really actually basically anyway""".split())

# The documents use both spellings of some words (aluminium 417x / aluminum 90x); one form is indexed and queried, so neither spelling is
# favoured by chance. Keys are stemmed forms, values the form the documents use most.
VARIANTS = {"aluminum": "aluminium", "sulfur": "sulphur", "color": "colour", "center": "centre", "gray": "grey", "behavior": "behaviour"}

_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-/][a-z0-9]+)*")
_SUBS = str.maketrans("₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹", "01234567890123456789")
_SPLIT = re.compile(r"[.\-/]")
# A standard identifier ("ISO 4063", "AWS D1.1", "ISO 15614-1") is one term, in documents and questions alike.
_STDID = re.compile(r"\b(iso|aws|asme|astm|ipc|ansi|csa|din|api|bs|en)[\s\-/]*((?:[a-z]{1,2}[\s\-]?)?\d+(?:\.\d+)?(?:-\d+)?)", re.I)


# Extract standard identifiers ("ISO 4063" -> "stdiso4063") as single normalized tokens.
def _std_tokens(text):
    return ["std" + m.group(1).lower() + re.sub(r"[^a-z0-9]", "", m.group(2).lower()) for m in _STDID.finditer(text)]



# Cheap suffix-stripping stemmer (not Porter): strips common endings only for words of 5+ letters.
def stem(w):
    if len(w) <= 4 or w.isdigit():
        return w
    if w.endswith("ies"):
        return w[:-3] + "y"
    for suf in ("ing", "ed", "es", "s", "ly"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


# The index form of a word: stemmed, with variant spellings merged.
def key(w):
    """The index form of a word: stemmed, with variant spellings merged."""
    s = stem(w)
    return VARIANTS.get(s, s)


# Ordered list of index terms (stemmed, stop words removed, compounds kept + split, standard ids appended).
def tokens(text):
    """Ordered list of index terms (stemmed, stop words removed, compounds kept + split)."""
    out = []
    for m in _TOKEN.findall(text.lower().translate(_SUBS)):
        parts = _SPLIT.split(m)
        if len(parts) > 1:
            out.append(m)  # compound: 1.25, iso-17635, 6061-t6, kj/mm
        for p in parts:
            if not p or p in STOP:
                continue
            if p.isdigit() and len(p) < 2 and len(parts) > 1:
                continue  # a lone digit from splitting "1.25" is noise
            out.append(key(p))
    out.extend(_std_tokens(text.translate(_SUBS)))
    return out


# Plain lowercase words of the query (for highlighting in the UI).
def query_words(text):
    """Plain lowercase words of the query (for highlighting in the UI)."""
    seen, out = set(), []
    for m in _TOKEN.findall(text.lower().translate(_SUBS)):
        for p in ([m] + _SPLIT.split(m) if _SPLIT.search(m) else [m]):
            if p and p not in STOP and p not in seen and (len(p) > 1 or p.isdigit()):
                seen.add(p)
                out.append(p)
    return out


class Index:
    K1, B = 1.4, 0.75
    HEAD_WEIGHT = 2  # heading / title terms count this many times
    WEAK_FIX_MIN_DF = 60   # passages a word must occur in to be accepted as the fix for a loosely matching unknown word
    UNSEEN = 0.6     # weight of query words absent from the corpus, relative to a very rare word

    # Build BM25 postings (term -> [(chunk, tf)]) over every chunk's body + weighted heading/title.
    def __init__(self, chunks):
        self.chunks = chunks
        self.post = defaultdict(list)  # term -> [(doc, tf)]
        self.dl = []
        self.terms = []  # per doc: set of terms (for coverage)
        self.bigrams = []
        for i, c in enumerate(chunks):
            body = tokens(c["text"])
            head = tokens(c["heading"] + " " + Path(c["source"]).stem.replace("_", " "))
            tf = Counter(body)
            for t in head:
                tf[t] += self.HEAD_WEIGHT
            for t, f in tf.items():
                self.post[t].append((i, f))
            self.dl.append(len(body) + self.HEAD_WEIGHT * len(head))
            self.terms.append(set(tf))
            self.bigrams.append(set(zip(body, body[1:])))
        self.n = len(chunks)
        self.avgdl = sum(self.dl) / max(self.n, 1)

    # Standard BM25 inverse document frequency of a term over chunks.
    def idf(self, t):
        df = len(self.post.get(t, ()))
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5)) if df else 0.0

    # Top-k chunks by BM25 score, with a bigram-adjacency bonus and a coverage-based re-weighting.
    def search(self, query, k=5):
        qt = tokens(query)
        if not qt:
            return []
        qtf = Counter(qt)
        scores = defaultdict(float)
        for t, qn in qtf.items():
            plist = self.post.get(t)
            if not plist:
                continue
            idf = self.idf(t)
            boost = 1 + 0.15 * (qn - 1)
            for i, f in plist:
                denom = f + self.K1 * (1 - self.B + self.B * self.dl[i] / self.avgdl)
                scores[i] += idf * f * (self.K1 + 1) / denom * boost
        if not scores:
            return []
        top = sorted(scores.items(), key=lambda x: -x[1])[:60]
        qb = set(zip(qt, qt[1:]))
        # words that occur nowhere in the documents count as (fairly rare) evidence of a poor match
        unseen = math.log(1 + self.n) * self.UNSEEN
        weights = {t: (self.idf(t) or unseen) for t in qtf}
        total_w = sum(weights.values()) or 1.0
        ranked = []
        for i, s in top:
            s += 1.0 * len(qb & self.bigrams[i])
            cov = sum(w for t, w in weights.items() if t in self.terms[i]) / total_w
            ranked.append((s * (0.6 + 0.4 * cov), i, cov))
        ranked.sort(key=lambda x: -x[0])
        res = []
        for s, i, cov in ranked[:k]:
            c = self.chunks[i]
            res.append({"chunk_id": c["chunk_id"], "source": c["source"], "heading": c["heading"],
                        "text": c["text"], "score": round(s, 3), "coverage": round(cov, 3)})
        return res

    # ------------------------------------------------------------------ typo tolerance
    # Indexed vocabulary grouped by first letter, for spelling-correction candidate lookup.
    def _vocab_by_letter(self):
        if not hasattr(self, "_vl"):
            self._vl = defaultdict(list)
            for t in self.post:
                if not t.isdigit() and len(t) >= 4 and t.isalpha():
                    self._vl[t[0]].append(t)
        return self._vl

    # True when the question contains a word from the documents' own titles or section headings.
    def has_anchor(self, query):
        """True when the question contains a word from the documents' own titles or section headings (weld, electrode, nugget, HAZ ...).
        A question with none of them is not about the documents' topic, even if some of its words occur somewhere in the text."""
        if not hasattr(self, "_anchor"):
            self._anchor = set()
            for c in self.chunks:
                self._anchor.update(t for t in tokens(c["heading"] + " " + Path(c["source"]).stem.replace("_", " ")) if len(t) > 2 and not t.isdigit())
        return any(t in self._anchor for t in tokens(query))

    # Words as actually written in the documents (unstemmed), for surface-form typo matching.
    def _surface_vocab(self):
        """Words as written in the documents (5+ letters) with their counts, by first letter: typos are matched against these,
        because the stemmed index no longer holds "welding" or "resistance"."""
        if not hasattr(self, "_sv"):
            counts = Counter(w for c in self.chunks for w in re.findall(r"[a-z]{5,}", c["text"].lower()))
            self._sv = defaultdict(dict)
            for w, n in counts.items():
                self._sv[w[0]][w] = n
        return self._sv

    # Closest surface-form spelling of an unrecognized word, via difflib fuzzy matching.
    def _surface_match(self, lw):
        import difflib
        cands = {c: n for c, n in self._surface_vocab().get(lw[0], {}).items() if abs(len(c) - len(lw)) <= 2}
        if lw in cands:
            return None
        close = difflib.get_close_matches(lw, list(cands), n=6, cutoff=0.85)
        if not close and len(lw) >= 6:                      # nothing under the same first letter: allow a wrong or extra first letter, with a stricter cutoff
            if not hasattr(self, "_sv_all"):
                self._sv_all = {w: n for d in self._surface_vocab().values() for w, n in d.items()}
            others = {c: n for c, n in self._sv_all.items() if abs(len(c) - len(lw)) <= 1 and (c[1:4] == lw[1:4] or c[0:3] == lw[1:4])}
            close = difflib.get_close_matches(lw, list(others), n=6, cutoff=0.9)
            cands = others
        return max(close, key=lambda c: cands[c]) if close else None

    # Try to split an unrecognized compound word into two indexed words ("servogun" -> "servo gun").
    def _split_compound(self, w):
        """"servogun" -> "servo gun" when both halves are indexed words and the joined form is not."""
        best = None
        for i in range(3, len(w) - 2):
            a, b = w[:i], w[i:]
            if a in self.post and (b in self.post or key(b) in self.post):
                sc = min(len(self.post[a]), len(self.post.get(b) or self.post.get(key(b), ())))
                if best is None or sc > best[0]:
                    best = (sc, a + " " + b)
        return best[1] if best else None

    # Content words that appear nowhere in the documents even after spelling correction.
    def unseen_words(self, query):
        """Content words (4+ letters) that appear nowhere in the documents even after spelling correction:
        a strong sign the question is about something the documents do not cover (blockchain, marathon, Australia)."""
        out = []
        for w in re.findall(r"[A-Za-z]{4,}", query.lower()):
            if w in STOP or w in PROTECT:
                continue
            if key(w) not in self.post and w not in self.post:
                out.append(w)
        return out

    # Fix likely typos by snapping each unrecognized word to the closest indexed/surface word, trying
    # compound-splitting and adjacent-letter-swap fixes first.
    def correct(self, query):
        """Fix likely typos ("temprature", "stell") by snapping unknown words to the closest indexed word.
        Returns (new_query, [(old, new), ...])."""
        import difflib
        vl = self._vocab_by_letter()
        changes, out = [], []
        for m in re.finditer(r"[A-Za-z]+|[^A-Za-z]+", query):        # words are corrected one by one, also inside brackets and hyphenated terms
            w = m.group(0)
            lw = w.lower()
            if w.isalpha() and len(w) >= 5 and lw not in PROTECT and lw not in STOP and key(lw) not in self.post and lw not in self.post:
                surf = self._surface_match(lw)
                sp = None if surf else self._split_compound(lw)     # "resistnce" is a typo of "resistance", not "resist" + "nce"
                if sp:
                    changes.append((w, sp))
                    out.append(sp)
                    continue
                swapped = next((lw[:i] + lw[i + 1] + lw[i] + lw[i + 2:] for i in range(len(lw) - 1)
                                if lw[i] != lw[i + 1] and (key(lw[:i] + lw[i + 1] + lw[i] + lw[i + 2:]) in self.post or (lw[:i] + lw[i + 1] + lw[i] + lw[i + 2:]) in self.post)), None)
                if swapped:                                # two letters typed in the wrong order
                    changes.append((w, swapped))
                    out.append(swapped)
                    continue
                cands = [c for c in vl.get(lw[0], []) if abs(len(c) - len(stem(lw))) <= 2]
                close = difflib.get_close_matches(stem(lw), cands, n=6, cutoff=0.78)
                close = [c for c in close if len(c) >= 5 or len(c) == len(stem(lw))]            # never shorten a word into a 4-letter one ("pasta" -> "past")
                close = [c for c in close if difflib.SequenceMatcher(None, stem(lw), c).ratio() >= 0.85
                         or (len(self.post[c]) >= self.WEAK_FIX_MIN_DF and abs(len(c) - len(stem(lw))) <= 1)]      # a loose match ("stell" -> "steel") must be a common word of about the same length
                if close:
                    best = max(close, key=lambda c: len(self.post[c]))   # prefer the word used in most passages
                    changes.append((w, best))
                    out.append(best)
                    continue
                if surf:                                          # "weling": the index holds the stem "weld", the documents hold "welding"
                    changes.append((w, surf))
                    out.append(surf)
                    continue
            out.append(w)
        return "".join(out), changes

    # ------------------------------------------------------------------ focused excerpts
    # Split passage text into sentence/line-level units (splitting long non-table lines on sentence boundaries).
    @staticmethod
    def _units(text):
        units = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if len(line) > 420 and not line.startswith("|"):
                units.extend(x.strip() for x in re.split(r"(?<=[.!?;])\s+(?=[A-Z0-9\[(])", line) if x.strip())
            else:
                units.append(line)
        return units

    # IDF-weighted overlap score of each unit against the query, length-normalized.
    def _unit_scores(self, units, query):
        qt = set(tokens(query))
        w = {t: self.idf(t) for t in qt}
        scores = []
        for u in units:
            ut = set(tokens(u))
            scores.append(sum(w[t] for t in qt if t in ut) / (1 + 0.0015 * len(u)))
        return scores

    # Trim a passage to ~max_chars around its most query-relevant unit, expanding outward greedily and
    # preserving the table header if a table row is kept.
    def focus(self, text, query, max_chars=850):
        """The part of a passage most relevant to the query (keeps table headers), trimmed to ~max_chars."""
        if len(text) <= max_chars:
            return text.strip()
        units = self._units(text)
        sc = self._unit_scores(units, query)
        best = max(range(len(units)), key=lambda i: sc[i])
        lo = hi = best
        size = len(units[best])
        while True:
            can_prev, can_next = lo > 0, hi < len(units) - 1
            if not (can_prev or can_next):
                break
            pick_next = can_next and (not can_prev or (sc[hi + 1] >= sc[lo - 1]))
            j = hi + 1 if pick_next else lo - 1
            if size + len(units[j]) + 1 > max_chars:
                break
            size += len(units[j]) + 1
            if pick_next:
                hi = j
            else:
                lo = j
        chosen = units[lo:hi + 1]
        if chosen[0].startswith("|") and lo >= 2 and units[0].startswith("|"):
            chosen = [units[0], units[1]] + [u for u in chosen if u not in units[:2]]   # keep the table header
        return "\n".join(chosen)

    # Pull the best verbatim sentences/table rows out of the top passages, for extractive quoting.
    def evidence(self, hits, query, per_hit=2, limit=5):
        """Best verbatim sentences / table rows from the top passages: (n, source, section, text)."""
        out = []
        for n, h in enumerate(hits, 1):
            units = self._units(h["text"])
            sc = self._unit_scores(units, query)
            order = sorted(range(len(units)), key=lambda i: -sc[i])[:per_hit]
            for i in sorted(order):
                u = units[i]
                if sc[i] <= 0 or u.startswith("| ---") or len(u) < 25 or re.search(r"https?:|cdn\.|www\.", u):
                    continue
                if i == 0 and (u[0].islower() or u[0] in "])"):     # first line of a passage can be an overlap fragment
                    continue
                out.append({"n": n, "chunk_id": h["chunk_id"], "source": h["source"],
                            "section": h["heading"].split(" > ")[-1], "text": units[i][:420], "score": round(sc[i], 2)})
        out.sort(key=lambda e: -e["score"])
        return out[:limit]


# Common welding abbreviations and spelling variants. Expansion words are only added if they occur in the documents.
ABBREV = {
    "mig": "gmaw", "mag": "gmaw", "mig/mag": "gmaw", "tig": "gtaw", "stick": "smaw shielded", "flux core": "fcaw flux cored",
    "spot weld": "resistance spot rsw", "spot welding": "resistance spot rsw", "sub arc": "submerged",
    "wpq": "welder qualification", "wpqr": "procedure qualification record", "pqr": "procedure qualification record",
    "amps": "current", "amperage": "current", "volts": "voltage",
    "mild steel": "carbon steel low-carbon", "inox": "stainless", "pick": "select choose", "choose": "select", "select": "choose",
    "welder cert": "welder qualification", "welder certification": "welder qualification", "ndt": "non-destructive testing",
    "haz": "heat affected zone", "pwht": "post-weld heat treatment",
}


# Append expansion words for any welding abbreviation found in the query (attached to Index as .expand).
def _expand(self, query):
    q = query.lower()
    extra = []
    for k, v in ABBREV.items():
        if re.search(r"(?<![a-z0-9])" + re.escape(k.lower()) + r"(?![a-z0-9])", q):
            for w in v.split():
                if w in self.post or key(w) in self.post:
                    extra.append(w)
    extra = [w for w in dict.fromkeys(extra) if w not in q]
    return query + " " + " ".join(extra) if extra else query


Index.expand = _expand


# Load chunks.json and build a plain BM25 Index over it.
def load(path=None):
    path = Path(path) if path else paths.INDEX_DIR / "chunks.json"
    return Index(json.loads(path.read_text(encoding="utf-8")))
