"""Answer engine: builds the answer the question asks for, from the documents' own text.

Input is the plan from `understanding.planner.analyze()`; output is an `Answer` (ordered text / table blocks plus the passages
cited), or None when the documents do not contain enough to answer.

`EvidenceStore` holds the indexes and shared scoring; each handler family lives in `handlers/`; `ROUTES` (below) says which
handlers are tried, in which order, for each intent (strategy pattern: change a route without touching a handler).
"""
import re

from welding_ai.answering.evidence import Answer, EvidenceStore  # noqa: F401  (Answer is re-exported)
from welding_ai.retrieval import rag, tables
from welding_ai.answering.handlers.comparisons import ComparisonHandlers
from welding_ai.answering.handlers.definition import DefinitionHandlers
from welding_ai.answering.handlers.enumeration import EnumerationHandlers
from welding_ai.answering.handlers.navigation import NavigationHandlers
from welding_ai.answering.handlers.premises import PremiseHandlers
from welding_ai.answering.handlers.procedures import ProcedureHandlers
from welding_ai.answering.handlers.rows import RowHandlers
from welding_ai.answering.handlers.situations import SituationHandlers
from welding_ai.answering.handlers.topics import TopicHandlers

# intent -> handlers tried in order; the first that returns an answer wins
ROUTES = {
    "define": ["entity_parts"],
    "enumerate": ["enumerate", "topic"],
    "explain": ["section", "overview", "list", "entity_parts_or_topic"],
    "topic": ["section", "list", "entity_parts_multi"],
    "diagnose": ["diagnose", "procedure"],
    "procedure": ["procedure", "diagnose"],
    "situation": ["situation", "diagnose"],
    "compare": ["compare", "topic"],
    "conflict": ["conflict"],
    "premise": ["premise"],
    "comply": ["enumerate", "topic"],
}
DEFAULT_ROUTE = ["topic"]


_MEANING = re.compile(r"\bmeans?\b|\bmeaning\b|\bwhat should\b|\bwhat happens\b|\bwhat next\b|\bwhat do i do\b", re.I)


# The ordered handler names for a plan, built from the intent's base route plus wording-triggered lookups in front.
def route_for(plan):
    """The ordered handler names for a plan: the intent's route, plus a 'what does <value> mean' lookup and a
    row-with-asked-columns lookup in front when the wording calls for them."""
    it = plan["intent"]
    order = list(ROUTES.get(it, DEFAULT_ROUTE))
    if it in ("topic", "explain", "enumerate", "diagnose", "procedure"):
        order = ["lead_list"] + order              # a question that restates the sentence introducing a list is answered by that list
    if it in ("diagnose", "define", "topic", "explain") and _MEANING.search(plan["q"]):
        order = ["situation"] + order
    if it not in ("compare", "conflict", "situation"):
        order = ["row_columns"] + order
    if len(plan.get("attrs") or []) >= 2 and it not in ("compare", "conflict", "premise"):
        order = ["attribute_rows"] + order                # "what does it mean and what action": the table row with every column asked
    if it not in ("conflict", "premise"):
        k = sum(1 for n in order[:3] if n in ("situation", "row_columns", "attribute_rows"))          # after a "what does <value> mean" / asked-columns lookup, before the general routes
        front = ["cell_lookup"] if plan.get("pure_define") else ["cell_lookup", "table_title", "heading_lookup"]      # "what is X" asks for a definition first; a table or heading only if there is none
        order = order[:k] + ["chapter"] + front + order[k:]        # a question about a chapter / the catalogue is answered from the catalogue
        if plan.get("pure_define"):
            order += ["table_title", "heading_lookup"]
    return order


class Engine(EvidenceStore, RowHandlers, DefinitionHandlers, EnumerationHandlers, ProcedureHandlers, SituationHandlers, ComparisonHandlers, TopicHandlers, PremiseHandlers, NavigationHandlers):
    def __init__(self, index, sections=None, path=None, tindex=None):
        super().__init__(index, sections, path)
        self.tindex = tindex if tindex is not None else tables.load(index)      # whole-table index used by enumeration answers

    # Try an exact section-heading match first, then the handlers routed for this question's intent, in order.
    def answer(self, plan, tindex=None):
        """Try the handlers routed for this question, in order; the first that returns an answer wins."""
        if plan["intent"] not in ("conflict", "premise") and not (tindex or self.tindex).match_title(plan["core"]):
            r = self.heading_lookup(plan, exact_only=True)          # a question that IS a section's heading (three words or more) names that section, whatever else its words match; a table of that title comes first
            if r and r.blocks:
                return r
        for name in route_for(plan):
            r = self._dispatch(name, plan, tindex)
            if r and r.blocks and self._relevant(plan, r):
                return self._with_definition(r, plan)
        return None

    RARE_IDF, MIN_RARE_COVER = 5.0, 0.6

    # True when one paragraph (or table row) of the answer holds every content word of the question.
    def paragraph_support(self, plan, r):
        """True when one paragraph (or table row) of the answer holds every content word of the question. Used for the fallback topic answer,
        which is built from a section that merely mentions the words in different places."""
        terms = [t for t in plan["terms"] if not t.isdigit() and not t.startswith(self.REQUEST_WORDS)]
        if not terms:
            return True
        for b in r.blocks:
            parts = [b.get("md", "")] if b["k"] == "text" else [" ".join(row) for row in b.get("rows", [])]
            for part in parts:
                if all(t in set(rag.tokens(part)) for t in terms):
                    return True
        return False
    REQUEST_WORDS = ("diagnos", "troubleshoot", "procedur", "explain", "describ", "overview", "summar", "compar", "differ", "instruct", "walk")     # what the user asks for, not what it is about

    # A free-form answer must contain the question's rare words, in the answer text or the passages it cites.
    def _relevant(self, plan, r):
        """A free-form answer (procedure, diagnosis, topic, situation) must contain the question's rare words, in the answer or in the passages it cites:
        "override the controller lockout" must not be answered by a table that never mentions a lockout."""
        if r.kind not in ("procedure", "diagnose", "topic", "situation"):
            return True
        rare = [t for t in plan["terms"] if not t.isdigit() and self.index.idf(t) >= self.RARE_IDF and not t.startswith(self.REQUEST_WORDS)]
        if not rare:
            return True
        text = " ".join(b.get("md", "") + " " + " ".join(" ".join(row) for row in b.get("rows", [])) for b in r.blocks)
        text += " " + " ".join(self.index.chunks[c["chunk"]]["text"] for c in r.cites if c.get("chunk") is not None)
        have = set(rag.tokens(text))
        return sum(t in have for t in rare) / len(rare) >= self.MIN_RARE_COVER

    # Call the named handler method with the right arguments (a few handlers need special-cased gating).
    def _dispatch(self, name, plan, tindex):
        ent = plan["entity"]
        if name == "entity_parts":
            return self.entity_parts(plan) if ent else None
        if name == "entity_parts_multi":
            return self.entity_parts(plan) if ent and len(plan["parts"]) > 1 else None
        if name == "entity_parts_or_topic":
            return (self.entity_parts(plan) if ent and any(p["own"] for p in plan["parts"]) else None) or self.topic(plan)
        if name == "enumerate":
            return self.enumerate_(plan, tindex or self.tindex)
        if name == "table_title":
            return self.table_by_title(plan, tindex or self.tindex)
        if name == "list":
            return self.list_(plan) if (plan.get("format") or plan["intent"] == "enumerate") else None
        if name == "overview":
            return self.doc_overview(plan) if plan.get("docref") else None
        if name == "section":
            return self.section_view(plan)
        return getattr(self, name)(plan)                 # situation, row_columns, procedure, diagnose, compare, conflict, topic

    # Prepend a named thing's own definition to an "explain" answer that's otherwise just a topic overview.
    def _with_definition(self, r, plan):
        """An explanation of a named thing starts with its definition."""
        ent = plan["entity"]
        if plan["intent"] == "explain" and ent and r.kind == "topic":
            d = self.definition(ent, plan)
            if d:
                r.blocks.insert(0, {"k": "text", "md": d["text"]})
                self._cite_block(r, d["sec"], d["b"])
        return r
