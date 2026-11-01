"""Answers a question that asks several things of the documents, each from the row, card or line that says it.

A question such as "Why does expulsion happen, what are its consequences and which standard governs spot weld acceptance?" is three asks. Each ask names a thing and what is wanted of it
(understanding/asks.py); the documents' own facts (knowledge/units.py) are looked up for exactly that: the cause cell of the expulsion row, the consequence cell of the expulsion row,
the standard whose row names spot weld acceptance. What comes back is the cell itself, quoted, with the passage it is in. An ask the facts cannot answer is given to the ordinary
pipeline on its own, so nothing the pipeline could answer before is lost, and an ask nothing answers is said not to be covered, never guessed.

    answerer = FactAnswerer.build(sections, vectors)
    seen = answerer.understand(question, history)         # -> Understanding(asks, found, takes_over)
    answerer.events(ctx, seen)                            # -> a generator of the events of the answer
"""
import json
import re
from dataclasses import dataclass, field

from welding_ai import paths
from welding_ai.core import events
from welding_ai.knowledge import units as units_mod
from welding_ai.knowledge.embed import TextVectors
from welding_ai.knowledge.facets import TASK_NAMES, Facets
from welding_ai.knowledge.store import FactStore
from welding_ai.knowledge.topics import Topics
from welding_ai.retrieval import rag
from welding_ai.understanding import asks as asks_mod

SHOWN = 2                       # facts shown for one ask and facet; the rest are named
MAX_CHARS = 620
STRONG_TOPIC = 0.9
MIN_ABOUT = 0.5                 # how much of the subject (idf weighted, besides its rarest words) a pipeline answer must name to count as an answer about it
CORE_SHARE = 0.8                # the rarest words of the subject, and the ones nearly as rare, must all be named
REVERSE = {"authority", "selection"}        # asks whose subject is named by the row's text rather than its first cell ("which standard covers spot weld acceptance")
REFUSALS = ("not_found", "no_direct_answer", "clarify", "intent", "guard_empty", "guard_injection", "llm_refusal", "relation")
CITE = re.compile(r"\[(\d+)\]")
MARKS = re.compile(r"\*\*|\[\d+\]")           # bold marks and [n] markers, left out of the text the AI note reads
NL = chr(10)


@dataclass
class Looked:
    ask: object
    facet: object
    matches: list = field(default_factory=list)
    others: list = field(default_factory=list)          # topic names with the same fact that are not shown


@dataclass
class Understanding:
    asks: list
    looked: list                    # [[Looked]] for each ask
    takes_over: bool
    tasks: list

    def plan(self):
        return events.plan(self.tasks, [{"text": a.text, "entity": a.subject or " ".join(a.entity_words), "carried": a.carried,
                                         "facets": [{"phrase": f.phrase, "label": f.concept or (f.labels[0] if f.labels else ""), "task": TASK_NAMES.get(f.task, f.task)} for f in a.facets]} for a in self.asks])


class _Material:
    """What the AI note reads: the facts as plain text."""

    def __init__(self, text):
        self.text = text

    def plain(self):
        return self.text


def _clip(text, limit=MAX_CHARS):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = max(text.rfind(". ", 0, limit), text.rfind("; ", 0, limit))
    return (text[:cut + 1] if cut > limit * 0.5 else text[:limit].rsplit(" ", 1)[0]) + " ..."


def _title(label):
    return label[:1].upper() + label[1:]


def _entity_title(ask, looked=None):
    """What a part of the answer is about, as the question put it ("Expulsion in spot welding")."""
    text = ask.subject or " ".join(ask.entity_words)
    return text[:1].upper() + text[1:]


class FactAnswerer:
    def __init__(self, sections, vectors=None):
        self.sections = sections
        self.units = units_mod.extract(sections)
        self.facets = Facets(self.units, vectors)
        self.store = FactStore(self.units, sections)
        self.topics = Topics(sections)
        self.lexicon = asks_mod.Lexicon(self.facets, self.store)

    @classmethod
    def build(cls, sections=None, index_dir=None, vectors=True):
        index_dir = index_dir or paths.INDEX_DIR
        sections = sections or json.loads((index_dir / "sections.json").read_text(encoding="utf-8"))
        return cls(sections, TextVectors(index_dir) if vectors else None)

    def prepare(self, log=None):
        """Index time: embed the facet labels. -> how many were new, or None when the embedding model is not running."""
        return self.facets.prepare(log)

    # ------------------------------------------------------------------ understanding
    def _previous(self, history):
        last = next((m["content"] for m in reversed(history or []) if m.get("role") == "user" and m.get("content")), "")
        if not last:
            return None
        parsed = asks_mod.parse(last, self.lexicon, self.facets, self.topics)
        return next((a for a in reversed(parsed) if a.entity_tokens), None)

    def _lookup(self, ask, facet):
        strong = ask.topics and max(ask.topics.values()) >= STRONG_TOPIC
        reverse = facet.concept in REVERSE
        ranked = self.store.rank(ask.entity_tokens, facet.labels, ask.topics, restrict=bool(strong), reverse=reverse) if ask.entity_tokens else []
        if not ranked and facet.wider and ask.entity_tokens:
            ranked = self.store.rank(ask.entity_tokens, facet.labels + facet.wider, ask.topics, restrict=bool(strong), reverse=reverse)
        if not ranked and strong and ask.implicit and ask.entity_tokens:                  # a described problem: the named process may not list it, the other chapters do
            ranked = self.store.rank(ask.entity_tokens, facet.labels, ask.topics, restrict=False, reverse=reverse)
        shown = self.store.select(ranked, SHOWN)
        hidden = []
        for m in ranked:
            name = self.topics.name(m.unit.source)
            if m not in shown and m.unit.source not in {x.unit.source for x in shown} and name not in hidden:
                hidden.append(name)
        return Looked(ask, facet, shown, hidden[:6])

    def _faq_covers(self, faq, question, parsed):
        """True when a prepared, verified answer is really the answer to the whole question: its own question names the thing asked about and every facet asked of it."""
        if faq is None:
            return False
        matcher, faqs = faq
        m = matcher.match(question)
        if not matcher.accept(m):
            return False
        entry = faqs[m["idx"]]
        have = set(rag.tokens(" ".join([entry["question"]] + list(entry.get("aliases", [])))))
        for ask in parsed:
            for facet in ask.facets:
                words = [w for w in facet.phrase.split() if self.lexicon.is_facet(w.lower())]
                if words and not any(t in have for w in words for t in rag.tokens(w)):
                    return False
            if ask.entity_tokens and sum(t in have for t in ask.entity_tokens) / len(ask.entity_tokens) < 0.7:
                return False
        return True

    def understand(self, question, history=None, faq=None):
        """`faq`: (the FAQ matcher, the FAQ list), so a prepared answer that really covers the whole question is left to answer it."""
        parsed = asks_mod.parse(question, self.lexicon, self.facets, self.topics)
        if history and parsed and parsed[0].facets and not parsed[0].entity_tokens:
            parsed = asks_mod.parse(question, self.lexicon, self.facets, self.topics, self._previous(history))
        looked = [[self._lookup(a, f) for f in a.facets] for a in parsed]
        found = sum(1 for group in looked for lk in group if lk.matches)
        lookups = sum(len(group) for group in looked)
        several = len(parsed) >= 2 and any(a.facets for a in parsed)       # a question that asks several things, at least one of them a facet of something, is answered one ask at a time
        takes = ((found >= 1 and (len(parsed) >= 2 or lookups >= 2 or any(a.problem for a in parsed))) or several) and not self._faq_covers(faq, question, parsed)
        tasks = list(dict.fromkeys(TASK_NAMES.get(f.task, f.task) for a in parsed for f in a.facets if f.task != "reference"))
        return Understanding(parsed, looked, takes, tasks)

    # ------------------------------------------------------------------ the answer
    def claim(self, ctx):
        """-> the events of the answer when the question holds two or more asks the facts can answer, else None (the normal pipeline carries on)."""
        if ctx.nested or ctx.via:
            return None
        seen = self.understand(ctx.q, ctx.history, faq=(ctx.h.faq, ctx.h.faqs) if ctx.h.faq else None)
        return self.events(ctx, seen) if seen.takes_over else None

    def events(self, ctx, seen):
        """The events of the answer: its plan, a step for each ask, then the facts (and an AI note when asked)."""
        h = ctx.h
        yield seen.plan()
        yield events.stage("asks", "Understood the question", " | ".join(self._describe(a, lk) for a, lk in zip(seen.asks, seen.looked)))
        hits, order = [], {}                                         # the passages cited, in the order they are first used

        def cite(chunk):
            if chunk is None or not 0 <= chunk < len(h.index.chunks):
                return ""
            if chunk not in order:
                order[chunk] = len(hits) + 1
                hits.append(h._hit(chunk, 1.0))
            return f" [{order[chunk]}]"

        sections, plain, any_found = [], [], False
        for i, (ask, group) in enumerate(zip(seen.asks, seen.looked)):
            found = [lk for lk in group if lk.matches]
            if found:
                yield events.stage(f"ask{i}", "Looking up " + _entity_title(ask, found).lower(), ", ".join(_title(lk.matches[0].cells[0][0]) for lk in found))
                key = tuple(ask.entity_tokens)
                if sections and sections[-1]["key"] == key:                  # the same thing asked about again ("... and what are its consequences")
                    section = sections[-1]
                else:
                    section = {"key": key, "title": _entity_title(ask, found), "blocks": []}
                    sections.append(section)
                lines = []
                for lk in group:
                    lines += self._lines(lk, cite)
                section["blocks"].append(("md", NL.join(lines)))
                plain.append(section["title"] + NL + NL.join(MARKS.sub("", x) for x in lines))
                any_found = True
                continue
            yield events.stage(f"ask{i}", "Searching the documents", ask.resolved)
            blocks, mine, relevant = self._nested(ctx, ask)
            title = ask.resolved.rstrip("?").strip()
            title = title[:1].upper() + title[1:] + "?"
            if not blocks or not relevant:
                near = "".join(cite(c) for c in mine[:2])
                sections.append({"key": None, "title": title, "blocks": [("md", "The documents do not state this directly." + (f" The closest passages:{near}." if near else ""))]})
                continue
            fixed = []
            for kind, content in blocks:
                if kind == "md":
                    fixed.append(("md", CITE.sub(lambda m: cite(mine[int(m.group(1)) - 1]) if 0 < int(m.group(1)) <= len(mine) else "", content)))
                    plain.append(MARKS.sub("", content))
                else:
                    fixed.append(("table", {**content, "rows": [[CITE.sub("", c) for c in row] for row in content["rows"]]}))
                    plain.append(" | ".join(content["header"]) + NL + NL.join(" | ".join(r) for r in content["rows"][:6]))
            for chunk in mine[:1]:
                cite(chunk)                                                 # a table carries no [n]: its passage is still a source
            sections.append({"key": None, "title": title, "blocks": fixed})

        yield events.sources(hits, len(hits), [], "good" if any_found else "weak", [], "facts", qtype="facts")
        for section in sections:
            yield events.token("## " + section["title"] + NL)
            for kind, content in section["blocks"]:
                if kind == "md":
                    yield events.token(content + NL)
                else:
                    yield events.table(content.get("title", ""), content.get("source", ""), content.get("chunk"), content["header"], content["rows"], content.get("note", ""))
            yield events.token(NL)
        yield events.cite([{"source": x["source"], "heading": x["heading"], "chunk": x["chunk_id"]} for x in hits])
        if ctx.mode == "answer" and any_found:
            yield from h.responders._ai_note(_Material(NL + NL.join(plain)), ctx.q, ctx.model, ctx.T)
        yield ctx.done(route="facts", grounded=True, confidence="high", match="good", answer_source="documents", tasks=seen.tasks, asks=len(seen.asks))

    def _describe(self, ask, group):
        what = " and ".join(_title(f.concept or f.labels[0]) for f in ask.facets) or "answer"
        return f"{' '.join(ask.entity_words) or ask.text}: {what}"

    def _lines(self, lk, cite):
        out = []
        if not lk.matches:
            return [f"- {_title(lk.facet.concept or lk.facet.labels[0])}: the documents do not state this for {' '.join(lk.ask.entity_words)}."]
        for m in lk.matches:
            where = f"{self.topics.name(m.unit.source)}, {m.unit.title}"
            subject = "" if m.unit.entity.lower() == (lk.ask.subject or "").lower() else f"{m.unit.entity}: "
            for label, value in m.cells[:3]:
                out.append(f"- **{_title(label)}** ({where}): {subject}{_clip(value)}{cite(m.unit.chunk)}")
        if lk.others:
            out.append(f"  Also described in: {', '.join(lk.others)}. Name the process to see its own row.")
        return out

    def _nested(self, ctx, ask):
        """The ordinary pipeline's answer to one ask: ([("md", text) | ("table", event)], [chunk of source n], is it really about the subject?).
        An answer that does not name the subject's words is not used: it is some other passage that happened to share a word."""
        evs = [e for e in ctx.h.stream(ask.resolved, history=[], model=ctx.model, mode="verbatim", k=ctx.k, nested=True) if e["type"] != "stage"]
        done = next((e for e in reversed(evs) if e["type"] == "done"), {})
        sources = next((e for e in evs if e["type"] == "sources"), {"sources": [], "used": 0})
        mine = [s["chunk_id"] for s in sources["sources"][: sources.get("used") or len(sources["sources"])]]
        if done.get("route") in REFUSALS:
            return [], mine, False
        blocks = []
        for e in evs:
            if e["type"] == "token":
                if blocks and blocks[-1][0] == "md":
                    blocks[-1] = ("md", blocks[-1][1] + e["t"])
                else:
                    blocks.append(("md", e["t"]))
            elif e["type"] == "table":
                blocks.append(("table", e))
            elif e["type"] == "replace":
                blocks = [("md", e["text"])]
        blocks = [(k, c.strip() if k == "md" else c) for k, c in blocks if (c.strip() if k == "md" else c)]
        shown = " ".join(c if k == "md" else " ".join(c["header"]) + " " + " ".join(" ".join(r) for r in c["rows"]) for k, c in blocks)
        shown += " " + " ".join(h["heading"] for h in sources["sources"][:3])
        return blocks, mine, self._about(ask, shown) and self._states(ask, shown)

    def _states(self, ask, text):
        """True when the text says what the ask wants of its subject, where that can be told: a "which standard" ask needs a standard named, not a passage that only mentions the word."""
        if any(f.concept == "authority" for f in ask.facets):
            return any(t.startswith("std") for t in rag.tokens(text))
        return True

    def _about(self, ask, text):
        """True when the text names the subject of the ask: the rarest of its words, and most of the rest (idf weighted). A question with no subject of its own is accepted as it is."""
        tokens = [t for t in ask.entity_tokens if not t.isdigit()]
        if not tokens:
            return True
        have = set(rag.tokens(text))
        weights = {t: self.store.idf[t] for t in tokens}
        top = max(weights.values())
        if any(t not in have for t, w in weights.items() if w >= CORE_SHARE * top):
            return False
        return sum(w for t, w in weights.items() if t in have) / sum(weights.values()) >= MIN_ABOUT
