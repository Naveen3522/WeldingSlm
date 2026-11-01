"""The two lists of approved question-and-answer pairs, each one JSON file in data/training/, rewritten from the database after every change.

    base training set    the data the model started from (welding_slm/v2/train.jsonl): never changed here
    training_pairs.json  approved pairs whose answer is in the documents (and, for a person's pair, that were tested): the only pairs a retrain takes
    learned_pairs.json   approved pairs people added whose answer is NOT in the documents: they answer at question time and are never trained on, because the documents are the
                         single source of truth and training would teach the model a fact as if it were general welding knowledge

A pair moves from one list to the other when the documents change or an administrator keeps it out of training. Documents are never touched: a pair that refers to a document only
records its name and the quoted text.
"""
import json
import os
import tempfile
from pathlib import Path


def rows(pairs):
    out = []
    for p in pairs:
        refs = json.loads(p["refs"]) if p.get("refs") else ([{"doc": p["doc"], "text": p["answer"]}] if p["doc"].endswith(".docx") else [])
        out.append({"id": p["id"], "question": p["question"], "answer": p["answer"], "source": "document" if refs else "hand", "documents": refs,
                    "passage": {"title": p["section"], "text": p["evidence"]} if not refs else None, "area": p.get("area", ""), "added_by": p.get("added_by", ""),
                    "approved_by": p["decided_by"], "approved_at": p["decided_at"], "trained_in": [v for v in p["used_in"].split(",") if v]})
    return out


def write(pairs, path):
    """Writes one list whole, through a temporary file, so a crash never leaves half of it. `pairs` are queue rows."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"version": 1, "pairs": rows(pairs)}, ensure_ascii=False, indent=1)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)
    return path


def write_both(queue, learned_path, training_path):
    """Rewrites both lists from the database."""
    write(queue.learned_only(), learned_path)
    write(queue.trainable(), training_path)
