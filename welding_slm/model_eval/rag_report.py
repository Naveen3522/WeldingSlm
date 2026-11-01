import json, re, statistics as st
import score as S
rec = [json.loads(l) for l in open("raw/rag_diag.jsonl", encoding="utf8")]
G = {x["item"]: x["score"] for x in S.score_run("G") if x["suite"] == "qa"}
pct = lambda x: f"{100*x:.1f}"
mean = lambda v: sum(v) / len(v) if v else float("nan")
NF = re.compile(r"do(?:es)? not (?:contain|support|answer)|not (?:contain|answer)|don't (?:answer|contain)|cannot (?:be )?(?:answer|find)|no information", re.I)
def sc(r):
    x = S.score_qa(dict(text=re.sub(r"\[\d+\]", "", r["text"]), done_reason=r["done_reason"], secs=r["secs"], tokens=r["tokens"], error=r["error"], item=r["item"]))
    x["notfound"] = bool(NF.search(r["text"])) or x["refusal"]
    return x
by = {}
for r in rec: by.setdefault(r["suite"], []).append((r, sc(r)))
ids = {r["item"] for r, _ in by["rag_oracle"]}
base = [G[i] for i in ids if i in G]
rows = [["No context, Modelfile prompt (greedy, from main run)", len(base), pct(mean([s["correct"] for s in base])), pct(mean([s["lenient"] for s in base])), pct(mean([s["strict"] for s in base])), f"{mean([s['recall'] for s in base]):.3f}", pct(mean([s["halluc"] for s in base])), pct(mean([s["refusal"] for s in base]))]]
for suite, nm in (("rag_noctx", "RAG prompt, empty excerpts"), ("rag_oracle", "RAG prompt + gold passage (oracle)"), ("rag_distract", "RAG prompt + WRONG passage")):
    v = [s for _, s in by[suite]]
    rows.append([nm, len(v), pct(mean([s["correct"] for s in v])), pct(mean([s["lenient"] for s in v])), pct(mean([s["strict"] for s in v])), f"{mean([s['recall'] for s in v]):.3f}", pct(mean([s["halluc"] for s in v])), pct(mean([s["notfound"] for s in v]))])
print("| Condition | n | Correct % | Lenient % | Strict % | Fact recall | Ungrounded-number % | Says 'not in documents' % |\n|---|---|---|---|---|---|---|---|")
for r in rows: print("| " + " | ".join(map(str, r)) + " |")
# same-40 comparison oracle vs noctx
ids40 = {r["item"] for r, _ in by["rag_distract"]}
o40 = [s for r, s in by["rag_oracle"] if r["item"] in ids40]
print(f"\nSame 40 questions: oracle correct {pct(mean([s['correct'] for s in o40]))}%, lenient {pct(mean([s['lenient'] for s in o40]))}%, recall {mean([s['recall'] for s in o40]):.3f}")
# where does oracle still fail?
bad = [(r, s) for r, s in by["rag_oracle"] if not s["lenient"]]
print(f"oracle answers still not lenient-correct: {len(bad)}/{len(by['rag_oracle'])}")
import random; random.Random(2).shuffle(bad)
for r, s in bad[:6]:
    g = S.BYID[r["item"]]
    print(f"Q: {g['question'][:100]}\n  G: {S.split_src(g['answer'])[0][:150]}\n  M: {r['text'][:200]!r}")
print("median secs oracle", st.median([r["secs"] for r, _ in by["rag_oracle"]]))
