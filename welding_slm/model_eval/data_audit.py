import json, re, collections, statistics as st
from pathlib import Path
import score as S
B = Path(r"D:\SLM\welding_slm")
tr = [json.loads(l) for l in (B / "04_dataset.clean.jsonl").read_text(encoding="utf8").splitlines() if l.strip()]
norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
print("train rows", len(tr), "kinds", collections.Counter(r["kind"] for r in tr))
body = lambda r: S.split_src(r["answer"])[0]
w = [len(body(r).split()) for r in tr]
print("answer words: median", st.median(w), "p10", sorted(w)[len(w)//10], "p90", sorted(w)[9*len(w)//10], "max", max(w))
print("answers <8 words:", sum(x < 8 for x in w), " >100 words:", sum(x > 100 for x in w))
midword = [r for r in tr if re.match(r"^[a-z]{1,3}[a-z]*\b", body(r)) and not re.match(r"^(the|a|an|to|and|of|in|it|is|if|for|no|use|do|not|with|on|at|by|as|or|all|each)\b", body(r))]
print("answers starting lowercase (possible mid-word/mid-sentence fragment):", sum(body(r)[:1].islower() for r in tr), f"({100*sum(body(r)[:1].islower() for r in tr)/len(tr):.1f}%)")
print("answers with ' | ' table fragments:", sum(" | " in r["answer"] for r in tr))
print("answers containing 'Source:' line:", sum("Source:" in r["answer"] for r in tr))
print("refusal-style answers:", sum(S.is_refusal(body(r)) for r in tr))
print("answers w/ mojibake char:", sum("\ufffd" in r["answer"] for r in tr))
qs = collections.Counter(norm(r["question"]) for r in tr); print("duplicate questions:", sum(v - 1 for v in qs.values() if v > 1))
qa_ = collections.Counter((norm(r["question"]), norm(r["answer"])) for r in tr); print("duplicate q+a:", sum(v - 1 for v in qa_.values() if v > 1))
# same question, different answers -> conflicting supervision
byq = collections.defaultdict(set)
for r in tr: byq[norm(r["question"])].add(norm(r["answer"]))
print("questions with >1 distinct answer:", sum(len(v) > 1 for v in byq.values()))
# answer grounded in chunk?
ung = 0; n = 0; ex = []
for r in [x for x in tr if x["kind"] == "doc"][:3000]:
    c = S.CHUNKS[r["chunk_id"]]["text"]; g = S.nums(body(r)) - S.nums(c) - S.nums(r["question"])
    n += 1
    if g: ung += 1; ex.append((r["question"], body(r)[:120], sorted(g)[:4]))
print(f"train answers with numbers not in their source chunk: {ung}/{n}")
for e in ex[:5]: print("  ", e)
# coverage: chunks per doc, questions per chunk
cc = collections.Counter(r["chunk_id"] for r in tr if "chunk_id" in r); print("chunks covered", len(cc), "of", len(S.CHUNKS), "; q/chunk median", st.median(cc.values()))
src = collections.Counter(r["source"] for r in tr); print("rows per doc min/median/max", min(src.values()), st.median(src.values()), max(src.values()), "docs", len(src))
print("least-covered docs:", src.most_common()[-6:])
# question style
print("question openers:", collections.Counter(" ".join(r["question"].split()[:2]).lower() for r in tr).most_common(10))
# eval leakage: near-duplicate questions and same-chunk
ev = S.EVAL; trc = collections.defaultdict(list)
for r in tr:
    if "chunk_id" in r: trc[r["chunk_id"]].append(r)
same = sum(e["chunk_id"] in trc for e in ev); print("eval chunks also in train:", same, "/", len(ev))
sim = []
for e in ev:
    qe = set(norm(e["question"]).split())
    best = max((len(qe & set(norm(r["question"]).split())) / max(len(qe | set(norm(r["question"]).split())), 1) for r in trc.get(e["chunk_id"], [])), default=0)
    sim.append(best)
print("eval q ↔ nearest train q on same chunk (Jaccard): median", round(st.median(sim), 2), " >0.6:", sum(s > .6 for s in sim))
# answers to eval Qs already in train (answer text equal)?
tra = {norm(body(r)) for r in tr}; print("eval gold answers verbatim in train:", sum(norm(body(e)) in tra for e in ev))
