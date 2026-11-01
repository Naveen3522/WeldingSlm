"""Sorts the passages of the documents by what they are, and the documents by the agent they feed. It runs on every reindex, so nothing is listed by hand:
add, change or delete a document and the agents' share of it follows.

    classify(chunk) -> "formula" | "troubleshooting" | "safety" | "procedure" | "standards" | "reference"
    sort_documents(chunks) -> {"passages": {chunk_id: type}, "documents": {source: {type: count}}, "agents": {agent: [{"source", "passages"}]}}

How a passage is typed: by its heading and its own words, counted against a small vocabulary for each type (a "Cause | Corrective action" table, "step 1 ...", "shall",
"Q = I R t where ..."). The type with the most evidence wins; a passage with too little evidence of any type is "reference". An agent gets every document that has at least
`MIN_SHARE` of its passages in one of the agent's types (or `MIN_PASSAGES` of them), so one document can feed more than one agent.
"""
import json
import re
from collections import Counter

TYPES = ("formula", "troubleshooting", "safety", "procedure", "standards", "reference")
AGENT_OF = {"troubleshooting": "fault-finder", "safety": "fault-finder", "procedure": "procedure-advisor", "standards": "procedure-advisor", "formula": "formulas"}
AGENT_NAMES = {"fault-finder": "Troubleshooter Agent", "procedure-advisor": "Procedure Advisor Agent", "formulas": "Formulas"}
MIN_SHARE, MIN_PASSAGES, MIN_EVIDENCE = 0.15, 3, 2

_EQUATION = re.compile(r"(?m)^[^\n=]{1,60}?\s=\s[^\n=]*[A-Za-z0-9)\]²³].*$")
_VOCAB = {
    "troubleshooting": re.compile(r"\b(troubleshoot\w*|fault\w*|defect\w*|cause[sd]?|corrective|root cause|symptom\w*|porosity|expulsion|spatter|remedy|fix(?:es)?|diagnos\w*|failure|alarm|error code)\b", re.I),
    "safety": re.compile(r"\b(safety|hazard\w*|lock-?out|tag-?out|interlock\w*|e-?stop|emergency stop|ppe|guard\w*|burn\w*|shock|fume\w*|eye protection|risk assessment|danger|warning)\b", re.I),
    "procedure": re.compile(r"\b(procedures?|steps?|sequence|wps|pqr|qualif\w+|set-?up|preparation|inspection|how to|instructions?|checklist|before starting|after welding)\b", re.I),
    "standards": re.compile(r"\b(aws|iso|en\s?\d|asme|din|csa|api\s?\d|clause|standards?|code|specification|shall|compl(?:y|iance)|requirement\w*|accept\w*|certif\w*)\b", re.I),
}
_NUMBERED = re.compile(r"(?m)^\s*(?:step\s*)?\d{1,2}[.)]\s+\S")


def classify(chunk):
    """The type of one passage (a chunk with `heading` and `text`)."""
    heading, text = chunk.get("heading", ""), chunk.get("text", "")
    both = heading + "\n" + text
    score = Counter()
    if _EQUATION.search(text) and (re.search(r"[×÷*/+\-^²³]|\bwhere\b", text) or re.search(r"\b(formula|equation|calculat\w+)\b", both, re.I)):
        score["formula"] += 2 + (2 if re.search(r"\bwhere\b[^\n]{3,}\bis\b", text) else 0) + (1 if re.search(r"\b(formula|equation)\b", heading, re.I) else 0)
    for kind, rx in _VOCAB.items():
        score[kind] += len(rx.findall(heading)) * 3 + min(len(rx.findall(text)), 12)
    if re.search(r"\bcause\b[^\n|]*\|[^\n]*\b(corrective|action|remedy|fix)\b", text, re.I):
        score["troubleshooting"] += 6                       # a "Cause | Corrective action" table
    steps = len(_NUMBERED.findall(text))
    if steps >= 3:
        score["procedure"] += 3 + min(steps, 6)
    if re.search(r"\b(shall|must)\b", text, re.I):
        score["standards"] += 1
    kind, evidence = max(((k, score[k]) for k in TYPES if k != "reference"), key=lambda x: x[1])
    return kind if evidence >= MIN_EVIDENCE + (2 if kind in ("procedure", "standards", "safety") else 0) else "reference"


def sort_documents(chunks):
    """-> {"passages": {chunk_id: type}, "documents": {source: {type: n}}, "agents": {agent: [{"source", "passages"}]}} for the whole index."""
    passages, documents = {}, {}
    for i, c in enumerate(chunks):
        kind = classify(c)
        passages[i] = kind
        documents.setdefault(c["source"], Counter())[kind] += 1
    agents = {}
    for source, counts in documents.items():
        total = sum(counts.values()) or 1
        for agent in set(AGENT_OF.values()):
            n = sum(v for k, v in counts.items() if AGENT_OF.get(k) == agent)
            if n >= MIN_PASSAGES or n / total >= MIN_SHARE:
                agents.setdefault(agent, []).append({"source": source, "passages": n})
    for rows in agents.values():
        rows.sort(key=lambda r: (-r["passages"], r["source"]))
    return {"passages": passages, "documents": {s: dict(c) for s, c in sorted(documents.items())}, "agents": dict(sorted(agents.items()))}


def write(sorted_docs, path):
    """Saves the sorting without the per-passage list (the index folder keeps only what a person can read)."""
    summary = {"documents": sorted_docs["documents"], "agents": {AGENT_NAMES.get(a, a): rows for a, rows in sorted_docs["agents"].items()},
               "passages_by_type": dict(Counter(sorted_docs["passages"].values()))}
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
