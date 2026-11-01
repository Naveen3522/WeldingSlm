"""How alike a question and each passage are, by meaning: cosine similarity of their embeddings from the local Ollama embedding model (nomic-embed-text).

    sim = PassageSimilarity(cache_dir)
    sim.prepare(chunks)          embed every passage that is not cached yet (run it in the background: the first time takes a few minutes, later only new passages)
    sim.ready                    True when every passage of the last `prepare` has a vector
    sim.rank(question, k)        -> [(chunk_id, cosine)] the k most similar passages, best first; [] when not ready or the embedding model cannot be reached
    sim.scores(question, ids)    -> {chunk_id: cosine} for these passages (the ones with a vector)

It is the "similarity check" of the answering step: the passages the keyword search finds and the passages closest in meaning are put side by side, and only passages that are
close enough to the question go to the language model. Everything degrades to "no similarity" when the embedding model is not running, and the answering step then relies on
the keyword search alone. Nothing is downloaded or paid for: the embedding model is one of the project's local Ollama models.
"""
import threading

import numpy as np

from welding_ai.knowledge.embed import TextVectors
from welding_ai.retrieval.dense import DenseIndex


class PassageSimilarity:
    def __init__(self, cache_dir, vectors=None):
        self.vectors = vectors or TextVectors(cache_dir)
        self._lock = threading.Lock()
        self._texts, self._ids, self._matrix = [], [], None
        self.ready = False
        self.progress = (0, 0)
        self._thread = None
        self._again = None

    @staticmethod
    def text_of(chunk):
        return DenseIndex.doc_text(chunk)

    def check(self, chunks):
        """Called as soon as the index changes: the vectors held are kept when the new passages only add to the old ones (a passage written by hand), and dropped when
        the passages changed (documents were rebuilt), because the numbers of the passages no longer mean the same thing. -> True when still usable."""
        texts = [self.text_of(c) for c in chunks]
        with self._lock:
            if self._texts != texts[:len(self._texts)]:
                self.ready = False
            return self.ready

    def prepare(self, chunks):
        """Embeds what is missing and then holds the matrix of all passages. -> True when every passage has a vector."""
        texts = [self.text_of(c) for c in chunks]
        ids = [c.get("chunk_id", i) for i, c in enumerate(chunks)]
        self.check(chunks)
        done = self.vectors.ensure(texts, log=lambda n, total: setattr(self, "progress", (n, total)))
        if done is None:
            return False
        matrix = self.vectors.get(texts)
        ok = bool(len(matrix)) and all(self.vectors.has(t) for t in texts)
        with self._lock:
            self._texts, self._ids, self._matrix = texts, ids, matrix
            self.ready = ok
        return ok

    def prepare_in_background(self, chunks):
        """Starts embedding in a thread. When one is already running, the newest passages are taken up as soon as it has finished."""
        self.check(chunks)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._again = chunks
                return self._thread
            self._again = None
            self._thread = threading.Thread(target=self._loop, args=(chunks,), daemon=True, name="passage-similarity")
        self._thread.start()
        return self._thread

    def _loop(self, chunks):
        while chunks is not None:
            self.prepare(chunks)
            with self._lock:
                chunks, self._again = self._again, None

    def _query(self, question):
        got = self.vectors.query([question])
        return None if got is None or not len(got) else got[0]

    def rank(self, question, k=10):
        if not self.ready:
            return []
        q = self._query(question)
        if q is None:
            return []
        with self._lock:
            matrix, ids = self._matrix, self._ids
        sims = matrix @ q
        top = np.argsort(-sims)[:k]
        return [(ids[int(i)], float(sims[int(i)])) for i in top]

    def scores(self, question, ids):
        if not self.ready:
            return {}
        q = self._query(question)
        if q is None:
            return {}
        with self._lock:
            matrix, position = self._matrix, {cid: i for i, cid in enumerate(self._ids)}
        return {cid: float(matrix[position[cid]] @ q) for cid in ids if cid in position}
