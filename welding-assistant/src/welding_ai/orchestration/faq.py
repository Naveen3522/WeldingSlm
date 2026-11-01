"""Matching a question against the verified FAQ (strict: an FAQ answer is only served for a clear match)."""
import math

from welding_ai.retrieval import rag


class FaqMatcher:
    # Precompute the term sets for every FAQ's question/aliases (variants) and full vocabulary.
    def __init__(self, index, faqs, cfg):
        self.index, self.faqs, self.cfg = index, faqs, cfg
        self.maxw = math.log(1 + index.n) * 0.6
        self.variants = []
        for i, f in enumerate(faqs):
            for v in [f["question"]] + f["aliases"]:
                self.variants.append((i, set(rag.tokens(index.expand(v)))))
        self.qtok = [set(rag.tokens(index.expand(f["question"]))) for f in faqs]
        self.vocab = [set(rag.tokens(f["question"] + " " + " ".join(f["aliases"]) + " " + f["answer"] + " "
                                     + " ".join(p["text"] for p in f["points"]))) for f in faqs]

    # Term weight (IDF, falling back to a max weight for unindexed terms).
    def _w(self, t):
        return self.index.idf(t) or self.maxw

    # Best-matching FAQ for a question, scored by F1 of question-coverage and variant-coverage.
    def match(self, question):
        qt = set(rag.tokens(self.index.expand(question)))
        if not qt:
            return None
        wq = sum(self._w(t) for t in qt)
        best = {i: (0.0, 0.0, 0.0) for i in range(len(self.faqs))}      # faq -> (f1, qcov, vcov)
        for i, vt in self.variants:
            inter = sum(self._w(t) for t in qt & vt)
            inter_q = sum(self._w(t) for t in qt & (vt | self.qtok[i]))     # topic words may come from the canonical question
            wv = sum(self._w(t) for t in vt)
            qcov, vcov = (inter_q / wq if wq else 0.0), (inter / wv if wv else 0.0)
            f1 = 2 * qcov * vcov / (qcov + vcov) if (qcov + vcov) else 0.0
            if f1 > best[i][0]:
                best[i] = (f1, qcov, vcov)
        ranked = sorted(best.items(), key=lambda x: -x[1][0])
        (i, (s1, qc, vc)), s2 = ranked[0], (ranked[1][1][0] if len(ranked) > 1 else 0.0)
        rare_missing = [t for t in qt if self._w(t) >= self.cfg.faq_rare_idf and t not in self.vocab[i]]
        return {"idx": i, "sim": round(s1, 3), "qcov": round(qc, 3), "vcov": round(vc, 3), "margin": round(s1 - s2, 3), "rare_missing": rare_missing}

    # Strict acceptance gate: similarity, both coverages and the margin over the runner-up must all clear their thresholds.
    def accept(self, m):
        c = self.cfg
        return (bool(m) and m["sim"] >= c.faq_threshold and m["qcov"] >= c.faq_qcov and m["vcov"] >= c.faq_vcov
                and m["margin"] >= c.faq_margin and not m["rare_missing"])
