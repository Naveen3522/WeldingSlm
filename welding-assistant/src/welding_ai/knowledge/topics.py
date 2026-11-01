"""What each document is about, from its own title: "Chapter 39 - Gas Metal Arc Welding (GMAW / MIG / MAG)" is the topic "Gas Metal Arc Welding" with the names GMAW, MIG and MAG.
A question that names a topic is looked up in that document first. Nothing is listed by hand: a new chapter brings its own title.

    topics = Topics(sections)
    topics.detect("porosity in GTAW")      -> {"41_GTAW_Gas_Tungsten_Arc_Welding.docx": 1.0}
    topics.name(source)                    -> "Gas Tungsten Arc Welding"
"""
import re
from collections import Counter

from welding_ai.retrieval import rag

CHAPTER = re.compile(r"^(?:chapter\s+\d+\s*[—–:\-]\s*)", re.I)
DISTINCTIVE_DF = 6          # a word in more document titles than this ("welding") does not identify a topic
MIN_COVER = 0.6


def _title_of(sections):
    first = {}
    for s in sections:
        first.setdefault(s["source"], s["title"] if s["level"] == 1 else None)
        if first[s["source"]] is None and s["level"] == 1:
            first[s["source"]] = s["title"]
    return first


class Topics:
    def __init__(self, sections):
        self._name, self._aliases, self._tokens = {}, {}, {}
        for source, title in _title_of(sections).items():
            title = CHAPTER.sub("", title or re.sub(r"^\d+_", "", source.rsplit(".", 1)[0]).replace("_", " ")).strip()
            alias = re.findall(r"\(([^)]*)\)", title)
            name = re.sub(r"\s*\([^)]*\)", "", title).strip()
            self._name[source] = name
            self._aliases[source] = {a.strip().lower() for chunk in alias for a in re.split(r"[/,]", chunk) if 2 <= len(a.strip()) <= 12}
            self._tokens[source] = frozenset(rag.tokens(name))
        df = Counter(t for toks in self._tokens.values() for t in toks)
        self._distinct = {s: frozenset(t for t in toks if df[t] <= DISTINCTIVE_DF) for s, toks in self._tokens.items()}

    def name(self, source):
        return self._name.get(source, re.sub(r"^\d+_", "", source.rsplit(".", 1)[0]).replace("_", " "))

    def detect(self, text):
        """-> {source: score} for the documents whose topic the text names (an alias such as GMAW, or most of the title's distinctive words)."""
        words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]*", text)}
        tokens = set(rag.tokens(text))
        found = {}
        for source in self._name:
            if self._aliases[source] & words:
                found[source] = 1.0
                continue
            distinct = self._distinct[source]
            if len(distinct) >= 1:
                cover = len(distinct & tokens) / len(distinct)
                if cover >= MIN_COVER:
                    found[source] = cover
        return found

    def words_of(self, source, text):
        """The words of `text` that name this topic: its aliases and the distinctive words of its title (never "welding", "metal" and the like, which are also part of what is asked)."""
        distinct = self._distinct.get(source, frozenset())
        names = {a.lower() for a in self._aliases.get(source, ())}
        return {w for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]*", text) if w.lower() in names or (set(rag.tokens(w)) & distinct)}
