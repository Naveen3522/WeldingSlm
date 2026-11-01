"""Answer composition and verification.

- `qtype`            classify the question (standard / value / why / procedure / difference / definition / general)
- `compose_extractive` build an answer purely from verbatim sentences of the retrieved passages (no model)
- `verify`           check a model-written answer against the passages it was given
"""
import re

from welding_ai.retrieval import rag

STD = re.compile(r"\b(?:ISO|AWS|EN|ASME|ASTM|IPC|API|BS|DIN|CSA|IEC|AS/NZS|ANSI)[\s/\-]*(?:[A-Z]{1,3}\s?)?\d[\w.\-:/]*")
VALUE = re.compile(r"\d+(?:[.,]\d+)?\s?(?:%|°\s?[CF]|kA|A\b|V\b|kV|mm|cm|µm|μm|kJ/mm|kJ/in|J\b|s\b|ms|Hz|kHz|N\b|kN|bar|MPa|ksi|psi|l/min|cfh|cycles|inch|in\b|kg|Ω)|\d+\s?[–-]\s?\d+")
WHY = re.compile(r"\b(because|since|so that|therefore|reason|as a result|which is why|this is why|due to|causes?|caused by|driven by|explains?)\b", re.I)
PROC = re.compile(r"^(?:\d+[.)]\s*)?(?:remove|clean|check|use|maintain|ensure|verify|keep|set|select|apply|inspect|record|dress|replace|reduce|increase|"
                  r"control|avoid|store|purge|test|calibrate|confirm|measure|adjust|hold|preheat|screen|leak-check|return|reposition|correct)\b"
                  r"|\b(?:must|shall|should|required|never|always)\b", re.I)
CONTRAST = re.compile(r"\b(while|whereas|unlike|versus|than|in contrast|but|instead|compared)\b", re.I)

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_ACR = re.compile(r"\b(?:[A-Z]{2,}[A-Za-z0-9\-]*|[A-Z]+\d[A-Za-z0-9\-]*)\b")
_STOPW = set("the a an of in on for to and or is are was were be been by at from as that this these those it its with without not no than then "
             "their there into per any all each other such can may must shall do does did which who what when where how why also used use using "
             "only more most less very both either between within about over under above below after before during while".split())

NOT_FOUND = "The provided documents do not contain this information."


# Classify the question's expected answer shape (standard / value / why / procedure / difference / definition / general).
def qtype(q):
    ql = q.lower()
    if re.search(r"\b(which|what)\s+(?:\w+\s+){0,2}(standards?|codes?|specifications?)\b|\bstandards?\s+(for|govern|define|cover|apply|specif)", ql):
        return "standard"
    if re.search(r"\bdifference\b|\bcompare|\bvs\b|\bversus\b|\bbetter than\b", ql):
        return "difference"
    if re.match(r"\s*why\b", ql) or re.search(r"\breasons?\b|\bhow come\b", ql):
        return "why"
    if re.search(r"\bhow (much|many|long|high|low|hot|thick|often)\b|\b(typical|minimum|maximum|recommended|required|acceptable)\b|"
                 r"\b(temperature|current|voltage|range|flow rate|force|thickness|diameter|percent|ratio)\b", ql):
        return "value"
    if re.search(r"\bhow (do|to|should|can|must)\b|\bwhat (must|should|needs?|has) to be done\b|\bsteps?\b|\bprocedure\b|\bwhat to do\b|\bcorrect(ed|ion)?\b|\bprevent", ql):
        return "procedure"
    if re.match(r"\s*(what is|what are|what does|define|meaning of|explain)\b", ql):
        return "definition"
    return "general"


_BOOST = {
    "standard": lambda u: 0.7 if STD.search(u) else 0.0,
    "value": lambda u: 0.5 if VALUE.search(u) else 0.0,
    "why": lambda u: 0.5 if WHY.search(u) else 0.0,
    "procedure": lambda u: 0.4 if PROC.search(u) else 0.0,
    "difference": lambda u: 0.3 if CONTRAST.search(u) else 0.0,
    "definition": lambda u: 0.15 if re.search(r"\b(is|are|means|refers to)\b", u) else 0.0,
    "general": lambda u: 0.0,
}


# Header cells of the markdown table that contains `row` (used to label table rows).
def _table_header(text, row):
    """Header cells of the markdown table that contains `row` (used to label table rows)."""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.strip() == row.strip():
            j = i
            while j > 0 and lines[j - 1].strip().startswith("|"):
                j -= 1
            if j + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|?$", lines[j + 1].strip()) and j != i:
                return [c.strip() for c in lines[j].strip().strip("|").split("|")]
    return None


_GENERIC = set("how is it the of what a an and to does do are for in on which who why when".split())


# Cheap suffix-stripping stemmer for header/question word matching.
def _wstem(w):
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


# Does a table column header ask for what the question asks?
def _hdr_matches(header, question):
    """Does a table column header ask for what the question asks ("How It Is Used" <- "how is X used")?"""
    hw = {_wstem(w) for w in re.findall(r"[a-z]+", header.lower())} - {_wstem(w) for w in _GENERIC}
    qw = {_wstem(w) for w in re.findall(r"[a-z]+", (question or "").lower())}
    return bool(hw & qw)


# Turn a raw unit (sentence or table row) into display text: table rows as "label: value" pairs (asked-for
# columns first), plain sentences with list markers/citation tags stripped.
def clean_unit(u, chunk_text=None, question=None):
    u = u.strip()
    if u.startswith("|"):
        cells = [c.strip() for c in u.strip("|").split("|")]
        hdr = _table_header(chunk_text, u) if chunk_text else None
        if hdr and len(hdr) == len(cells):
            idx = [i for i in range(len(cells)) if cells[i] and hdr[i]]
            key = [i for i in idx[:1]]                                   # the row's subject stays first
            asked = [i for i in idx[1:] if question and _hdr_matches(hdr[i], question)]
            rest = [i for i in idx[1:] if i not in asked]
            order = (key + asked + rest)[:5]
            return "; ".join(f"{hdr[i]}: {cells[i]}" for i in order)
        return " · ".join(c for c in cells if c)
    u = re.sub(r"^[\]\)\.,;:\s]+", "", u)
    u = re.sub(r"^\s*(?:[•\-*]|\d+[.)])\s+", "", u)
    u = re.sub(r"\s*\[S\d+\](?:\[S\d+\])*", "", u)
    return re.sub(r"\s+", " ", u).strip()


# Token set of a string.
def _tok(s):
    return set(rag.tokens(s))


# A unit too short, boilerplate-y or link-like to ever be a good answer.
def _bad_unit(u):
    return (len(u) < 28 or u.startswith("| ---") or re.search(r"https?:|cdn\.|www\.|Catalogue|catalog page", u, re.I)
            or re.match(r"^\[S\d+\]", u) or re.match(r"^\|?\s*(?:Position|Specification|Mode|Parameter|Component|Level)\s*\|", u) and "---" in u)


# Share of the question's rare-word-weighted terms that the sentence contains.
def _cover(index, qtoks, unit):
    """Share of the question's rare-word-weighted terms that the sentence contains."""
    if not qtoks:
        return 0.0
    ut = set(rag.tokens(unit))
    tot = sum(index.idf(t) or 3.0 for t in qtoks)
    return sum((index.idf(t) or 3.0) for t in qtoks if t in ut) / tot if tot else 0.0


# Score every sentence/row of the retrieved passages against the question, with qtype-based and position boosts.
def candidate_units(index, question, hits):
    """All scored (score, hit_no, cleaned_text, raw_unit, cover) sentences from the retrieved passages."""
    qt = qtype(question)
    boost = _BOOST[qt]
    qtoks = set(rag.tokens(question))
    known = [t for t in qtoks if index.idf(t) > 0 and not t.isdigit()]
    rarest = max(known, key=index.idf) if known else None       # the most distinctive term of the question
    out = []
    for n, h in enumerate(hits, 1):
        units = index._units(h["text"])
        scorer = getattr(index, "score_units", None)
        base = scorer(question, h["chunk_id"]) if scorer else index._unit_scores(units, question)
        if len(base) != len(units):
            base = index._unit_scores(units, question)
        for j, (u, s) in enumerate(zip(units, base)):
            if s <= 0 or _bad_unit(u):
                continue
            if j == 0 and (u[0].islower() or u[0] in "])},;:"):      # an overlap fragment from the previous passage
                continue
            plain = u.startswith("|") is False
            if plain and len(u) < 60 and not re.search(r"[.:;!?]", u):     # headings are poor answers
                s *= 0.4
            s *= (1 + boost(u)) * (1.15 if n == 1 else 1.0 if n == 2 else 0.9)
            shown = clean_unit(u, h["text"], question)        # table rows are judged with their column labels, as they are shown
            ut = set(rag.tokens(shown))
            matched = sum(1 for t in known if t in ut)
            out.append((s, n, shown, u, _cover(index, qtoks, shown), rarest is None or rarest in ut, matched, len(known)))
    out.sort(key=lambda x: -x[0])
    return qt, out


REQUIRE_RAREST = False
MIN_MATCHED = 2            # the headline sentence must contain at least this many of the question's key terms
EXTRACT_MIN_COVER = 0.45     # the headline sentence must contain this share of the question's key terms (tuned in evaluate.py)


FULL_COVER_KEEP = 0.6       # a sentence holding the most of the question's terms may lead if it scores at least this share of the best score
LIST_ITEMS_MAX = 8
_LIST_LINE = re.compile(r"^\s*[-*]\s+")


# Prefer the best-covering candidate as the headline, unless it scores far below the overall top candidate.
def _prefer_full_cover(good):
    """The headline is the best-scoring sentence among those that hold the most of the question's terms, unless that sentence scores far below
    the overall best (a short table row from another section can outscore the sentence that actually restates the question)."""
    top = good[0][0]
    best_cover = max(c[4] for c in good)
    full = [c for c in good if c[4] >= best_cover - 0.02 and c[0] >= FULL_COVER_KEEP * top]
    if not full or full[0] is good[0]:
        return good
    return [full[0]] + [c for c in good if c is not full[0]]


# The list that directly follows the headline sentence in its own passage, if any.
def _list_items(index, hits, head):
    """The list that directly follows the headline in its passage. A document introduces a list either with a colon ("the conditions are:") or with
    a paragraph the list then answers (a worked example's "Situation." paragraph, followed by its steps), so the items belong with the headline
    either way."""
    _s, n, _text, raw = head
    if _LIST_LINE.match(raw):
        return []                       # the headline is itself one item of a list: the items around it are alternatives, not its continuation
    units = index._units(hits[n - 1]["text"])
    if raw not in units:
        return []
    out = []
    for u in units[units.index(raw) + 1:]:
        if not _LIST_LINE.match(u) or len(out) >= LIST_ITEMS_MAX:
            break
        out.append(f"- {clean_unit(u, hits[n - 1]['text'], '')} [{n}]")
    return out


# Build a verbatim (model-free) answer: a headline sentence, its following list if any, and supporting sentences.
def compose_extractive(index, question, hits, max_units=3):
    """Answer built from verbatim sentences: a headline sentence plus up to two supporting ones.
    Returns text="" when no sentence covers enough of the question (there is no direct answer)."""
    qt, cands = candidate_units(index, question, hits)
    good = [c for c in cands if c[4] >= EXTRACT_MIN_COVER and (c[5] or not REQUIRE_RAREST) and c[6] >= min(MIN_MATCHED, max(c[7], 1))]
    if not good:
        return {"text": "", "units": [], "qtype": qt, "top_score": 0.0}
    good = _prefer_full_cover(good)
    chosen, seen = [], []
    for s, n, text, raw, cov, has_rare, matched, nknown in good:
        toks = _tok(text)
        if any(len(toks & t) / max(len(toks | t), 1) > 0.6 for t in seen):
            continue
        chosen.append((s, n, text, raw))
        seen.append(toks)
        if len(chosen) >= max_units:
            break
    head = chosen[0]
    lines = [f"{head[2]} [{head[1]}]"]
    lines += _list_items(index, hits, head)
    for s, n, text, raw in chosen[1:]:
        if s >= 0.35 * head[0] and not any(text in ln for ln in lines):
            lines.append(f"- {text} [{n}]")
    return {"text": chr(10).join(lines), "units": [(n, t) for _, n, t, _r in chosen], "qtype": qt, "top_score": round(head[0], 3)}


# ------------------------------------------------------------------------------------------ verification
# Strip [S1]/[1]-style citation markers.
def _strip_cites(a):
    return re.sub(r"\[\s*S?\d+\s*\]", "", a)


# Check a model answer's numbers/acronyms/word-support/coherence against the excerpts it was given.
_CLAUSE = re.compile(r",|;|:|\band\b|\bbut\b|\bwhile\b|\bwhereas\b|\bso\b", re.I)
CLAUSE_SUPPORT = 0.7          # share of a clause's content words that one excerpt sentence or table row must hold


# Excerpt units a claim can rest on: table rows (a line with " | ") and sentences. -> [(token set, is_row)]
def _evidence_units(context):
    units = []
    for ln in context.split("\n"):
        ln = ln.strip()
        if not ln or re.match(r"^\[\d+\]\s*\(document:", ln):
            continue
        if ln.count("|") >= 2:
            units.append((_tok(ln), True))
        else:
            units.append((_tok(ln), False))
            for st in re.split(r"(?<=[.!?])\s+", ln):
                units.append((_tok(st), False))
    return [u for u in units if u[0]]


def verify_claims(answer, context, question=""):
    """Every clause of a model answer must rest on ONE sentence or table row of the excerpts, and clauses backed only by table rows must
    come from the same row. Catches an invented meaning ("the joint failed") and a fix from one row glued to a symptom from another."""
    units = _evidence_units(context)
    q_toks = _tok(question)
    for sent in re.split(r"(?<=[.!?])\s+", _strip_cites(answer)):
        row_sets = []
        for cl in _CLAUSE.split(sent):
            toks = {t for t in rag.tokens(cl) if t not in q_toks and t not in _STOPW and len(t) > 3 and not t.isdigit()}
            if len(toks) < 2:
                continue
            hit = {i for i, (u, _r) in enumerate(units) if len(toks & u) / len(toks) >= CLAUSE_SUPPORT}
            if not hit:
                return False, "not supported by any single excerpt sentence or row: " + cl.strip()[:60]
            if all(units[i][1] for i in hit):
                row_sets.append(hit)
        if len(row_sets) >= 2 and not set.intersection(*row_sets):
            return False, "mixes details from different table rows"
    return True, ""


def verify(answer, context, question="", claims=False):
    """Check a model answer against the excerpts it was given. Returns a dict of signals plus `ok`.
    claims=True also runs `verify_claims` (each clause must rest on one excerpt sentence or row)."""
    a = _strip_cites(answer).strip()
    ctx_norm = context.lower().translate(rag._SUBS)
    res = {"refusal": a.startswith(NOT_FOUND[:30]), "numbers_missing": [], "acronyms_missing": [], "support": 1.0,
           "coherence": 1.0, "ok": True, "reasons": []}
    if res["refusal"]:
        return res
    q_norm = question.lower().translate(rag._SUBS)
    for x in sorted(set(_NUM.findall(a.translate(rag._SUBS)))):
        if x not in ctx_norm and x.replace(",", ".") not in ctx_norm and x not in q_norm:
            res["numbers_missing"].append(x)
    for x in sorted(set(_ACR.findall(a))):
        if x.lower() not in ctx_norm and x.lower() not in q_norm:
            res["acronyms_missing"].append(x)
    q_toks = _tok(question)
    ctx_toks = _tok(context)
    a_toks = [t for t in rag.tokens(a) if t not in q_toks and t not in _STOPW and len(t) > 3 and not t.isdigit()]
    if a_toks:
        res["support"] = round(sum(t in ctx_toks for t in a_toks) / len(a_toks), 3)
        best = 0.0
        aset = set(a_toks)
        for ln in context.split("\n"):
            ut = _tok(ln)
            if ut:
                best = max(best, len(aset & ut) / len(aset))
        res["coherence"] = round(best, 3)
    if len(a) < 12:
        res["reasons"].append("answer too short")
    if res["numbers_missing"]:
        res["reasons"].append("numbers not in the excerpts: " + ", ".join(res["numbers_missing"][:5]))
    if res["acronyms_missing"]:
        res["reasons"].append("terms not in the excerpts: " + ", ".join(res["acronyms_missing"][:5]))
    if res["support"] < VERIFY_SUPPORT:
        res["reasons"].append(f"only {res['support']:.0%} of the answer's words appear in the excerpts")
    if res["coherence"] < VERIFY_COHERENCE:
        res["reasons"].append("answer does not match any single excerpt sentence closely")
    if claims:
        ok, why = verify_claims(a, context, question)
        if not ok:
            res["reasons"].append(why)
    res["ok"] = not res["reasons"]
    return res


VERIFY_SUPPORT = 0.80       # tuned in evaluate.py
VERIFY_COHERENCE = 0.45


AGREE_MIN = 0.2      # chosen offline on the dev half, confirmed on the test half (evaluate.py / eval report)


# Share of the model answer's content words that also appear in the best verbatim passage sentences.
def agreement(answer, extractive_text, question=""):
    """Share of the model answer's content words that also appear in the best verbatim passage sentences.
    A model answer that shares almost nothing with them (e.g. names a different standard) is not trusted."""
    qt = set(rag.tokens(question))
    a = {t for t in rag.tokens(_strip_cites(answer)) if t not in qt and not t.isdigit() and len(t) > 3}
    e = set(rag.tokens(extractive_text))
    return len(a & e) / len(a) if a else 1.0
