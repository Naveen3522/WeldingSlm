"""The facts of all the documents, looked up by what they are about and what is asked of them.

    store = FactStore(units)
    store.lookup(entity_tokens, labels, topics)  -> [Match], best first

`entity_tokens` are the index terms of the thing asked about ("expulsion"); `labels` are the facet labels the question asks for ("failure consequence", "symptom if wrong");
`topics` {source: score} are the documents the question names. A fact matches when its subject (the first cell of the row, the title of the card) holds the entity, or, for a
question about a standard or a limit where the subject is in the text rather than the key, when its row names it. Only the asked cells come back, never the whole table.
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field

from welding_ai.retrieval import rag
from welding_ai.retrieval.roles import is_nav_title

MIN_TEXT_COVER = 0.85        # how much of a longer entity the whole row must name when its subject does not
PER_DOCUMENT = 2
LIMIT = 4
CORE_SHARE = 0.8            # the core of the subject: its rarest word and the words nearly as rare must be in the row's own subject
RARE_SHARE = 0.5            # words at least this rare (relative to the rarest, and at least MIN_RARE) must be somewhere in the fact
MIN_RARE = 2.5
KEEP = 0.7                   # a match scoring below this share of the best one is left out


@dataclass
class Match:
    unit: object
    cells: list = field(default_factory=list)          # [(label, value)] the asked attributes
    key_cover: float = 0.0
    text_cover: float = 0.0
    topic: float = 0.0
    score: float = 0.0


class FactStore:
    def __init__(self, units, sections=None):
        nav = {s["path"] for s in (sections or []) if is_nav_title(s["title"])}
        self.units = [u for u in units if u.section not in nav]
        self.by_label = defaultdict(list)
        self.key_tokens, self.text_tokens = [], []
        df = defaultdict(int)
        for i, u in enumerate(self.units):
            for label, _v in u.attrs:
                self.by_label[label].append(i)
            key = frozenset(rag.tokens(u.entity))
            text = key | frozenset(rag.tokens(u.title + " " + " ".join(v[:500] for _l, v in u.attrs)))
            self.key_tokens.append(key)
            self.text_tokens.append(text)
            for t in text:
                df[t] += 1
        n = max(len(self.units), 1)
        self.idf = defaultdict(lambda: math.log(1 + n / 0.5), {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()})

    def _cover(self, tokens, have):
        total = sum(self.idf[t] for t in tokens)
        return sum(self.idf[t] for t in tokens if t in have) / total if total else 0.0

    def rank(self, entity_tokens, labels, topics=None, restrict=False, reverse=False):
        """-> every Match for the entity that holds one of the labels, best first. `restrict`: only the documents named in `topics` (the question names a process).
        `reverse`: the subject may be named by the row's text instead of its first cell (a question about which standard covers something)."""
        entity = [t for t in dict.fromkeys(entity_tokens) if not t.isdigit() or len(t) > 1]
        wanted = set(labels)
        topics = topics or {}
        if not entity or not wanted:
            return []
        top = max(self.idf[t] for t in entity)
        core = [t for t in entity if self.idf[t] >= CORE_SHARE * top]
        rare = [t for t in entity if self.idf[t] >= max(MIN_RARE, RARE_SHARE * top)]
        seen, found = set(), []
        for label in wanted:
            for i in self.by_label.get(label, ()):
                if i in seen:
                    continue
                seen.add(i)
                u = self.units[i]
                if restrict and u.source not in topics:
                    continue
                if not all(t in self.text_tokens[i] for t in rare):
                    continue                                                      # the fact does not mention a rare word of the subject: it is about something else
                key = self._cover(entity, self.key_tokens[i])
                text = self._cover(entity, self.text_tokens[i])
                if not all(t in self.key_tokens[i] for t in core) and not (reverse and len(entity) >= 2 and text >= MIN_TEXT_COVER):
                    continue                                                      # the core of the subject is not what this row is about
                cells = [(lab, v) for lab, v in u.attrs if lab in wanted]
                topic = topics.get(u.source, 0.0)
                found.append(Match(u, cells, key, text, topic, key + 0.4 * text + 0.5 * topic + 0.02 * min(len(cells), 3) - 0.0005 * len(u.entity)))
        found.sort(key=lambda m: -m.score)
        return [m for m in found if m.score >= KEEP * found[0].score]

    def select(self, ranked, limit=LIMIT, per_document=PER_DOCUMENT):
        """The matches to show: the best ones, at most `per_document` from one document, none that says the same twice."""
        out, per, values = [], defaultdict(int), set()
        for m in ranked:
            sig = (m.unit.entity.lower(), tuple(v[:80] for _l, v in m.cells))
            if len(out) >= limit:
                break
            if per[m.unit.source] >= per_document or sig in values:
                continue
            per[m.unit.source] += 1
            values.add(sig)
            out.append(m)
        return out

    def lookup(self, entity_tokens, labels, topics=None, limit=LIMIT, restrict=False):
        """-> [Match] for the facts about the entity that hold one of the labels, best first, at most PER_DOCUMENT from one document."""
        return self.select(self.rank(entity_tokens, labels, topics, restrict), limit)
