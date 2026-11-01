"""Understand the question: what is asked (intent), in which layout (format), how long, about what (entity), in how many parts.

Everything here is wording-based and cheap (a few milliseconds); the engine then retrieves evidence for each part.
"""
import re

from welding_ai.retrieval import rag

SYN = [                                    # wording the documents do not use -> wording they do
    (r"\blife[- ]?cycle\b", "cycle"), (r"\bservo[- ]?gun\b", "servo gun"), (r"\bspot[- ]?weld(er|ing)?\b", "spot weld"),
    (r"\bwps\b", "WPS"), (r"\bfaults?\b(?= codes?)", "fault"),
]
_FORMAT_WORDS = r"(?:in\s+)?(?:a\s+)?(?:tabular\s+format|table\s+format|tabular\s+form|table|tabular|tabulate[d]?|grid|matrix)\b"

RE_TABLE = re.compile(r"\b(tabular|tabulate[d]?|table|grid|matrix)\b", re.I)
RE_STEPS = re.compile(r"\b(step[- ]by[- ]step|steps|numbered|procedure)\b", re.I)
RE_LIST = re.compile(r"\b(bullet(?:s|ed)?|bullet points?|as a list|in a list|list (?:form|format)|point[- ]wise|points|pointers?)\b", re.I)
RE_BRIEF = re.compile(r"\b(brief(?:ly)?|short(?:ly)?|quick(?:ly)?|concise(?:ly)?|tl;?dr|one line|one sentence|in a nutshell|summar(?:y|ise|ize))\b", re.I)
RE_DETAIL = re.compile(r"\b(in detail|detailed|in depth|in-depth|thorough(?:ly)?|comprehensive(?:ly)?|elaborate|full explanation)\b", re.I)

RE_CONFLICT = re.compile(r"\b(?:do|does|are|is)\b[^?]{0,50}\b(?:documents?|docs|chapters|sources)\b[^?]{0,50}\b(?:different|differ|same|consistent|agree|vary)\b|\b(conflict\w*|contradict\w*|disagree\w*|inconsisten\w*|discrepanc\w*|differ(?:s|ent|ently)? (?:between|across|in) (?:the )?(?:docs|documents|chapters|files|sources))\b", re.I)
RE_COMPARE = re.compile(r"\b(compare|comparison|compared|versus|vs\.?|difference between|differences between|differ from|differs from|distinguish)\b", re.I)
RE_SITUATION = re.compile(r"\b(shows?|showing|displays?|displaying|displayed|reads?|reading|status (?:is|says)|state (?:is|says)|indicates?|indicating|is in|stuck (?:in|at)|currently|says)\b", re.I)
RE_MEANS = re.compile(r"\b(what does|what do|what is|mean|means|meaning|what should|what happens|what next|what now|is it (?:ok|normal|bad|fine))\b", re.I)
RE_DIAG = re.compile(r"\b(won'?t|doesn'?t|does not|do not|don'?t|not (?:start|work|weld|reach|firing|fire|moving|closing|opening|ready)|no weld|fail(?:s|ed|ing|ure)?|error|alarm|fault(?:s|y|ed)?|troubleshoot\w*|diagnos\w*|fix|stuck|trip(?:s|ped)?|splash|expulsion|porosity|root cause|cause(?:s|d)?|symptom\w*|defect\w*|what (?:do|should) i (?:check|do)|how (?:do|can|to) i? ?(?:fix|solve|resolve|diagnose|troubleshoot))\b", re.I)
RE_PROC = re.compile(r"\b(how (?:to|do i|do we|can i|should i|is it done)\b|steps? (?:to|for|of)|procedure (?:for|to)|walk me through|instructions? (?:for|to)|guide (?:me|to)|method (?:for|to))", re.I)
RE_COMPLY = re.compile(r"\b(standards?|requir\w*|compl(?:y|ies|iance|iant)|clause|mandatory|shall|audit\w*|qualif\w*|certif\w*|accept\w*)\b", re.I)
RE_EXPLAIN = re.compile(r"\b(explain|describe|overview|walk|tell me about|elaborate|summari[sz]e|what do you know about|discuss)\b", re.I)
RE_DEFINE = re.compile(r"^\s*(?:what(?:'s| is| are| was)|define|meaning of|who is)\b", re.I)
RE_PREMISE = re.compile(
    r"^\s*(?:why|how\s+come)\s+(?:does|do|did|is|are|must|should)\s+(.+?)\s+(?:(?:need|needs|require|requires|use|uses|involve|involves|rely\s+on|relies\s+on)\s+|(?:done|performed|carried\s+out|made|used)\s+(?:with|using)\s+|(?:have\s+to\s+)?be\s+(?:made|built)\s+(?:of|from|with)\s+)(?:a\s+|an\s+|the\s+)?(.+?)\s*\??\s*$", re.I)
RE_NAVQ = re.compile(r"\b(?:which|what)\s+(?:chapters?|documents?)\b|\bchapters?\s+\d+|\bhow many\s+(?:chapters?|volumes?|documents?)\b|\btable of contents\b|\ball\s+(?:the\s+)?chapters\b", re.I)
RE_DOCREF = re.compile(r"\b(document|doc|chapter|file|docx|section|whole|entire)\b", re.I)

_PARTSPLIT = re.compile(r"\s*(?:[,;?]|\band\b|\balso\b)\s*(?=(?:what|how|why|when|where|which|who|does|do|is|are|can|list|explain|give|show|tell|describe)\b)", re.I)
_PRON = {"it", "its", "this", "that", "they", "them", "their", "these", "those"}
_ENTSTOP = set("what is are the a an of and to how does do for in on why which who explain describe define tell me about give show list "
               "different types type kinds kind steps step table tabular format detail brief please in but doesn don won isn aren t s my me our we i cannot cant dont wont doesnt".split())
BROAD = set("material materials choosing choose choice selecting select selection best right good setting settings parameter parameters "
            "weld welding welds welder help recommend suitable recommended which should use using guide advice tips need".split())
# Words that say WHAT to return about the subject (meaning, action, cause ...) or HOW (pointers, explain), never the subject itself.
ASK_WORDS = ("meaning mean means signify interpret action actions fix fixes remedy remedies corrective resolve solve solution stop prevent avoid "
             "cause causes reason reasons explain pointer pointers point points bullet bullets taken place need needs needed happen happens next detail").split()
_ENTITY_STOP = set(ASK_WORDS) | {"in", "on", "it", "them", "this", "that", "do", "does", "i", "we", "you"}
# attribute asked about -> the question wording that asks it
ATTR_ASK = {"meaning": re.compile(r"\b(?:mean(?:s|ing)?|signif\w+|interpret\w*|what (?:is|does) (?:it|this) (?:tell|indicate))", re.I),
            "action": re.compile(r"\b(?:action|fix(?:es)?|remed\w+|corrective|resolv\w+|solv\w+|solution|prevent\w*|stop|avoid|what (?:should|do|can|must) (?:i|we|you|one) do|what to do|what next)", re.I),
            "cause": re.compile(r"\b(?:causes?|reasons?|root cause|why)", re.I),
            "detect": re.compile(r"\b(?:detect\w*|identif\w+|how (?:is|do you|to) (?:it )?(?:found|spotted|checked))", re.I)}
_ACRO = re.compile(r"\b[A-Z][A-Z0-9]{1,6}\b")
_ACRO_SKIP = {"AND", "THE", "FOR", "WHAT", "HOW", "WHY", "ARE", "IS", "OK", "PLZ"}


_DEFINITION_OF = re.compile(r"^\s*(?:what\s+is|what's|give me|tell me|state)\s+(?:the\s+)?(?:definition|meaning)\s+of\s+(?:a\s+|an\s+|the\s+)?(.+?)(?:\s+in\s+welding)?\s*\??\s*$", re.I)


RE_WRAPPER = re.compile(r"^(?:(?:hey|hi|hello|ok|okay|so)[,!.\s]+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|please\s+|pls\s+|i\s+(?:want|need|would\s+like)\s+to\s+know\s+|i\s+wanna\s+know\s+)", re.I)
RE_TELLME = re.compile(r"^(?:tell|show|give)\s+me\s+(?=(?:what|which|who|how|why|when|where|explain|describe|list|define|compare|summari[sz]e)\b|the\s+(?:definition|meaning)\b)", re.I)


# Strip citation markers/polite wrappers, canonicalize "definition of X" phrasing, and apply wording synonyms.
def normalise(q):
    q = re.sub(r"\[S?\d+(?:[,;]\s*S?\d+)*\]", " ", q)             # pasted citation markers such as [S1] or [3]
    q = " ".join(q.split())
    q = RE_TELLME.sub("", RE_WRAPPER.sub("", q)).strip() or q          # "please tell me what is X" and "I want to know what is X" are "what is X"
    m = _DEFINITION_OF.match(q)
    if m:
        q = "what is " + m.group(1)                       # "what is the definition of X" = "what is X"
    for pat, rep in SYN:
        q = re.sub(pat, rep, q, flags=re.I)
    return q


# Remove wording that asks for a layout (table/list/steps) or a length (brief/detailed), leaving the core question.
def _strip_format_words(q):
    q = re.sub(r"\b(?:please|pls|plz)\b", " ", q, flags=re.I)
    q = re.sub(r"\b(?:in|as|using)\s+(?:a\s+|an\s+)?(?:tabular(?:\s+(?:format|form))?|table(?:\s+(?:format|form))?|bullets?|list|steps?|"
               r"step[- ]by[- ]step|bullet points?)\b", " ", q, flags=re.I)
    q = re.sub(r"\b(?:tabular|tabulate[d]?|table format|in detail|briefly|in short|step[- ]by[- ]step)\b", " ", q, flags=re.I)
    return " ".join(q.split())


# The thing the question is about: a standard identifier, an acronym, or the noun phrase after "what is / explain".
def find_entity(q):
    """The thing the question is about: an acronym (WPS), a standard, or the noun phrase after 'what is / explain'."""
    m = re.search(r"\b(?:ISO|AWS|ASME|ASTM|IPC|EN|DIN|IEC|RWMA)\s?[A-Z]?\d[\dA-Za-z.\-/]*", q)
    if m:
        return m.group(0)
    acr = [a for a in _ACRO.findall(q) if a not in _ACRO_SKIP]
    if acr:
        return acr[0]
    m = re.search(r"\b(?:what(?:'s| is| are)|define|explain|describe|about|overview of)\s+(?:the\s+|a\s+|an\s+)?([A-Za-z][\w\- ]{1,40}?)(?=\s*(?:,|\?|$|\band\b|\bwhat\b|\bhow\b|\bdoes\b|\bis\b|\bare\b|\bwork\b|\bmean\b))", q, re.I)
    if m:
        ws = [w for w in m.group(1).split() if w.lower() not in _ENTSTOP and w.lower() not in _ENTITY_STOP]
        if ws:
            return " ".join(ws)
    return ""


_NOSEP = re.compile(r"(?<=[A-Za-z0-9])\s+(?=(?:what|how|why|when|where|which|who)\s+(?:is|are|does|do|did|can|should|would|about)\b)", re.I)


# Split a multi-part question ("what is X and how does Y work") into its separate sub-questions.
def split_parts(q):
    q = _NOSEP.sub(", ", q)
    q = re.sub(r"\s+\b(?:and|also)\s*,", ",", q)
    parts = [p.strip() for p in _PARTSPLIT.split(q) if p and p.strip()]
    return parts or [q]


# Stemmed index terms of a text without format / filler words.
def content_terms(text, drop=()):
    """Stemmed index terms of a text without format / filler words."""
    text = _strip_format_words(text)
    ws = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-/]*", text) if w.lower() not in _ENTSTOP and w.lower() not in _PRON]
    out = []
    for t in rag.tokens(" ".join(ws)):
        if t not in out and t not in drop:
            out.append(t)
    return out


# The main entry point: classify a question's intent, format, length, entity, sub-parts and vagueness/nav flags.
def analyze(question):
    q = normalise(question)
    fmt = None
    if RE_TABLE.search(q):
        fmt = "table"
    elif RE_LIST.search(q):
        fmt = "list"
    elif re.search(r"\b(step[- ]by[- ]step|steps|numbered)\b", q, re.I):
        fmt = "steps"
    length = "brief" if RE_BRIEF.search(q) else "detail" if RE_DETAIL.search(q) else None
    core = _strip_format_words(q)

    pm = RE_PREMISE.match(core)
    if pm:
        intent = "premise"
    elif RE_CONFLICT.search(q):
        intent = "conflict"
    elif RE_COMPARE.search(q):
        intent = "compare"
    elif RE_SITUATION.search(q) and RE_MEANS.search(q) and not re.search(r"^\s*(what is|what are)\b", q, re.I):
        intent = "situation"
    elif RE_DIAG.search(q) and not RE_DEFINE.match(core):
        intent = "diagnose"
    elif RE_PROC.search(q) and not RE_DEFINE.match(core):
        intent = "procedure"
    elif re.search(r"\b(different|various|types?|kinds?|categories|list|all|every|each|how many|options?|layers?|states?|stages|phases|levels?|modes?|classes)\b|\b(?:\d+|two|three|four|five|six|seven|eight|nine|ten)\s+(?:\w+\s+)?(?:different|types?|kinds?|layers?|states?|stages|steps|modes?|methods?|levels?)\b", q, re.I) and not RE_DEFINE.match(core.replace("are", "is", 1)) or re.match(r"^\s*(?:what|which)\s+are\b", core, re.I):
        intent = "enumerate"
    elif RE_COMPLY.search(q) and re.search(r"\b(require\w*|standard|clause|compl\w+|mandatory|shall)\b", q, re.I):
        intent = "comply"
    elif RE_EXPLAIN.search(q):
        intent = "explain"
    elif RE_DEFINE.match(core):
        intent = "define"
    else:
        intent = "topic"

    entity = find_entity(core)
    if entity and not (re.search(r"[A-Z0-9]", entity) and (entity.isupper() or re.search(r"\d", entity))) and intent not in ("define", "explain"):
        entity = ""          # a noun phrase is only trusted as the subject of a definition / explanation
    parts = split_parts(core) if intent in ("define", "topic", "explain") else [core]
    ent_toks = set(rag.tokens(entity)) if entity else set()
    bracket_def = None                                   # "what is resistance spot welding (RSW)": the words before the bracket are the entity too
    if entity:
        bracket_def = re.match(r"^\s*(?:what(?:'s| is| are)|define|explain|describe|tell me about|meaning of)\s+(?:the\s+|a\s+|an\s+)?([\w\- ]{2,60})\(\s*" + re.escape(entity) + r"\s*\)\s*\??\s*$", core, re.I)
        if bracket_def:
            ent_toks |= set(rag.tokens(bracket_def.group(1)))
    ask = {t for w in ASK_WORDS for t in rag.tokens(w)}
    plan_parts = []
    for p in parts:
        terms = content_terms(p)
        pterms = [t for t in terms if t not in ent_toks]
        if plan_parts and pterms and all(t in ask for t in pterms):       # "... and what is the meaning": says what to return about the previous part, not a new subject
            plan_parts[-1]["asks"] = list(dict.fromkeys(plan_parts[-1].get("asks", []) + pterms))
            continue
        plan_parts.append({"text": p, "terms": terms, "own": pterms})
    attrs = [k for k, rx in ATTR_ASK.items() if rx.search(q)]
    all_terms = content_terms(core)
    non_broad = [t for t in all_terms if t not in {rag.stem(b) for b in BROAD}]
    vague = (intent in ("topic", "define", "explain", "procedure") and not entity and len(non_broad) == 0 and bool(all_terms)) or \
            (intent == "topic" and not entity and len(all_terms) <= 1 and len(core.split()) <= 2 and False)
    pure = False
    if entity:
        pure = bool(re.match(r"^\s*(?:what(?:'s| is| are)|define|explain|describe|tell me about|meaning of)\s+(?:the\s+|a\s+|an\s+)?" + r"\W*".join(re.escape(w) for w in entity.split()) + r"\W*(?:\s+(?:briefly|in detail))?\s*\??\s*$", core, re.I))
    pure = pure or bool(bracket_def)
    nav = bool(RE_NAVQ.search(q))                        # a question about chapters / the catalogue, whatever else it names
    docref = bool(RE_DOCREF.search(q)) and intent in ("explain", "define", "topic", "enumerate")
    wants_price = bool(re.search(r"\b(price|prices|pricing|cost|costs|costing|cheap|cheaper|expensive|salary|salaries|wage|wages|how much)\b", q, re.I))
    return {"question": question, "q": q, "core": core, "intent": intent, "format": fmt, "length": length, "entity": entity,
            "parts": plan_parts, "terms": all_terms, "vague": vague, "docref": docref, "pure_define": pure, "wants_price": wants_price, "nav": nav, "attrs": attrs, "subject": [t for t in all_terms if t not in ask], "premise": (pm.group(1), pm.group(2)) if pm else None}


# Merge a short follow-up question's plan with the subject (terms/entity) of the question before it.
def with_context(plan, prev):
    """A short follow-up ("what are the groups") is read together with the question before it: the layout / intent
    stay the follow-up's own, the subject (terms, entity) is carried over from the previous question."""
    out = dict(plan)
    out["core"] = plan["core"] + " " + prev["core"]
    out["terms"] = list(dict.fromkeys(plan["terms"] + prev["terms"]))
    out["entity"] = plan["entity"] or prev["entity"]
    out["vague"] = False
    out["pure_define"] = False
    out["with_context"] = True
    return out
