"""Approved pairs added by people are used the moment they are approved, without training: the question asked is compared, by meaning, with the question of every such pair,
and a close one hands its approved answer and the text it stands on to the language model as the first passage.

    lookup = LearnedLookup(vectors)
    lookup.refresh(pairs)                       the approved pairs added by people (queue.approved() filtered); embeds the questions not seen before
    lookup.find(question)                       -> (pair, cosine) for the closest pair if it is close enough, else None

    passages_of(pair, chunks, hit)              -> the index passages the pair stands on: its own passage (written by hand) or the passages its highlights are in

Nothing is trained and nothing is written into a document. A pair that is rejected, deleted, edited or no longer approved drops out at the next `refresh`, which the queue's
change hook calls. When the embedding model is not running `find` returns None and the ordinary passage search answers.
"""
import re
import threading

import numpy as np

from welding_ai.knowledge.manual_passages import SOURCE

MATCH = 0.82                                # cosine of two questions from which on they are the same question (nomic-embed-text: reworded questions 0.85-0.97, other topics below 0.75)
_SPACE = re.compile(r"\s+")


def _squash(text):
    return _SPACE.sub(" ", text.replace("|", "/")).strip().lower()


class LearnedLookup:
    def __init__(self, vectors):
        self.vectors = vectors
        self._lock = threading.Lock()
        self._pairs, self._matrix = [], None
        self._found = {}                                   # (pair id, number of passages) -> the passage numbers the pair stands on

    def refresh(self, pairs):
        """Holds these pairs (the ones added by a person and approved). -> how many."""
        pairs = [p for p in pairs if p["kind"] in ("manual", "suggested") and p["status"] == "approved"]
        texts = [p["question"] for p in pairs]
        done = self.vectors.ensure(texts) if texts else 0
        matrix = self.vectors.get(texts) if texts and done is not None else None
        with self._lock:
            self._pairs, self._matrix = (pairs, matrix) if matrix is not None else ([], None)
            self._found.clear()
        return len(self._pairs)

    def find(self, question, threshold=MATCH):
        with self._lock:
            pairs, matrix = self._pairs, self._matrix
        if matrix is None or not len(pairs):
            return None
        got = self.vectors.query([question])
        if got is None or not len(got):
            return None
        sims = matrix @ got[0]
        best = int(np.argmax(sims))
        return (pairs[best], float(sims[best])) if sims[best] >= threshold else None

    def passages_of(self, pair, chunks):
        """-> the index passages (dicts) this pair stands on, in order: its own passage, or the passage each highlight is in. Empty when they are not found (the document changed)."""
        key = (pair["id"], len(chunks))
        with self._lock:
            known = self._found.get(key)
        if known is None:
            known = self._resolve(pair, chunks)
            with self._lock:
                self._found[key] = known
        return [chunks[i] for i in known]

    @staticmethod
    def _resolve(pair, chunks):
        import json
        if pair["doc"] == SOURCE:
            title = pair["section"].strip().lower()
            i = next((n for n, c in enumerate(chunks) if c["source"] == SOURCE and c["heading"].strip().lower() == title), None) if title else None
            return [] if i is None else [i]
        refs = json.loads(pair["refs"]) if pair.get("refs") else [{"doc": pair["doc"], "text": pair["answer"]}]
        out = []
        for ref in refs:
            want = _squash(ref["text"])
            i = next((n for n, c in enumerate(chunks) if c["source"] == ref["doc"] and want in _squash(c["text"])), None)
            if i is not None and i not in out:
                out.append(i)
        return out
