"""The training set, made fresh from the documents every time the model is trained, and always the same size.

    build(base_rows, held_out_docs, pairs, index, doc_texts, ...) -> (rows, report)

The documents are the single source of truth, so the set is never carried over as it was:
  1. PRUNE   a row of the existing set is dropped when a document it shows has been deleted, or when its answer is no longer in the document it cites (the document changed).
  2. PICK    rows from the current documents' pairs (and the rows an administrator added) are chosen with guards: never from a held-out test document, no answer too short to teach
             anything, no near-copies (questions that mean the same, found by comparing their embeddings), no more than a share from one document, no more than a share of the
             one generic question wording. An answer that only points ("Chapter 47, Section 4") has that section read and shown with it.
  3. FILL    the picked rows replace existing ones, up to a share of the set, so what is new always gets in and the model keeps its earlier behaviour; anything the prune removed is
             made up from the same pool. The result is exactly TARGET rows in the proportions of the original set.
The report says what was kept, dropped, merged and added, and why.
"""
import hashlib
import random
import re
from collections import Counter

from welding_ai.knowledge import references

TARGET = 3000
MIX = {"gold": 0.65, "wrong_passage": 0.15, "contrastive": 0.15, "empty": 0.05}     # the proportions of the original set (1950, 450, 450, 150 of 3000)
FRESH_SHARE = 0.30                    # of the answerable rows, how many may be replaced by rows from the current documents in one run
MIN_WORDS = 6                         # an answer shorter than this teaches nothing (the original set dropped rows under 8 words)
MIN_WORDS_WITH_NUMBER = 3             # ... unless it holds a figure ("4.5 kN at 1 mm")
DOC_SHARE = 0.06                      # of the answerable rows, at most this share from one document
GENERIC_SHARE = 0.25                  # ... and at most this share of the generic question wording
GENERIC = re.compile(r'^What does the documentation list under', re.I)
SAME_MEANING = 0.92                   # two questions whose embeddings are this close ask the same thing
SUPPORT = 0.6                         # of an answer's words that must still be in the document it cites
BLOCK = re.compile(r"\[(\d+)\] \(document: (.+?) \| section: (.*?)\)\n")
CITE = re.compile(r"\[(\d+)\]")
_WORD = re.compile(r"[A-Za-z0-9]+")


def _words(text):
    return {w.lower() for w in _WORD.findall(text) if len(w) > 3}


def _count(text):
    return len(_WORD.findall(text))


def _rank(text, seed):
    return hashlib.sha1(f"{seed}:{text}".encode("utf-8")).hexdigest()


def row_parts(row):
    """-> (kind, the documents its excerpts show in order, the documents its answer cites, the answer) for a training row."""
    user = next((m["content"] for m in row["messages"] if m["role"] == "user"), "")
    answer = next((m["content"] for m in row["messages"] if m["role"] == "assistant"), "")
    shown = [m.group(2) for m in BLOCK.finditer(user)]
    cited = [shown[int(n) - 1] for n in CITE.findall(answer) if 0 < int(n) <= len(shown)]
    return row.get("kind", ""), shown, cited, CITE.sub("", answer).strip()


def doc_texts(chunks):
    """-> {document: all its text} for the real documents (passages written by hand are not documents)."""
    parts = {}
    for c in chunks:
        if c["source"] != "Added by hand" and not c.get("manual"):
            parts.setdefault(c["source"], []).append(c["text"])
    return {doc: " ".join(texts) for doc, texts in parts.items()}


def held_out(dev_rows):
    """The documents the test set is made from: they are never trained on, or the test would be spoiled."""
    docs = set()
    for row in dev_rows:
        kind, shown, cited, _ = row_parts(row)
        if kind == "gold":
            docs.update(cited or shown[:1])
    return docs


def prune(base_rows, doc_texts):
    """Keeps the rows that are still true to the documents. -> (kept rows, {reason: how many})."""
    kept, why = [], Counter()
    word_sets = {}
    for row in base_rows:
        kind, shown, cited, answer = row_parts(row)
        gone = [d for d in shown if d not in doc_texts]
        if gone:
            why["a document it shows was deleted"] += 1
            continue
        if kind == "gold" and cited:
            wanted = _words(answer)
            ok = True
            for doc in set(cited):
                if doc not in word_sets:
                    word_sets[doc] = _words(doc_texts[doc])
                if wanted and len(wanted & word_sets[doc]) / len(wanted) < SUPPORT:
                    ok = False
            if not ok:
                why["its answer is no longer in the document"] += 1
                continue
        kept.append(row)
    return kept, dict(why)


def _pointer_only(answer):
    """An answer that is only a pointer ("Standard controls (Chapter 47, Section 4)"): little is left once the reference is taken out."""
    return bool(references.find(answer)) and _count(references.REF.sub("", answer)) <= 5


def select(pairs, held, doc_texts, vectors=None, chapters=None, seed=3407, target_gold=None):
    """Chooses the pairs to teach from, in priority order (not yet trained on first). -> (picked pairs, report). Each picked pair may carry "extra": the passages its pointer leads to."""
    target_gold = target_gold or round(TARGET * MIX["gold"])
    report = Counter()
    pool = []
    for p in sorted(pairs, key=lambda p: (bool(p.get("used_in")), _rank(p["question"] + p["answer"], seed))):
        if p["doc"] not in doc_texts:
            report["from a document that is gone"] += 1
        elif p["doc"] in held:
            report["from a held-out test document"] += 1
        elif not (_count(p["answer"]) >= MIN_WORDS or (re.search(r"\d", p["answer"]) and _count(p["answer"]) >= MIN_WORDS_WITH_NUMBER)):
            if _pointer_only(p["answer"]) and chapters is not None:
                refs = references.find(p["answer"])
                ids = [i for ch, sec in refs for i in chapters.resolve(ch, sec, limit=1)]
                if ids:
                    pool.append(dict(p, extra=ids[:2]))
                    report["pointer followed"] += 1
                    continue
            report["answer too short"] += 1
        else:
            extra = []
            if chapters is not None and references.find(p["answer"]):
                extra = [i for ch, sec in references.find(p["answer"]) for i in chapters.resolve(ch, sec, limit=1)][:2]
            if extra:
                report["pointer followed"] += 1
            pool.append(dict(p, extra=extra) if extra else p)
    # near-copies: questions that mean the same, by embedding, within a document
    if vectors is not None and pool:
        texts = [p["question"] + " " + p["answer"][:120] for p in pool]
        if vectors.ensure(texts) is None:
            report["near-copies not checked (the embedding model is off)"] += 1
        else:
            matrix, kept, per_doc = vectors.get(texts), [], {}
            for i, p in enumerate(pool):
                mine = per_doc.setdefault(p["doc"], [])
                if mine and float((matrix[mine] @ matrix[i]).max()) >= SAME_MEANING:
                    report["merged: asks what another already asks"] += 1
                    continue
                mine.append(i)
                kept.append(p)
            pool = kept
    picked, per_doc, generic = [], Counter(), 0
    doc_cap, generic_cap = max(1, round(target_gold * DOC_SHARE)), round(target_gold * GENERIC_SHARE)
    for p in pool:
        if per_doc[p["doc"]] >= doc_cap:
            report["over one document's share"] += 1
            continue
        if GENERIC.match(p["question"]):
            if generic >= generic_cap:
                report["over the generic wording's share"] += 1
                continue
            generic += 1
        per_doc[p["doc"]] += 1
        picked.append(p)
    return picked, dict(report)


def build(base_rows, held_docs, pairs, index, doc_texts, make_gold, make_refusals, vectors=None, chapters=None, seed=3407, target=TARGET):
    """-> (the rows, the report). `make_gold(pair)` and `make_refusals(pairs, kind, count)` build rows in the served form (training/datasets.py)."""
    rng = random.Random(seed)
    kept, dropped = prune(base_rows, doc_texts)
    quota = {k: round(target * v) for k, v in MIX.items()}
    quota["gold"] += target - sum(quota.values())                      # rounding goes to the answerable rows
    by_kind = {k: [r for r in kept if r.get("kind") == k] for k in quota}
    picked, skipped = select(pairs, held_docs, doc_texts, vectors, chapters, seed, quota["gold"])
    fresh = picked[:round(quota["gold"] * FRESH_SHARE)]                  # what is new always gets in, up to a share of the answerable rows
    rng.shuffle(by_kind["gold"])
    room = max(0, quota["gold"] - len(fresh))                            # ...and replaces existing rows, so the set stays the same size
    base_gold = by_kind["gold"][:room]
    gold = base_gold + [make_gold(p) for p in fresh]
    leftovers = iter(picked[len(fresh):])
    for p in leftovers:                                                  # rows the prune removed are made up from the same pool
        if len(gold) >= quota["gold"]:
            break
        gold.append(make_gold(p))
    rows = list(gold)
    usable = [p for p in pairs if p["doc"] in doc_texts and p["doc"] not in held_docs]
    for kind in ("wrong_passage", "contrastive", "empty"):
        have = by_kind[kind][:quota[kind]]
        rows += have
        short = quota[kind] - len(have)
        if short > 0 and usable:                                         # invented-thing rows that are gone come back as wrong-passage rows
            rows += make_refusals(usable, "empty" if kind == "empty" else "wrong_passage", short)
    rng.shuffle(rows)
    added = len(gold) - len(base_gold)
    report = {"target": target, "rows": min(len(rows), target), "base rows kept": len(kept), "base rows dropped": dropped, "base rows replaced": len(by_kind["gold"]) - len(base_gold),
              "new rows added": added, "pairs skipped": skipped, "by kind": dict(Counter(r["kind"] for r in rows[:target])), "documents": len({p["doc"] for p in picked}),
              "pool": len(picked), "short by": max(0, target - len(rows))}
    return rows[:target], report
