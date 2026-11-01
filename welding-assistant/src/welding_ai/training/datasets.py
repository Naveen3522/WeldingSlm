"""Training datasets for the retrain job, written in exactly the form the model is served in (the same system prompt, the same numbered excerpts).

From the approved pairs it makes
    gold          the question, the passage the answer comes from among one or two other passages, and the quoted answer citing the right number
    wrong_passage the same question with passages from other documents, answered "not in the documents"
    empty         the question with no passages at all, answered "not in the documents"
in the proportions of the v2 set, then mixes them with the existing training rows according to the mode:
    full   the existing rows and the new ones          short   the new ones plus some of the existing ones (so earlier behaviour is kept)
    test   a handful of rows, to prove the whole chain works in minutes
"""
import json
import math
import random
import time
from pathlib import Path

from welding_ai import paths
from welding_ai.answering.answering import NOT_FOUND
from welding_ai.orchestration.prompts import SYSTEM_PROMPT

WRONG_RATIO, EMPTY_RATIO = 0.23, 0.08                    # per gold row, as in the v2 set (450 and 150 per 1,950)
REPLAY_SHARE = 0.30                                      # of a short run: the existing rows kept so earlier behaviour is not forgotten
TEST_ROWS, TEST_DEV = 24, 12
EVIDENCE_CHARS = 850
MODES = ("full", "short", "test")


def datasets_dir():
    return paths.MODEL_DIR / "datasets"


def _user(blocks, question):
    if not blocks:
        body = "(no passages were found)"
    else:
        body = "\n\n".join(f"[{n}] (document: {doc} | section: {section})\n{text}" for n, (doc, section, text) in enumerate(blocks, 1))
    return f"EXCERPTS:\n{body}\n\nQUESTION: {question}"


def _row(kind, blocks, question, answer):
    return {"kind": kind, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": _user(blocks, question)}, {"role": "assistant", "content": answer}]}


def _others(index, question, avoid_doc, want, rng):
    """Passages about something else, for a distractor: the search's best hits from other documents first, then any other chunk."""
    blocks = []
    for h in index.search(question, 12):
        if h["source"] != avoid_doc and len(blocks) < want:
            blocks.append((h["source"], h["heading"].split(" > ")[-1], index.focus(h["text"], question, EVIDENCE_CHARS)))
    while len(blocks) < want:
        c = index.chunks[rng.randrange(len(index.chunks))]
        if c["source"] != avoid_doc:
            blocks.append((c["source"], c["heading"].split(" > ")[-1], c["text"][:EVIDENCE_CHARS]))
    return blocks


def rows_for(approved, index, rng):
    """-> [training rows] for approved pairs (gold, plus the refusal rows that go with them)."""
    gold, wrong, empty = [], [], []
    for p in approved:
        target = (p["doc"], p["section"], p["evidence"])
        blocks = [target] + _others(index, p["question"], p["doc"], rng.choice((1, 1, 2)), rng)
        rng.shuffle(blocks)
        gold.append(_row("gold", blocks, p["question"], f"{p['answer']} [{blocks.index(target) + 1}]"))
    for p in rng.sample(approved, min(len(approved), round(len(approved) * WRONG_RATIO))):
        wrong.append(_row("wrong_passage", _others(index, p["question"], p["doc"], rng.choice((1, 2)), rng), p["question"], NOT_FOUND))
    for p in rng.sample(approved, min(len(approved), round(len(approved) * EMPTY_RATIO))):
        empty.append(_row("empty", [], p["question"], NOT_FOUND))
    return gold + wrong + empty


def _read(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _write(path, rows):
    Path(path).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _pointer_blocks(index, ids):
    return [(index.chunks[i]["source"], index.chunks[i]["heading"].split(" > ")[-1], index.chunks[i]["text"][:EVIDENCE_CHARS]) for i in ids]


def fresh_set(approved, index, base_train, base_dev, rng, seed, vectors=None):
    """The training set made fresh from the documents as they are now: always the same size, never carried over as it was (training/baseset.py).
    -> (train rows, dev rows, report)."""
    from welding_ai.knowledge import references
    from welding_ai.training import baseset
    base, dev = _read(base_train), _read(base_dev)
    texts = baseset.doc_texts(index.chunks)
    held = baseset.held_out(dev)
    dev = [r for r in dev if all(d in texts for d in baseset.row_parts(r)[1])]            # a test question about a deleted document is no test

    def make_gold(p):
        target = (p["doc"], p["section"], p["evidence"])
        extra = _pointer_blocks(index, p.get("extra") or [])
        blocks = [target] + extra + _others(index, p["question"], p["doc"], rng.choice((1, 1, 2)), rng)
        rng.shuffle(blocks)
        cites = "".join(f"[{blocks.index(b) + 1}]" for b in [target] + extra)
        return _row("gold", blocks, p["question"], f"{p['answer']} {cites}")

    def make_refusals(pairs, kind, count):
        chosen = [pairs[rng.randrange(len(pairs))] for _ in range(count)]
        return [_row(kind, [] if kind == "empty" else _others(index, p["question"], p["doc"], rng.choice((1, 2)), rng), p["question"], NOT_FOUND) for p in chosen]

    train, report = baseset.build(base, held, approved, index, texts, make_gold, make_refusals, vectors, references.Chapters(index.chunks), seed)
    return train, dev, report


def make(approved, index, mode, base_train, base_dev, out_root=None, seed=3407, clock=time.time, vectors=None):
    """Writes datasets/<version>/{train,dev}.jsonl and meta.json. -> meta (with "dir"). Raises ValueError when there is nothing to train on.
    full and short both make the fresh set of exactly baseset.TARGET rows; short only lets the pairs not trained on yet in (approved is then just those)."""
    if mode not in MODES:
        raise ValueError("The mode must be full, short or test.")
    if mode != "full" and not approved:
        raise ValueError("There are no approved question-and-answer pairs to train on.")
    rng = random.Random(seed)
    report = None
    if mode == "test":                                                   # a handful of rows, to prove the chain works
        new = rows_for(approved, index, rng)
        base, dev = _read(base_train), _read(base_dev)
        rng.shuffle(new)
        train = new[:TEST_ROWS]
        replay = max(0, min(len(base), TEST_ROWS - len(train)))
        train += rng.sample(base, replay)
        dev = rng.sample(dev, min(TEST_DEV, len(dev)))
        added = min(len(new), len(train))
    else:
        train, dev, report = fresh_set(approved, index, base_train, base_dev, rng, seed, vectors)
        added, replay = report["new rows added"], len(train) - report["new rows added"]
    rng.shuffle(train)
    version = "v3-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(clock()))
    out = Path(out_root or datasets_dir()) / version
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "train.jsonl", train)
    _write(out / "dev.jsonl", dev)
    kinds = {k: sum(1 for r in train if r["kind"] == k) for k in ("gold", "contrastive", "wrong_passage", "empty")}
    meta = {"version": version, "mode": mode, "rows": len(train), "dev_rows": len(dev), "new_rows": added, "replay_rows": replay,
            "pairs": added if report else len(approved), "documents": sorted({p["doc"] for p in approved}), "kinds": kinds, "created": clock(), "dir": str(out)}
    if report:
        meta["report"] = report
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta
