"""Hybrid retrieval: unit-level (sentence / table-row) search fused with chunk-level search.

Why: chunk-level BM25 cannot tell a passage that *is about* an entity (a standards-table row "ISO 4063 | 2023 | ...")
from a passage that merely mentions it. This index makes every sentence and every table row searchable on its own,
with its column headers, row key (first cell), section and document title as context, and then

  * treats standard identifiers (ISO 4063, AWS D1.1, ISO 15614-1) as single terms, in documents and questions alike;
  * rewards a row whose key IS the identifier asked about, and demotes header rows (they describe columns, not entities);
  * matches the question's intent words against column labels ("how is X used" -> the "How It Is Used" column);
  * fuses the unit-level ranking with the chunk-level ranking (reciprocal rank fusion), letting the unit-level ranking
    decide when the question is an entity lookup and acting as a light tie-breaker otherwise.

The public API is the same as rag.Index (search / idf / expand / correct / focus ...), plus `score_units`, used by the
answer composer so that answers are chosen with the same relevance model that found the passage, and `entity_query`.
"""
from welding_ai import paths
import json
import os
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from welding_ai.answering import answering
from welding_ai.retrieval import rag, roles
from welding_ai.retrieval.semantic import SemanticIndex

K1, B = 1.2, 0.6
KEY_BONUS = 1.6          # row key matches a query term
HDR_BONUS_ENTITY = 8.0   # entity lookup: a column label matches the question's intent words ("used", "covers", "scope")
HDR_BONUS_GENERAL = 0.0  # ordinary questions are not affected by column labels
BIGRAM_BONUS = 0.9       # adjacent query words appear adjacent in the unit
ID_BOOST = 2.0           # standard identifiers count double in the query
EXACT_KEY_BONUS = 8.0    # entity lookup: the row's key IS the identifier asked about
HEADER_ROW_FACTOR = 0.35 # column-header rows describe columns, not the entity
RRF_K = 60
W_CHUNK_GENERAL, W_UNIT_GENERAL = 1.0, 0.15      # ordinary question: chunk-level decides, units break ties
W_CHUNK_ENTITY, W_UNIT_ENTITY = 0.2, 6.0         # entity lookup: the unit-level (row) ranking decides
W_DENSE = float(os.environ.get("WELD_DENSE_WEIGHT", 0.0))        # embedding ranking in the fusion (Phase 3); 0 = off
DENSE_MODEL = os.environ.get("WELD_DENSE_MODEL", "nomic-embed-text")
W_SEM_GENERAL = float(os.environ.get("WELD_SEM_WEIGHT", 0.0))   # LSA ranking in the fusion: scored worse at every weight (0.3-1.0), so off
MAX_POSTING = 2500       # very common terms ("iso") do not generate candidate units

_ORGS = {"iso", "aws", "asme", "astm", "ipc", "ansi", "csa", "din", "api", "bs", "en"}
_SEP = re.compile(r"^\|[\s:|-]+\|?$")


# A token that can be part of a bare identifier such as 'ISO 4063' or 'AWS D1.1'.
def _idish(t):
    """A token that can be part of a bare identifier such as 'ISO 4063' or 'AWS D1.1'."""
    return t.startswith("std") or t in _ORGS or bool(re.fullmatch(r"[a-z]?\d[a-z0-9.\-]*", t))


# Split a markdown table row unit ("| a | b |") into its cell strings.
def _row_cells(u):
    return [c.strip() for c in u.strip().strip("|").split("|")]


# Stemmed content words of a text, excluding the answering module's generic/stop terms.
def _intent_stems(text):
    return {answering._wstem(w) for w in re.findall(r"[a-z]+", text.lower())} - {answering._wstem(w) for w in answering._GENERIC}


class HybridIndex(rag.Index):
    # Build the chunk-level BM25 index (via the parent) plus a unit-level (sentence/row) postings index,
    # keyed for entity/identifier lookups, and optionally the semantic and dense embedding indexes.
    def __init__(self, chunks):
        super().__init__(chunks)
        self.units = []                                   # per chunk: unit dicts, same order as rag.Index._units
        self.u_post = defaultdict(list)                   # term -> [(unit_id, tf)]
        self.u_len, self.u_seq, self.u_key, self.u_keyid = [], [], [], []
        self.u_hdr, self.u_isheader, self.u_chunk, self.u_ref = [], [], [], []
        self.u_post_skip = set()
        uid = 0
        for ci, c in enumerate(chunks):
            head_toks = rag.tokens(c["heading"] + " " + c["source"].rsplit(".", 1)[0].replace("_", " "))
            lines = c["text"].split("\n")
            header_rows = {lines[i].strip() for i in range(len(lines) - 1) if lines[i].startswith("|") and _SEP.match(lines[i + 1].strip())}
            lst = []
            for u in self._units(c["text"]):
                key, search, hdr_words, is_header = "", u, set(), False
                if _SEP.match(u.strip()):                 # "| --- | --- |" carries no content: never a candidate
                    self.u_post_skip.add(uid)
                    self.u_len.append(1); self.u_seq.append([]); self.u_key.append(set()); self.u_keyid.append(False)
                    self.u_hdr.append(set()); self.u_isheader.append(True); self.u_chunk.append(ci); self.u_ref.append((ci, len(lst)))
                    lst.append({"uid": uid, "text": u, "key": ""})
                    uid += 1
                    continue
                if u.startswith("|"):
                    cells = _row_cells(u)
                    is_header = u.strip() in header_rows
                    hdr = answering._table_header(c["text"], u)
                    if hdr and len(hdr) == len(cells) and not is_header:
                        search = " ".join(f"{h} {v}" for h, v in zip(hdr, cells))
                        hdr_words = set().union(*[_intent_stems(h) for h in hdr])
                    else:
                        search = " ".join(cells)
                    key = cells[0] if cells else ""
                toks = rag.tokens(search)
                ktoks = rag.tokens(key) if key else []
                tf = Counter(toks)
                for t in ktoks:
                    tf[t] += 3                            # the row key is what the row is about
                for t in head_toks:
                    tf[t] += 1
                for t, f in tf.items():
                    self.u_post[t].append((uid, f))
                self.u_len.append(len(toks) + 3 * len(ktoks) + len(head_toks))
                self.u_seq.append(toks)
                self.u_key.append(set(ktoks))
                self.u_keyid.append((not is_header) and bool(ktoks) and any(t.startswith("std") for t in ktoks) and all(_idish(t) for t in ktoks))
                self.u_hdr.append(hdr_words)
                self.u_isheader.append(is_header)
                self.u_chunk.append(ci)
                self.u_ref.append((ci, len(lst)))
                lst.append({"uid": uid, "text": u, "key": key})
                uid += 1
            self.units.append(lst)
        self.nu = uid
        self.u_avg = sum(self.u_len) / max(self.nu, 1)
        self._bigram_cache = {}
        self.sem = SemanticIndex.load_or_build(chunks) if W_SEM_GENERAL > 0 else None
        self.dense = self._load_dense(chunks) if W_DENSE > 0 else None
        self.nav = [roles.is_nav_heading(c["heading"]) for c in chunks]      # contents lists repeat every heading in one short line: they point to content, they are not content

    # Load or build the dense embedding index; returns None if the embedding service is unavailable.
    @staticmethod
    def _load_dense(chunks):
        """The embedding index, or None if the embedding service is unavailable (retrieval then works as before)."""
        try:
            from welding_ai import paths
            from welding_ai.retrieval.dense import DenseIndex
            return DenseIndex.load_or_build(chunks, DENSE_MODEL, paths.INDEX_DIR)
        except Exception:
            return None

    # ------------------------------------------------------------------ unit-level scoring
    # IDF of a term over units rather than chunks.
    def _uidf(self, t):
        df = len(self.u_post.get(t, ()))
        return math.log(1 + (self.nu - df + 0.5) / (df + 0.5)) if df else 0.0

    # Tokenize and weight a query, detecting whether it's an entity (identifier) lookup.
    def _prep(self, query):
        qt = rag.tokens(query)
        w = Counter(qt)
        for t in list(w):
            if t.startswith("std"):
                w[t] *= ID_BOOST
        p = {"qt": qt, "w": w, "bi": set(zip(qt, qt[1:])), "intent": _intent_stems(query)}
        qstd = {t for t in w if t.startswith("std")}
        p["std"] = qstd
        p["entity"] = any(self.u_keyid[uid] and t in self.u_key[uid] for t in qstd for uid, _ in self.u_post.get(t, ()))
        p["hdrb"] = HDR_BONUS_ENTITY if p["entity"] else HDR_BONUS_GENERAL
        return p

    # BM25-style score of one unit against the prepared query, plus key/header/bigram/entity bonuses.
    def _score_unit(self, uid, p):
        s = 0.0
        seq = self.u_seq[uid]
        tf = Counter(seq)
        key = self.u_key[uid]
        for t, qn in p["w"].items():
            f = tf.get(t, 0) + (3 if t in key else 0)
            if not f:
                continue
            idf = self._uidf(t)
            s += idf * qn * f * (K1 + 1) / (f + K1 * (1 - B + B * self.u_len[uid] / self.u_avg))
            if t in key:
                s += KEY_BONUS * idf
        if p["bi"]:
            bg = self._bigram_cache.get(uid)
            if bg is None:
                bg = self._bigram_cache[uid] = set(zip(seq, seq[1:]))
            s += BIGRAM_BONUS * len(p["bi"] & bg)
        if s and self.u_hdr[uid] and (p["intent"] & self.u_hdr[uid]):
            s += p["hdrb"] * min(len(p["intent"] & self.u_hdr[uid]), 2)      # the row has the column the question asks for
        if s and p["entity"] and self.u_keyid[uid] and (p["std"] & key):
            s += EXACT_KEY_BONUS
        if self.u_isheader[uid]:
            s *= HEADER_ROW_FACTOR
        return s

    # Relevance of each unit of one chunk, aligned with rag.Index._units(chunk text). Used by the answer
    # composer so answers are chosen with the same relevance model that found the passage.
    def score_units(self, query, chunk_id):
        """Relevance of each unit of one chunk, aligned with rag.Index._units(chunk text)."""
        p = self._prep(query)
        return [self._score_unit(u["uid"], p) for u in self.units[chunk_id]]

    # Rank chunks by their best-scoring units (candidate units restricted to affordable postings lists).
    def _unit_ranking(self, query, top_units=80):
        p = self._prep(query)
        if not p["qt"]:
            return {}, {}, False
        w = p["w"]
        terms = sorted(w, key=lambda t: len(self.u_post.get(t, ())))
        gen = [t for t in terms if 0 < len(self.u_post.get(t, ())) <= MAX_POSTING] or [t for t in terms if t in self.u_post][:2]
        cand = {uid for t in gen for uid, _ in self.u_post[t] if not self.nav[self.u_chunk[uid]]}
        entity = p["entity"]
        scored = sorted(((self._score_unit(u, p), u) for u in cand), reverse=True)[:top_units]
        per = defaultdict(list)
        for s, u in scored:
            per[self.u_chunk[u]].append((s, u))
        agg = {ci: v[0][0] + 0.3 * (v[1][0] if len(v) > 1 else 0.0) for ci, v in per.items()}
        return agg, per, entity

    # True if the question names a standard that has its own table row (an entity lookup, not a topic question).
    def entity_query(self, query):
        """True if the question names a standard that has its own table row (an entity lookup, not a topic question)."""
        return self._prep(query)["entity"]          # cheap: only looks up the identifier's rows

    # ------------------------------------------------------------------ coverage (same definition as rag.Index)
    # Share of the query's rare-word-weighted terms present in this chunk.
    def _coverage(self, query, ci):
        qtf = Counter(rag.tokens(query))
        if not qtf:
            return 0.0
        unseen = math.log(1 + self.n) * self.UNSEEN
        weights = {t: (self.idf(t) or unseen) for t in qtf}
        tot = sum(weights.values()) or 1.0
        return sum(wt for t, wt in weights.items() if t in self.terms[ci]) / tot

    # ------------------------------------------------------------------ fused search
    # Combine chunk-level BM25, unit-level, dense and semantic rankings via reciprocal rank fusion,
    # weighting unit-level much higher when the query is an entity/identifier lookup.
    def search(self, query, k=5):
        base = [h for h in super().search(query, 40) if not self.nav[h["chunk_id"]]]                # chunk-level BM25 (with its coverage)
        agg, per, entity = self._unit_ranking(query)
        w_chunk, w_unit = (W_CHUNK_ENTITY, W_UNIT_ENTITY) if entity else (W_CHUNK_GENERAL, W_UNIT_GENERAL)
        u_rank = {ci: r for r, (ci, _) in enumerate(sorted(agg.items(), key=lambda x: -x[1]), 1)}
        s_rank = {ci: r for r, (ci, _) in enumerate(self.sem.search(query, 40), 1)} if (self.sem is not None and not entity) else {}
        d_rank = {}
        if self.dense is not None and not entity:
            try:
                d_rank = {ci: r for r, (ci, _) in enumerate(self.dense.search(query, 40), 1) if not self.nav[ci]}
            except Exception:
                d_rank = {}                                                  # embedding service down: keyword ranking only
        c_rank = {h["chunk_id"]: r for r, h in enumerate(base, 1)}
        by_id = {h["chunk_id"]: h for h in base}
        fused = defaultdict(float)
        for ci, r in c_rank.items():
            fused[ci] += w_chunk / (RRF_K + r)
        for ci, r in u_rank.items():
            fused[ci] += w_unit / (RRF_K + r)
        for ci, r in d_rank.items():
            fused[ci] += W_DENSE / (RRF_K + r)
        for ci, r in s_rank.items():
            fused[ci] += W_SEM_GENERAL / (RRF_K + r)
        out = []
        for ci in sorted(fused, key=lambda x: -fused[x])[:k]:
            h = by_id.get(ci)
            if h is None:
                c = self.chunks[ci]
                h = {"chunk_id": ci, "source": c["source"], "heading": c["heading"], "text": c["text"], "score": 0.0,
                     "coverage": round(self._coverage(query, ci), 3)}
            h = dict(h)
            h["score"] = round(fused[ci] * 1000, 3)
            h["entity_match"] = entity
            h["units"] = [{"text": self.units[ci][self.u_ref[u][1]]["text"], "score": round(s, 2)} for s, u in per.get(ci, [])[:3]]
            out.append(h)
        return out


# Load chunks.json and build a HybridIndex over it.
def load(path=None):
    """The index from chunks.json, with the passages written by hand added when the standard index is loaded (not when a file is named)."""
    chunks = json.loads((Path(path) if path else paths.INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
    if path is None:
        from welding_ai.knowledge import manual_passages
        chunks = manual_passages.merge(chunks, manual_passages.ManualPassages(paths.MANUAL_PASSAGES).listing())
    return HybridIndex(chunks)
