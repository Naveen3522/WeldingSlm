"""Vectors for short texts (column headers, labels, entity names) from the local Ollama embedding model, kept on disk so each text is embedded once.

    vectors = TextVectors(cache_dir)
    vectors.ensure(texts)          index time: embed what is not cached yet. -> how many were new, or None when Ollama cannot be reached
    vectors.get(texts)             -> a normalised matrix with one row per text (cached ones only; a missing text is a zero row)
    vectors.query(phrases)         query time: one batched call for the phrases of a question, with the query prefix. -> matrix or None

Everything degrades to None when the model is not running, and callers then match by words only. Nothing is downloaded or paid for: nomic-embed-text is already
one of the project's local models.
"""
import hashlib
import json
import threading
import time
from pathlib import Path

import numpy as np

from welding_ai.retrieval import dense

MODEL = "nomic-embed-text"
BATCH = 64
UP_FOR = 20.0                          # seconds a "model is reachable" answer is trusted
PROBE_TIMEOUT = 1.5


def _key(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


class TextVectors:
    def __init__(self, cache_dir, model=MODEL, embed=None):
        self.model = model
        self._embed = embed or (lambda texts: dense._embed(model, texts))
        self.path = Path(cache_dir) / f"facts_{model.replace(':', '_')}"
        self._lock = threading.Lock()
        self._vec = {}                                       # key -> normalised vector
        self._queries = {}                                   # phrase -> vector (small LRU)
        self._up = (0.0, False)
        self._load()
        prefixes = dense.PREFIX.get(model.split(":")[0], ("", ""))
        self.query_prefix, self.document_prefix = prefixes

    # ------------------------------------------------------------------ the cache on disk
    def _load(self):
        keys, mat = self.path.with_suffix(".json"), self.path.with_suffix(".npy")
        if keys.exists() and mat.exists():
            try:
                names = json.loads(keys.read_text(encoding="utf-8"))
                m = np.load(mat)
                if len(names) == len(m):
                    self._vec = dict(zip(names, m))
            except Exception:
                self._vec = {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        names = list(self._vec)
        np.save(self.path.with_suffix(".npy"), np.stack([self._vec[k] for k in names]) if names else np.zeros((0, 1), dtype=np.float32))
        self.path.with_suffix(".json").write_text(json.dumps(names), encoding="utf-8")

    # ------------------------------------------------------------------ is the model reachable
    def available(self):
        when, ok = self._up
        if time.time() - when < UP_FOR:
            return ok
        try:
            dense.urllib.request.urlopen(dense.OLLAMA + "/api/tags", timeout=PROBE_TIMEOUT).read()
            ok = True
        except Exception:
            ok = False
        self._up = (time.time(), ok)
        return ok

    # ------------------------------------------------------------------ index time
    def ensure(self, texts, log=None):
        """Embeds every text that is not cached. -> the number embedded, or None if the model cannot be reached."""
        missing = [t for t in dict.fromkeys(texts) if _key(t) not in self._vec]
        if not missing:
            return 0
        if not self.available():
            return None
        done = 0
        try:
            for i in range(0, len(missing), BATCH):
                part = missing[i:i + BATCH]
                vecs = dense._norm(self._embed([self.document_prefix + t for t in part]))
                with self._lock:
                    for t, v in zip(part, vecs):
                        self._vec[_key(t)] = v.astype(np.float32)
                done += len(part)
                if log:
                    log(done, len(missing))
        except Exception:
            self._up = (time.time(), False)
        if done:
            with self._lock:
                self._save()
        return done

    def has(self, text):
        return _key(text) in self._vec

    def get(self, texts):
        dim = next(iter(self._vec.values())).shape[0] if self._vec else 1
        return np.stack([self._vec.get(_key(t), np.zeros(dim, dtype=np.float32)) for t in texts]) if texts else np.zeros((0, dim), dtype=np.float32)

    # ------------------------------------------------------------------ query time
    def query(self, phrases):
        """-> a normalised matrix, one row per phrase, or None when the model cannot be reached."""
        fresh = [p for p in dict.fromkeys(phrases) if p not in self._queries]
        if fresh:
            if not self.available():
                return None
            try:
                vecs = dense._norm(self._embed([self.query_prefix + p for p in fresh]))
            except Exception:
                self._up = (time.time(), False)
                return None
            for p, v in zip(fresh, vecs):
                self._queries[p] = v.astype(np.float32)
            while len(self._queries) > 2000:
                self._queries.pop(next(iter(self._queries)))
        return np.stack([self._queries[p] for p in phrases]) if phrases else np.zeros((0, 1), dtype=np.float32)
