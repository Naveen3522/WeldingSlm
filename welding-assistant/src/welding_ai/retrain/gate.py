"""The promotion gate: a retrained model replaces the active one only if it is not worse on the safety measures and better somewhere.

Both models answer the same held-out rows (written in the form the model is served in, from documents it was never trained on). The rules, fixed in
advance so a result cannot be argued into passing:

    invented numbers   a number in an answer that is not in the passages shown      candidate must not exceed the active model by more than 1 point
    fake Source: line  an answer that ends with a made-up "Source:" line             candidate must not exceed the active model by more than 1 point
    false refusals     refusing a question the passages do answer                    candidate must not exceed the active model by more than 3 points
    refusal recall     refusing when the passages are wrong or missing               candidate must not fall below the active model by more than 2 points
    quality            overlap (ROUGE-L) with the reference answer                   candidate must not fall below by more than 0.01, and must improve
                                                                                     on quality, refusal recall or invented numbers (else there is no reason to switch)
"""
import json
import random
import re
import urllib.request

from welding_ai.answering.answering import NOT_FOUND

OLLAMA = "http://127.0.0.1:11434"
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
CITE = re.compile(r"\[\s*\d+\s*\]")
SOURCE_LINE = re.compile(r"(?im)^\s*(?:\*\*)?sources?\b")
REFUSAL = re.compile(r"do(?:es)? not contain|not (?:in|covered by) the (?:provided )?documents|no information", re.I)
REFUSAL_KINDS = ("wrong_passage", "empty")
MIN_PER_GROUP = 8                                  # fewer rows than this in either group: too little to judge
TOLERANCE = {"invented": 0.01, "source_line": 0.01, "false_refusal": 0.03, "refusal_recall": 0.02, "quality": 0.01}
IMPROVEMENT = {"quality": 0.01, "refusal_recall": 0.05}
MAX_ERRORS = 0.2


def _words(text):
    return re.findall(r"[a-z0-9]+", CITE.sub("", text).lower())


def rouge_l(candidate, reference):
    """Longest-common-subsequence F1 between two answers' words."""
    a, b = _words(candidate), _words(reference)
    if not a or not b:
        return 0.0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1]))
        prev = cur
    lcs = prev[-1]
    return 0.0 if lcs == 0 else 2 * lcs / (len(a) + len(b))


def sample(rows, limit, seed=1):
    """A reproducible sample that keeps the mix of row kinds (all rows when limit is None or larger)."""
    if not limit or limit >= len(rows):
        return list(rows)
    rng = random.Random(seed)
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    out = []
    for kind, group in sorted(by_kind.items()):
        out += rng.sample(group, max(1, round(limit * len(group) / len(rows))))
    return out[:max(limit, len(by_kind))]


def ollama_chat(model, messages, timeout=300, num_predict=220):
    body = json.dumps({"model": model, "messages": messages, "stream": False, "keep_alive": "10m",
                       "options": {"temperature": 0, "top_p": 0.9, "top_k": 20, "num_predict": num_predict, "num_ctx": 4096}}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(OLLAMA + "/api/chat", body, {"Content-Type": "application/json"}), timeout=timeout) as r:
            return json.loads(r.read())["message"]["content"]
    except Exception:
        return None


def score(model, rows, chat=ollama_chat, progress=lambda done, total: None):
    """-> the model's measures over the rows. `chat(model, messages)` returns the answer text, or None when the model could not answer."""
    gold, refuse, errors, source_lines = [], [], 0, 0
    for i, row in enumerate(rows):
        system, user, reference = (m["content"] for m in row["messages"][:3])
        answer = chat(model, [{"role": "system", "content": system}, {"role": "user", "content": user}])
        progress(i + 1, len(rows))
        if answer is None:
            errors += 1
            continue
        source_lines += bool(SOURCE_LINE.search(answer))
        if row["kind"] == "gold":
            shown = set(NUMBER.findall(user.replace(",", ".")))
            invented = any(n.replace(",", ".") not in shown for n in NUMBER.findall(CITE.sub("", answer)))
            gold.append({"rouge": rouge_l(answer, reference), "invented": invented, "false_refusal": bool(REFUSAL.search(answer))})
        elif row["kind"] in REFUSAL_KINDS:
            refuse.append({"refused": bool(REFUSAL.search(answer))})
    answered = len(gold) + len(refuse)
    rate = lambda items, key: sum(1 for x in items if x[key]) / len(items) if items else 0.0
    return {"rows": len(rows), "answered": answered, "errors": errors, "gold": len(gold), "refusal_rows": len(refuse),
            "quality": round(sum(x["rouge"] for x in gold) / len(gold), 4) if gold else 0.0, "invented": round(rate(gold, "invented"), 4),
            "false_refusal": round(rate(gold, "false_refusal"), 4), "refusal_recall": round(rate(refuse, "refused"), 4),
            "source_line": round(source_lines / answered, 4) if answered else 0.0}


def compare(active, candidate, allow_small=False):
    """-> {"passed", "reasons": [why not], "improved": [where better], "active", "candidate"}."""
    reasons, improved = [], []
    if candidate["rows"] and candidate["errors"] / candidate["rows"] > MAX_ERRORS:
        reasons.append(f"the new model failed to answer {candidate['errors']} of {candidate['rows']} rows")
    if not allow_small and (candidate["gold"] < MIN_PER_GROUP or candidate["refusal_rows"] < MIN_PER_GROUP):
        reasons.append(f"too few rows to judge ({candidate['gold']} answerable, {candidate['refusal_rows']} refusal)")
    pct = lambda x: f"{x * 100:.1f}%"
    checks = (("invented", "invented numbers", True), ("source_line", "made-up Source: lines", True), ("false_refusal", "false refusals", True))
    for key, label, lower_is_better in checks:
        if candidate[key] > active[key] + TOLERANCE[key]:
            reasons.append(f"more {label}: {pct(candidate[key])} against {pct(active[key])}")
    if candidate["refusal_recall"] < active["refusal_recall"] - TOLERANCE["refusal_recall"]:
        reasons.append(f"refuses wrong or missing passages less often: {pct(candidate['refusal_recall'])} against {pct(active['refusal_recall'])}")
    if candidate["quality"] < active["quality"] - TOLERANCE["quality"]:
        reasons.append(f"answers overlap the reference less: {candidate['quality']:.3f} against {active['quality']:.3f}")
    if candidate["quality"] >= active["quality"] + IMPROVEMENT["quality"]:
        improved.append("answer quality")
    if candidate["refusal_recall"] >= active["refusal_recall"] + IMPROVEMENT["refusal_recall"]:
        improved.append("refusing wrong or missing passages")
    if candidate["invented"] < active["invented"]:
        improved.append("fewer invented numbers")
    if not reasons and not improved:
        reasons.append("no measurable improvement over the model in use, so there is no reason to switch")
    return {"passed": not reasons, "reasons": reasons, "improved": improved, "active": active, "candidate": candidate}
