"""Reads a question as a set of asks: what is asked about (the entity) and what is wanted of it (the facets).

"Why does expulsion happen in spot welding, what are its consequences and which standard governs spot weld acceptance?" is three asks:
    expulsion            -> why it happens        (a column such as "cause" / "why it happens")
    expulsion            -> its consequences      (a column such as "failure consequence" / "symptom if wrong")
    spot weld acceptance -> which standard governs (a column such as "governing standard", "what it governs")

Nothing in here lists question wordings. A word is a facet word when the documents use it to name a column or a label in many documents and far more often than in the
names of the things those columns describe ("consequence", "detected", "function"); every other word of the question is part of what is asked about ("expulsion",
"welding current"). The words of a facet are then matched to the documents' own labels by meaning (knowledge/facets.py).

    parse(question, lexicon, facets, topics, context=None) -> [Ask]
"""
import re
from dataclasses import dataclass, field

from welding_ai.knowledge.facets import CONCEPT_PREFIX
from welding_ai.retrieval import rag

INTERROGATIVE = r"(?:what|which|who|whom|whose|how|why|when|where|is|are|does|do|did|can|could|should|will|would|list|explain|give|show|tell|describe)"
BOUNDARY = re.compile(r"\s*(?:\?|;|\.|,|\band\b|\balso\b|\bplus\b|\bas\s+well\s+as\b)\s*(?=" + INTERROGATIVE + r"\b)", re.I)
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-/]*|,")
WORD = re.compile(r"[A-Za-z][A-Za-z\-]+")
MIN_SYMPTOM_IDF = 2.5            # ... and is not an everyday word of the documents ("weld", "steel")
MIN_SYMPTOM_UNITS = 3          # a word that names at least this many defects and faults in the documents is taken for the problem described
CONJUNCTIONS = {"and", "or", "plus"}
PRONOUNS = {"it", "its", "this", "that", "they", "them", "their", "these", "those", "such", "which"}
POSSESSIVE = re.compile(r"^(?P<lead>.*?)\b(?:its|their)\s+(?P<rest>.+?)\s*$", re.I)
REFERS = re.compile(r"\b(?:its|their|it|this|that|they|them|these|those)\b", re.I)
PROBLEM = re.compile(r"\b(?:i|we)(?:'m|'re|\s+am|\s+are)?\s+(?:get|getting|got|have|having|see|seeing|find|finding|keep\s+getting)\b|\bmy\b|\bour\b|\bkeeps?\s+happening\b|"
                     r"\bwhat\s+(?:should|can|must)\s+(?:i|we)\s+(?:do|check|change)\b|\bwhat\s+to\s+(?:do|check)\b|\bhow\s+(?:do|can|should)\s+(?:i|we)\s+(?:fix|solve|stop|avoid|prevent|get\s+rid\s+of)\b|"
                     r"\btroubleshoot\w*|\bdiagnos\w+", re.I)
IMPLICIT = ("cause", "correction", "detection")          # what someone who describes a problem wants to know about it
MAX_CLAUSES = 5
WIDER_MARGIN = 0.12          # a second, wider set of labels for a facet the documents word differently ("why it happens" -> "cause")
WIDER_MIN = 0.62


@dataclass
class FacetAsk:
    phrase: str                  # the words of the question that name it ("what are the consequences")
    labels: list                 # the labels in the documents that mean it
    wider: list                  # more labels that nearly mean it, tried when the first set holds nothing about the entity
    sim: float
    task: str
    concept: str = ""


@dataclass
class Ask:
    text: str
    facets: list = field(default_factory=list)
    entity_words: list = field(default_factory=list)
    entity_tokens: list = field(default_factory=list)
    topics: dict = field(default_factory=dict)       # {source: score}
    carried: bool = False                            # the subject was taken from the ask before ("its consequences")
    subject: str = ""                                 # the thing asked about as written, in order ("expulsion in spot welding")
    implicit: bool = False                           # the facets were not asked in words: the question describes a problem, which asks for its cause, correction and detection
    problem: bool = False                            # the question describes a problem ("I get porosity ..."): a troubleshooting question even when it names one facet

    @property
    def resolved(self):
        """The ask as a complete question: "what are its consequences" -> "what are the consequences of expulsion spot welding"."""
        if not self.carried:
            return self.text
        subject = self.subject or " ".join(self.entity_words)
        possessive = POSSESSIVE.match(self.text)
        if possessive:
            return f"{possessive.group('lead')}the {possessive.group('rest')} of {subject}"
        swapped = REFERS.sub(subject, self.text, count=1)
        return swapped if swapped != self.text else f"{self.text} for {subject}"


class Lexicon:
    """How the documents use words: to name a kind of information (a facet word) or a thing."""

    def __init__(self, facets, store):
        self.facets, self.store, self._symptoms = facets, store, None
        self.key_units = {}
        for key in store.key_tokens:
            for t in key:
                self.key_units[t] = self.key_units.get(t, 0) + 1

    def is_facet(self, w):
        names = max((self.key_units.get(t, 0) for t in rag.tokens(w)), default=0)
        return self.facets.is_facet_word(w, names)

    def symptoms(self):
        """{token: how many units about a problem (those with a cause, correction or detection) have it in their subject}: the words that name defects and faults."""
        if self._symptoms is None:
            wanted = set(IMPLICIT)
            counts = {}
            for u, key in zip(self.store.units, self.store.key_tokens):
                if any(self.facets.concept_of(label) in wanted for label, _v in u.attrs):
                    for t in key:
                        counts[t] = counts.get(t, 0) + 1
            self._symptoms = counts
        return self._symptoms


def clauses(question):
    """The separate questions of a question, in order. A list of facets after one subject ("the causes, detection and corrective action of porosity") stays one."""
    text = re.sub(r"\s+", " ", question).strip()
    parts = [p.strip(" ,;?") for p in BOUNDARY.split(text) if p and p.strip(" ,;?")]
    return parts[:MAX_CLAUSES] or [text.strip(" ?")]


def _classify(clause, lex):
    """-> [(word, kind)] with kind "stop", "facet", "entity" or "sep" (a comma, "and", "or")."""
    out = []
    for w in TOKEN.findall(clause):
        lw = w.lower()
        if w == "," or lw in CONJUNCTIONS:
            out.append((w, "sep"))
        elif lw in PRONOUNS or lw in rag.STOP:
            out.append((w, "stop"))
        elif lex.is_facet(lw):
            out.append((w, "facet"))
        else:
            out.append((w, "entity"))
    return out


def _subject(classified):
    """The thing asked about as it was written: from its first word to its last, without the words that name the facet and without commas."""
    spots = [i for i, (_w, kind) in enumerate(classified) if kind == "entity"]
    if not spots:
        return ""
    return " ".join(w for w, kind in classified[spots[0]:spots[-1] + 1] if kind in ("entity", "stop"))


def _phrases(classified):
    """The facet phrases of a clause: [(whole phrase, only its facet words)], cut at "and", "or" and commas. An entity word in the middle ("why does <expulsion> happen") is left out."""
    phrases, current = [], []

    def flush():
        if any(kind == "facet" for _w, kind in current):
            phrases.append((" ".join(w for w, _k in current), " ".join(w for w, k in current if k == "facet")))
        current.clear()

    for w, kind in classified:
        if kind == "sep":
            flush()
        elif kind != "entity":
            current.append((w, kind))
    flush()
    return phrases


def _facet_asks(phrases, facets):
    """The facets the phrases name: each phrase is matched whole and by its facet words alone, and the better match counts."""
    flat = [x for pair in phrases for x in pair]
    hits = facets.match(flat, k=10) if flat else []
    out = []
    for i, (full, content) in enumerate(phrases):
        options = [facets.accepted(hits[2 * i]), facets.accepted(hits[2 * i + 1])]
        best = max(options, key=lambda h: h[0].sim if h else 0.0)
        if not best:
            continue
        same = facets.same_facet(best)
        labels = sorted({l for h in same for l in facets.family(h.label)} | {h.label for h in same if not h.label.startswith(CONCEPT_PREFIX)})
        wider = [h.label for h in best if h.sim >= best[0].sim - WIDER_MARGIN and h.sim >= WIDER_MIN and h.label not in labels and not h.label.startswith(CONCEPT_PREFIX)]
        out.append(FacetAsk(full, labels, wider, best[0].sim, facets.task_of(best[0].label), facets.concept_of(best[0].label) or best[0].label))
    return out


def parse(question, lex, facets, topics, context=None):
    """-> [Ask]. `context` is the last ask of the question before this one in the conversation, for a follow-up that says "its consequences"."""
    asks, previous = [], context
    for clause in clauses(question):
        classified = _classify(clause, lex)
        found = topics.detect(clause)
        topic_words = {w.lower() for src in found for w in topics.words_of(src, clause)}
        entity_words = [w for w, kind in classified if kind == "entity" and w.lower() not in topic_words]
        ask = Ask(clause, entity_words=entity_words, entity_tokens=[t for t in rag.tokens(" ".join(entity_words)) if not t.isdigit() or len(t) > 1], topics=found)
        ask.subject = _subject(classified)
        ask.facets = _facet_asks(_phrases(classified), facets)
        if PROBLEM.search(clause):                                     # "I'm getting expulsion on 1.2 mm galvanised steel spot welds": the problem is what is asked about, the rest is context
            head = _head(clause, lex)
            if head is not None:
                ask.entity_words, ask.entity_tokens, ask.subject = [head[0]], [head[1]], head[0]
        points_back = bool(REFERS.search(clause))
        if previous is not None and previous.entity_tokens and (points_back or (ask.facets and not ask.entity_tokens)):
            ask.entity_words, ask.entity_tokens, ask.carried, ask.subject = previous.entity_words, previous.entity_tokens, True, previous.subject      # "it" is what was just asked about
            ask.topics = ask.topics or previous.topics
        if ask.entity_tokens:
            previous = ask
        asks.append(ask)
    for i, ask in enumerate(asks):                      # "Tell me why it happens and how to find it for X": the subject comes after the asks that need it
        if ask.facets and not ask.entity_tokens:
            after = next((a for a in asks[i + 1:] if a.entity_tokens and not a.carried), None)
            if after is not None:
                ask.entity_words, ask.entity_tokens, ask.carried, ask.subject = after.entity_words, after.entity_tokens, True, after.subject
                ask.topics = ask.topics or after.topics
    if PROBLEM.search(question):
        merged = _problem(question, asks, lex, facets, topics)
        if merged is not None:
            return [merged]
    return asks


def _head(text, lex):
    """(word, token) of the problem a text describes: of the words the documents name defects and faults by, the commonest among those that are not everyday words of the documents."""
    counts, best = lex.symptoms(), None
    for word in dict.fromkeys(WORD.findall(text)):
        tokens = rag.tokens(word)
        if not tokens or lex.store.idf[tokens[0]] < MIN_SYMPTOM_IDF:
            continue
        n = counts.get(tokens[0], 0)
        if n < MIN_SYMPTOM_UNITS:
            continue
        share = n / max(lex.key_units.get(tokens[0], n), n)                 # how much of what the documents name with this word is a problem: "expulsion" always, "steel" rarely
        if best is None or n * share ** 2 > best[0]:
            best = (n * share ** 2, word, tokens[0])
    return (best[1], best[2]) if best else None


def _problem(question, asks, lex, facets, topics):
    """A question that describes a problem ("I get porosity when TIG welding 2 mm stainless steel. What should I do?", "My welds show spatter, how do I fix it?") is one ask about that problem.
    What it asks for is named when it says so ("how do I fix it" asks for the correction) and otherwise is its cause, correction and detection. The problem is the word the documents most often name
    defects and faults by; the rest (process, material, thickness) is context."""
    if sum(1 for a in asks if a.entity_tokens and not a.carried and not a.implicit) > 1 and not all(not a.facets for a in asks):
        return None                                                  # several different things are asked about: not one problem
    head = _head(question, lex)
    if head is None:
        return None
    named = {f.concept or f.phrase: f for a in asks for f in a.facets}
    ask = Ask(re.sub(r"\s+", " ", question).strip(" ?"), entity_words=[head[0]], entity_tokens=[head[1]], topics=topics.detect(question), subject=head[0], implicit=not named, problem=True)
    ask.facets = list(named.values()) or [f for f in (facets.concept_facet(c) for c in IMPLICIT) if f is not None]
    return ask if ask.facets else None
