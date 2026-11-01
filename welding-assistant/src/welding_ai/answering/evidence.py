"""Shared evidence store for the answer handlers: the section index (BM25 over sections), the table index, scoring helpers, and
the `Answer` container that handlers fill (ordered text / table blocks plus the passages cited)."""
from welding_ai import paths
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from welding_ai.retrieval import rag
from welding_ai.retrieval.roles import is_nav_title

ROOT = paths.ROOT
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[(\"'])")
_REF = re.compile(r"\s*\[S\d+(?:[,;]\s*S?\d+)*\]")
_DIAG_HDR = re.compile(r"symptom|observation|condition|pattern|defect|fault|failure|problem|deviation|indication|issue|alarm|event|signal", re.I)
_DIAG_ACT = re.compile(r"cause|check|action|remed|clue|path|evidence|effect|source|diagnos|response|fix|correct|detect", re.I)
_SEQ = re.compile(r"cycle|sequence|through|stage|order|progress|lifecycle|steps?|phase|flow", re.I)
_SKIP_TITLE = re.compile(r"sources referenced|references|in this chapter|contents|glossary|quick reference|known gaps|worked example|further reading", re.I)
_WEAK = {"do", "doe", "what", "how", "mean", "show", "give", "tell", "caus", "fix", "diagnos", "problem", "issu", "check", "error", "resolv", "solv",
         "troubleshoot", "diagnose", "chang", "differ", "document", "doc", "chapter", "explain", "describ", "compar", "conflict", "disagre", "requir"}


# Strip [S1]-style reference markers and trim.
def clean(s):
    return _REF.sub("", s).strip()


# Split cleaned text into sentences.
def sentences(text):
    return [s.strip() for s in _SENT.split(clean(text)) if s.strip()]


# Strip a leading "NN_" ordering prefix and turn underscores into spaces for a display title.
def nice(src):
    return re.sub(r"^\d+_", "", src.rsplit(".", 1)[0]).replace("_", " ")


# A section title with its leading numbering stripped.
def title_of(s):
    return re.sub(r"^\d+\.\s*", "", s["title"]).strip()



class Answer:
    def __init__(self, kind):
        self.kind, self.blocks, self.cites, self.missing, self.clarify = kind, [], [], [], None

    # Append a markdown text block (no-op for empty text).
    def text(self, md):
        if md:
            self.blocks.append({"k": "text", "md": md})

    # Append a table block.
    def table(self, header, rows, title="", source="", chunk=None, note=""):
        self.blocks.append({"k": "table", "title": title, "header": header, "rows": rows, "source": source, "chunk": chunk, "note": note})

    # Record a source citation for a chunk, deduplicated.
    def cite(self, source, heading, chunk):
        if chunk is None:
            return
        if not any(c["chunk"] == chunk for c in self.cites):
            self.cites.append({"source": source, "heading": heading, "chunk": chunk})

    # Serialize to a JSON-able dict (for the RPC boundary in distributed mode).
    def to_dict(self):
        return {"kind": self.kind, "blocks": self.blocks, "cites": self.cites, "missing": self.missing, "clarify": self.clarify}

    # Rebuild an Answer from its serialized dict.
    @classmethod
    def from_dict(cls, d):
        ans = cls(d["kind"])
        ans.blocks, ans.cites, ans.missing, ans.clarify = d["blocks"], d["cites"], d["missing"], d.get("clarify")
        return ans

    # Flatten the answer's blocks to plain text (for the AI-note prompt's MATERIAL).
    def plain(self):
        out = []
        for b in self.blocks:
            if b["k"] == "text":
                out.append(b["md"])
            else:
                out.append(" | ".join(b["header"]))
                out += [" | ".join(r) for r in b["rows"]]
        return "\n".join(out)


class EvidenceStore:
    """Section index, table index and the scoring helpers every handler shares."""

    # Build the section-level BM25 postings and the flat table list (with context) from sections.json.
    def __init__(self, index, sections=None, path=None):
        self.index = index
        path = Path(path) if path else paths.INDEX_DIR / "sections.json"
        self.sec = sections or json.loads(path.read_text(encoding="utf-8"))
        self.post = defaultdict(dict)
        self.slen, self.stoks = [], []
        self.tables = []                                   # every table with its context
        for s in self.sec:
            head = rag.tokens(s["path"] + " " + nice(s["source"]))
            body = []
            lead = ""
            for bi, b in enumerate(s["blocks"]):
                if b["t"] in ("p", "li"):
                    body += rag.tokens(b["text"])
                    lead = b["text"]
                else:
                    cells = " ".join(b["header"]) + " " + " ".join(" ".join(r) for r in b["rows"])
                    body += rag.tokens(cells)
                    meta = set(rag.tokens(s["path"] + " " + nice(s["source"]) + " " + " ".join(b["header"])))
                    self.tables.append({"sid": s["id"], "bi": bi, "sec": s, "b": b, "lead": lead, "meta": meta,
                                        "all": meta | set(rag.tokens(lead + " " + cells)),
                                        "keys": [set(rag.tokens(r[0])) for r in b["rows"]]})
            tf = Counter(body)
            for t in head:
                tf[t] += 3
            for t, f in tf.items():
                self.post[t][s["id"]] = f
            self.slen.append(len(body) + 3 * len(head))
            self.stoks.append(set(tf))
        self.n = len(self.sec)
        self.avg = sum(self.slen) / max(self.n, 1)
        self.by_src = defaultdict(list)
        for s in self.sec:
            self.by_src[s["source"]].append(s)
        self.nav_sec = {s["id"] for s in self.sec if is_nav_title(s["title"])}

    # IDF of a term over sections.
    def idf(self, t):
        df = len(self.post.get(t, ()))
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5)) if df else math.log(1 + self.n) * 0.6

    # BM25-ish search over sections for a list of terms, excluding navigation sections.
    def search(self, terms, k=8):
        """-> [(score, sid, cover)]; cover = share of the terms' rarity weight that the section contains."""
        terms = [t for t in dict.fromkeys(terms)]
        if not terms:
            return []
        tot = sum(self.idf(t) for t in terms) or 1.0
        cand = defaultdict(float)
        for t in terms:
            i = self.idf(t)
            for sid, f in self.post.get(t, {}).items():
                cand[sid] += i * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * self.slen[sid] / self.avg))
        out = []
        for sid, sc in cand.items():
            if sid in self.nav_sec:
                continue
            cov = sum(self.idf(t) for t in terms if t in self.stoks[sid]) / tot
            out.append((sc * (0.4 + cov), sid, cov))
        out.sort(reverse=True)
        return out[:k]

    # Share of the terms' rarity weight present in a token set.
    def _cover(self, terms, tokset):
        terms = [t for t in dict.fromkeys(terms)]
        tot = sum(self.idf(t) for t in terms) or 1.0
        return sum(self.idf(t) for t in terms if t in tokset) / tot

    # Cite the source document/section/chunk that a section block came from.
    def _cite_block(self, ans, s, b):
        ans.cite(s["source"], s["path"], b.get("chunk"))

    # Relevance score of each sentence of a text against a list of terms.
    def _sent_scores(self, terms, text):
        tot = sum(self.idf(t) for t in terms) or 1.0
        out = []
        for st in sentences(text):
            tk = set(rag.tokens(st))
            out.append((sum(self.idf(t) for t in terms if t in tk) / tot, st))
        return out

    # Drop sentences that heavily overlap a sentence already kept (near-duplicates).
    @staticmethod
    def _dedupe(sents):
        kept, seen = [], []
        for st in sents:
            tk = set(rag.tokens(st))
            if any(len(tk & o) / max(len(tk | o), 1) > 0.6 for o in seen):
                continue
            kept.append(st)
            seen.append(tk)
        return kept

    # Join the top sentences up to a sentence-count and character budget.
    def _trim(self, sents, max_sent, max_chars):
        out, n = [], 0
        for st in sents[:max_sent]:
            if n + len(st) > max_chars and out:
                break
            out.append(st)
            n += len(st)
        return " ".join(out)
