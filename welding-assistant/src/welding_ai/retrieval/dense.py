"""Dense (embedding) ranking of passages, served by a local Ollama embedding model. Vectors are computed once and cached on disk next to the index;
a query costs one embedding call. Off unless WELD_DENSE_WEIGHT > 0 (see retrieval.py)."""
import hashlib
import json
import urllib.request
from pathlib import Path

import numpy as np

OLLAMA = "http://127.0.0.1:11434"
PREFIX = {   # models trained with task prefixes
    "nomic-embed-text": ("search_query: ", "search_document: "),
    "mxbai-embed-large": ("Represent this sentence for searching relevant passages: ", ""),
}


# Call Ollama's embedding API for a batch of texts.
def _embed(model, texts):
    body = json.dumps({"model": model, "input": texts, "truncate": True}).encode()
    r = urllib.request.urlopen(urllib.request.Request(OLLAMA + "/api/embed", body, {"Content-Type": "application/json"}), timeout=600)
    return np.array(json.loads(r.read())["embeddings"], dtype=np.float32)


# L2-normalize rows of a matrix.
def _norm(m):
    return m / (np.linalg.norm(m, axis=-1, keepdims=True) + 1e-9)


class DenseIndex:
    def __init__(self, model, vecs):
        self.model, self.vecs = model, vecs
        self.qp = PREFIX.get(model.split(":")[0], ("", ""))[0]
        self._cache = {}

    # Text of a chunk to embed (heading + body, truncated).
    @staticmethod
    def doc_text(c):
        return (c["heading"] + "\n" + c["text"])[:2000]

    # Load the cached vectors for this exact chunk set + model, or embed every chunk and cache the result.
    @classmethod
    def load_or_build(cls, chunks, model, cache_dir, batch=16, log=None):
        stamp = hashlib.md5(("".join(c["text"][:50] + c["heading"] for c in chunks) + model).encode()).hexdigest()[:10]
        path = Path(cache_dir) / f"dense_{model.replace(':', '_')}_{stamp}.npy"
        if path.exists():
            return cls(model, np.load(path))
        dp = PREFIX.get(model.split(":")[0], ("", ""))[1]
        out = []
        for i in range(0, len(chunks), batch):
            out.append(_embed(model, [dp + cls.doc_text(c) for c in chunks[i:i + batch]]))
            if log and (i // batch) % 20 == 0:
                log(i, len(chunks))
        vecs = _norm(np.concatenate(out))
        np.save(path, vecs)
        return cls(model, vecs)

    # Embed a query (cached, LRU-ish bounded to 512 entries).
    def query_vec(self, q):
        v = self._cache.get(q)
        if v is None:
            v = self._cache[q] = _norm(_embed(self.model, [self.qp + q])[0])
            if len(self._cache) > 512:
                self._cache.pop(next(iter(self._cache)))
        return v

    # Top-k chunks by cosine similarity to the query embedding.
    def search(self, query, k=40):
        s = self.vecs @ self.query_vec(query)
        top = np.argsort(-s)[:k]
        return [(int(i), float(s[i])) for i in top]
