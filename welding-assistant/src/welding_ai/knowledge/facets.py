"""The facets the documents talk about, taken from the documents: every column header and every "Label:" in them ("corrective action", "failure consequence",
"likely causes", "gtaw-specific causes", "what it governs"). A question phrase ("what happens if it goes wrong", "which standard governs") is matched to these labels by meaning
(the local embedding model) and, when that model is not running, by words.

Every label is also placed in one of a few CONCEPTS (cause, consequence, detection, correction, function, controls, authority, ...) by how close it is to the concept's seed phrases.
The seeds are plain English, not about welding; which of the documents' labels belong to a concept is decided by the matching, so a column a new document introduces
("smaw-specific causes", "wear consequence") joins its concept the next time the documents are indexed.

    facets = Facets(units, vectors)
    facets.prepare()                          index time: embed the labels
    facets.match(["consequences", ...])       -> for each phrase, [Hit(label, sim, semantic)] best first
    facets.family(label)                      every label that means the same kind of thing ("cause", "likely causes", "gtaw-specific causes", "why it happens")
    facets.task_of(label)                     the kind of job a label belongs to ("troubleshoot", "parameters", "equipment", "standards", ...)
"""
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from welding_ai.retrieval import rag

MARGIN = 0.04                      # labels this close to the best one are the same facet ("consequence", "failure consequence", "practical consequence")
SEMANTIC_MIN = 0.66                # below this a phrase does not name a facet
LEXICAL_MIN = 0.70
SEED_MIN = 0.62                    # a phrase (with a facet word in it) this close to a concept's seed phrase names that concept even when no header says it that way
CONCEPT_MIN = 0.80                 # a label this close to a seed phrase belongs to its concept
NO_SHARED_WORD = 0.12              # a label that shares no word with the seed phrase must be this much closer (the embedding model finds all short phrases somewhat alike)
MIN_GENERAL_DOCS = 8               # a word that names a column in this many documents is a general facet word ("cause", "consequence", "detected")
MIN_SEED_DOCS = 3                  # a word of the seeds counts as a facet word when it names a column in at least this many documents

# concept -> (task, seed phrases). Generic English: what people ask of any technical reference.
CONCEPTS = {
    "cause": ("troubleshoot", ("cause", "likely causes", "root cause", "why it happens", "reason it occurs")),
    "consequence": ("troubleshoot", ("consequence", "failure consequence", "effect", "symptom if wrong", "what happens if it goes wrong", "risk")),
    "detection": ("troubleshoot", ("how it is detected", "detection", "how to find it", "inspection method", "symptom")),
    "correction": ("troubleshoot", ("corrective action", "remedy", "how it is corrected", "fix", "prevention")),
    "mechanism": ("troubleshoot", ("mechanism", "how it forms", "what physically happens")),
    "function": ("equipment", ("function", "what it does", "purpose", "role of the component")),
    "controls": ("parameters", ("what it controls", "controls", "parameter effect", "setting")),
    "authority": ("standards", ("standard", "what it governs", "who decides", "requirement", "what it covers", "scope", "acceptance criteria", "how it is used")),
    "selection": ("selection", ("choose something else when", "suitability", "when to use", "limitations", "advantages", "application")),
    "procedure": ("procedure", ("steps", "procedure", "sequence", "order of operations")),
    "safety": ("safety", ("hazard", "protection", "safety risk", "exposure")),
}
TASK_NAMES = {"troubleshoot": "Troubleshooting", "parameters": "Parameters", "equipment": "Equipment", "standards": "Standards", "selection": "Selection", "procedure": "Procedure",
              "safety": "Safety", "reference": "Reference"}
CONCEPT_PREFIX = "concept:"
RELATION_WORDS = frozenset({"requir", "govern", "decid", "determin", "specif", "appl", "cover", "fix", "remedy", "prevent", "avoid", "resolv", "solv", "correct"})        # verbs that say how two things relate, whatever the documents' column names are


@dataclass
class Hit:
    label: str
    sim: float
    semantic: bool


def word(w):
    """A word reduced so its forms meet: consequence / consequences, cause / causing, govern / governs / governing, detect / detection."""
    w = w.lower()
    for suffix in ("ation", "ion", "ing", "ed", "es", "s", "e", "ly"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            w = w[: -len(suffix)]
    return w


def lexical_words(text):
    return [word(w) for w in re.findall(r"[A-Za-z][A-Za-z\-]+", text) if w.lower() not in rag.STOP and len(w) > 2]


def _head(label):
    words = re.findall(r"[A-Za-z]+", label)
    return word(words[-1]) if words else ""


SEEDS = [(concept, phrase) for concept, (_task, phrases) in CONCEPTS.items() for phrase in phrases]
SEED_WORDS = frozenset(w for _c, p in SEEDS for w in lexical_words(p))


class Facets:
    def __init__(self, units, vectors=None):
        self.vectors = vectors
        self.count = Counter()
        self.docs = defaultdict(set)
        for u in units:
            for label, _v in u.attrs:
                self.count[label] += 1
                self.docs[label].add(u.source)
        self.labels = sorted(self.count)
        self.words = {label: frozenset(lexical_words(label)) for label in self.labels}
        self.word_docs, self.word_units = defaultdict(set), Counter()
        for label, ws in self.words.items():
            for w in ws:
                self.word_docs[w] |= self.docs[label]
                self.word_units[w] += self.count[label]
        df = Counter(w for ws in self.words.values() for w in ws)
        self.weight = {w: 1.0 / (1.0 + 0.25 * n) for w, n in df.items()}              # a word in many labels ("process", "chapter") says little
        self.heads = defaultdict(list)
        for label in self.labels:
            self.heads[_head(label)].append(label)
        self._matrix = None
        self._concepts = None
        self._semantic = False
        self._seeds = None

    # ------------------------------------------------------------------ index time
    def prepare(self, log=None):
        """Embeds the labels and the concept seeds. -> how many were new, or None when the embedding model is not running."""
        if self.vectors is None:
            return None
        done = self.vectors.ensure(self.labels + [p for _c, p in SEEDS], log)
        self._matrix = self._concepts = None
        self._semantic = False
        return done

    @property
    def semantic(self):
        """True once every label has a vector (then matching is by meaning); until then it is by words."""
        if self._semantic:
            return True
        if self.vectors is not None and all(self.vectors.has(label) for label in self.labels):
            self._semantic = True
            self._concepts = None
        return self._semantic

    def _seed_matrix(self):
        if self._seeds is None and self.vectors is not None and all(self.vectors.has(p) for _c, p in SEEDS):
            self._seeds = self.vectors.get([p for _c, p in SEEDS])
        return self._seeds

    def _label_matrix(self):
        if self._matrix is None:
            self._matrix = self.vectors.get(self.labels)
        return self._matrix

    # ------------------------------------------------------------------ query time
    def _lexical(self, phrase):
        q = set(lexical_words(phrase))
        out = []
        if not q:
            return out
        for label in self.labels:
            have = q & self.words[label]
            if not have:
                continue
            total = sum(self.weight[w] for w in self.words[label])
            cover = sum(self.weight[w] for w in have) / total
            precision = len(have) / len(q)
            out.append(Hit(label, 0.5 + 0.35 * cover + 0.15 * precision, False))
        for concept, phrase in SEEDS:                                      # a seed phrase is a way to name a concept too
            have = q & set(lexical_words(phrase))
            if have and len(have) == len(set(lexical_words(phrase))):
                out.append(Hit(CONCEPT_PREFIX + concept, 0.5 + 0.35 + 0.15 * len(have) / len(q), False))
        out.sort(key=lambda h: -h.sim)
        return out

    def match(self, phrases, k=6):
        """-> for each phrase a list of Hit (label, sim, semantic), best first. Semantic when the embedding model answers, else by words."""
        vecs = self.vectors.query(phrases) if self.vectors is not None and self.semantic else None
        if vecs is None or not len(self.labels):
            return [self._lexical(p)[:k] for p in phrases]
        sims = vecs @ self._label_matrix().T
        seed_sims = vecs @ self._seed_matrix().T if self._seed_matrix() is not None else None
        out = []
        for r, row in enumerate(sims):
            top = np.argsort(-row)[:k]
            hits = [Hit(self.labels[int(i)], float(row[int(i)]), True) for i in top]
            if seed_sims is not None:                                     # the best seed phrase of each concept is a way to name it too
                best = {}
                for j, (concept, _p) in enumerate(SEEDS):
                    best[concept] = max(best.get(concept, 0.0), float(seed_sims[r][j]))
                hits += [Hit(CONCEPT_PREFIX + c, v, True) for c, v in best.items() if v >= SEED_MIN]
                hits.sort(key=lambda h: -h.sim)
            out.append(hits[:k + 3])
        return out

    def accepted(self, hits):
        """The hits good enough to name a facet, best first: a header must be SEMANTIC_MIN close, a concept's seed phrase SEED_MIN (or LEXICAL_MIN when matching by words)."""
        return [h for h in hits if h.sim >= (LEXICAL_MIN if not h.semantic else SEED_MIN if h.label.startswith(CONCEPT_PREFIX) else SEMANTIC_MIN)]

    def same_facet(self, hits):
        """The hits within MARGIN of the best one."""
        if not hits:
            return []
        best = hits[0].sim
        return [h for h in hits if h.sim >= best - MARGIN]

    def is_facet_word(self, w, store_key_units=0):
        """True when the documents use this word to name a kind of information (a column header or label) rather than a thing."""
        k = word(w)
        if k in RELATION_WORDS:
            return True
        docs, units = len(self.word_docs.get(k, ())), self.word_units.get(k, 0)
        general = docs >= MIN_GENERAL_DOCS and units >= 2 * store_key_units
        seeded = docs >= MIN_SEED_DOCS and k in SEED_WORDS
        return general or seeded

    # ------------------------------------------------------------------ concepts and tasks
    def _concept_map(self):
        if self._concepts is not None:
            return self._concepts
        out = {}
        if self.vectors is not None and self.semantic and all(self.vectors.has(p) for _c, p in SEEDS):
            sims = self._label_matrix() @ self.vectors.get([p for _c, p in SEEDS]).T
            seed_words = [frozenset(lexical_words(p)) for _c, p in SEEDS]
            for i, label in enumerate(self.labels):
                scored = [(sims[i][j] - (0.0 if self.words[label] & seed_words[j] else NO_SHARED_WORD), j) for j in range(len(SEEDS))]
                best, j = max(scored)
                if best >= CONCEPT_MIN:
                    out[label] = SEEDS[j][0]
        else:                                                                          # by words: a label that shares a word with a seed phrase takes its concept
            for label in self.labels:
                best = (0.0, None)
                for concept, phrase in SEEDS:
                    have = self.words[label] & set(lexical_words(phrase))
                    if have:
                        score = len(have) / max(len(self.words[label]), 1)
                        if score > best[0]:
                            best = (score, concept)
                if best[1]:
                    out[label] = best[1]
        self._concepts = out
        return out

    def concept_of(self, label):
        if label.startswith(CONCEPT_PREFIX):
            return label[len(CONCEPT_PREFIX):]
        return self._concept_map().get(label)

    def concept_facet(self, concept):
        """A facet asked for by name (the problem someone describes wants its cause, correction and detection): every label of the concept. None when no label belongs to it."""
        from welding_ai.understanding.asks import FacetAsk
        labels = sorted(l for l, c in self._concept_map().items() if c == concept)
        if not labels:
            return None
        return FacetAsk(concept, sorted(set(labels) | self.family(labels[0])), [], 1.0, CONCEPTS[concept][0], concept)

    def task_of(self, label):
        concept = self.concept_of(label)
        return CONCEPTS[concept][0] if concept else "reference"

    def family(self, label):
        """Every label that means what this one means: the labels of its concept, and the ones that end in the same word ("gtaw-specific causes", "filler-related cause")."""
        concept = self.concept_of(label)
        out = set() if label.startswith(CONCEPT_PREFIX) else {label}
        if concept:
            out |= {l for l, c in self._concept_map().items() if c == concept}
        if not label.startswith(CONCEPT_PREFIX) and _head(label) in SEED_WORDS:
            out |= set(self.heads.get(_head(label), ()))
        return out

    def tasks_by_document(self, units):
        """{task: {source: how many facts}} : what each kind of question can be answered from, document by document."""
        out = defaultdict(Counter)
        for u in units:
            for label, _v in u.attrs:
                task = self.task_of(label)
                if task != "reference":
                    out[task][u.source] += 1
        return out
