"""Is a pair's answer in the documents? The documents are the single source of truth, so only a pair whose answer is already in them may be trained on; a fact that is not
in them (a plant detail, a number somebody knows) is answered at question time and never taught to the model, because training on it would make the model state it as if it
were general welding knowledge.

    classify(answer, texts, similarity, chunks) -> (backed, how)

    exact     the answer is found word for word in a document (ignoring spaces and case)
    meaning   the passage closest in meaning to the answer is very close (cosine MEANING or more) and holds at least COVER of the answer's words
    none      neither: the pair stays a learned pair

`texts` is {document: its text squashed} for the real documents only (not passages written by hand). `similarity` is the passage similarity (retrieval/similarity.py); without it
(the embedding model is off) only the exact check runs. Measured on the real documents: answers reworded from a document score 0.87-0.89, facts that are not in them 0.65-0.69.
"""
import re

from welding_ai.knowledge.manual_passages import SOURCE

MEANING = 0.84
COVER = 0.5
_SPACE = re.compile(r"\s+")


def squash(text):
    return _SPACE.sub(" ", text.replace("|", "/")).strip().lower()


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}


def classify(answer, texts, similarity=None, chunks=None):
    """-> (True, "exact" | "meaning") when the documents say it, else (False, "none")."""
    want = squash(answer)
    if want and any(want in text for text in texts.values()):
        return True, "exact"
    if similarity is None or chunks is None or not getattr(similarity, "ready", False):
        return False, "none"
    mine = _words(answer)
    for chunk_id, cosine in similarity.rank(answer, 5):
        chunk = chunks[chunk_id]
        if chunk["source"] == SOURCE or chunk.get("manual"):
            continue                                                     # a passage written by hand is not a document
        if cosine < MEANING:
            break                                                        # ranked best first: nothing further down can be close enough
        if mine and len(mine & _words(chunk["text"])) / len(mine) >= COVER:
            return True, "meaning"
    return False, "none"


def document_texts(chunks):
    """-> {document: its text squashed} from the index's passages, without the passages written by hand."""
    parts = {}
    for chunk in chunks:
        if chunk["source"] == SOURCE or chunk.get("manual"):
            continue
        parts.setdefault(chunk["source"], []).append(chunk["text"])
    return {doc: squash(" ".join(texts)) for doc, texts in parts.items()}
