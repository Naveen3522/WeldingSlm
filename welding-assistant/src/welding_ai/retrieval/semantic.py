"""Semantic passage index (latent semantic analysis): finds passages that use different words for the same idea.

TF-IDF over the passages, reduced to a few hundred dimensions by a truncated SVD. Words that occur in the same kinds of
passages end up close together, so "classification of imperfections" also reaches a passage that says "grouped by reference
number". It is built from the documents themselves (no download, no model), needs numpy, and is optional: without numpy the
hybrid index simply runs without it.
"""
import math
from collections import Counter
from pathlib import Path

try:
    import numpy as np
except ImportError:                      # optional dependency
    np = None

from welding_ai import paths
from welding_ai.retrieval import rag

DIMS = 256


class SemanticIndex:
    def __init__(self, vocab, idf, comps, doc_vecs):
        self.vocab, self.idf, self.comps, self.doc_vecs = vocab, idf, comps, doc_vecs

    # ------------------------------------------------------------------ build / load
    # Build the TF-IDF matrix over chunks and reduce it via randomized truncated SVD to `dims` dimensions.
    @classmethod
    def build(cls, chunks, dims=DIMS, seed=7):
        docs = [Counter(rag.tokens(c["heading"] + " " + c["text"])) for c in chunks]
        df = Counter(t for d in docs for t in d)
        vocab = {t: i for i, (t, n) in enumerate(sorted((t, n) for t, n in df.items() if n >= 2))}
        n = len(docs)
        idf = np.zeros(len(vocab), dtype=np.float32)
        for t, i in vocab.items():
            idf[i] = math.log(1 + n / df[t])
        X = np.zeros((n, len(vocab)), dtype=np.float32)
        for r, d in enumerate(docs):
            for t, f in d.items():
                i = vocab.get(t)
                if i is not None:
                    X[r, i] = (1 + math.log(f)) * idf[i]
        X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
        # randomized truncated SVD
        rng = np.random.default_rng(seed)
        Q = rng.standard_normal((X.shape[1], dims + 16)).astype(np.float32)
        Y = X @ Q
        for _ in range(4):
            Y, _r = np.linalg.qr(Y)
            Z, _r = np.linalg.qr(X.T @ Y)
            Y = X @ Z
        B, _r = np.linalg.qr(Y)
        U, S, Vt = np.linalg.svd(B.T @ X, full_matrices=False)
        comps = Vt[:dims].T.astype(np.float32)                    # vocab x dims
        doc_vecs = X @ comps
        doc_vecs /= np.linalg.norm(doc_vecs, axis=1, keepdims=True) + 1e-9
        return cls(vocab, idf, comps, doc_vecs)

    # Persist the SVD components, doc vectors and IDF to disk (plus the vocab as sidecar JSON).
    def save(self, path):
        import json
        np.savez_compressed(path, comps=self.comps, doc_vecs=self.doc_vecs, idf=self.idf)
        Path(str(path) + ".vocab.json").write_text(json.dumps(self.vocab), encoding="utf-8")

    # Load a previously saved SemanticIndex from disk.
    @classmethod
    def load(cls, path):
        import json
        z = np.load(path)
        vocab = json.loads(Path(str(path) + ".vocab.json").read_text(encoding="utf-8"))
        return cls(vocab, z["idf"], z["comps"], z["doc_vecs"])

    # Load the cached index if it matches this chunk set, else rebuild and cache it; None if numpy is missing.
    @classmethod
    def load_or_build(cls, chunks, path=None):
        if np is None:
            return None
        path = Path(path) if path else paths.INDEX_DIR / "semantic.npz"
        try:
            if path.exists() and Path(str(path) + ".vocab.json").exists():
                s = cls.load(path)
                if s.doc_vecs.shape[0] == len(chunks):
                    return s
            s = cls.build(chunks)
            s.save(path)
            return s
        except Exception:
            return None

    # ------------------------------------------------------------------ query
    # Project a piece of text into the reduced (LSA) space as a unit vector.
    def vector(self, text):
        v = np.zeros(len(self.vocab), dtype=np.float32)
        for t, f in Counter(rag.tokens(text)).items():
            i = self.vocab.get(t)
            if i is not None:
                v[i] = (1 + math.log(f)) * self.idf[i]
        q = v @ self.comps
        n = np.linalg.norm(q)
        return q / n if n else None

    # Top-k chunks by cosine similarity in the reduced semantic space.
    def search(self, query, k=40):
        """-> [(chunk_id, cosine)] best first."""
        q = self.vector(query)
        if q is None:
            return []
        sims = self.doc_vecs @ q
        top = np.argsort(-sims)[:k]
        return [(int(i), float(sims[i])) for i in top]
