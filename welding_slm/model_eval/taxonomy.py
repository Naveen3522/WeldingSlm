"""Classify every wrong greedy QA answer into a failure mode (heuristic), print counts and examples."""
import re, collections, random, sys
import score as S

STD = re.compile(r"\b(?:ISO|AWS|EN|ASME|ANSI|DIN|JIS|IEC|GB|BS|CSA|SAE|API|NFPA|OSHA|RWMA)\b[\s-]*[A-Z]?\d", re.I)
rows = [x for x in S.score_run("G") if x["suite"] == "qa"]
wrong = [x for x in rows if not x["score"]["correct"]]


def mode(x):
    s, g = x["score"], S.BYID[x["item"]]
    q = g["question"].lower()
    body = S.split_src(x["rec"]["text"])[0]
    gbody = S.split_src(g["answer"])[0]
    if s["refusal"]:
        return "refused an answerable question"
    if s["degenerate"]:
        return "degenerate / truncated / repetitive"
    if STD.search(gbody) or "standard" in q or "iso" in q or "aws" in q:
        if s["has_nums"] and (s["numr"] or 0) < 1:
            return "wrong or invented standard / clause number"
    if s["has_nums"] and (s["numr"] or 0) < 1:
        return "wrong number / value / unit" if s["halluc"] or s["numr"] == 0 else "partially right numbers (missing some)"
    if s["cite_file_ok"] is False and s["has_cite"]:
        pass
    if s["len_ratio"] < 0.5:
        return "too short / incomplete"
    if re.search(r"\b(list|which .* (are|must)|what are|steps|sequence|order)\b", q) and s["recall"] < 0.5:
        return "incomplete or wrong list / enumeration"
    if s["recall"] < 0.25:
        return "unrelated or contradicts the source"
    if s["recall"] < 0.5:
        return "vague / generic (right topic, misses the specific fact)"
    return "near miss (metric-borderline; probably paraphrase)"


d = collections.defaultdict(list)
for x in wrong:
    d[mode(x)].append(x)
print(f"wrong: {len(wrong)}/{len(rows)}")
print("| Failure mode | n | % of failures | % of all questions |\n|---|---|---|---|")
for k, v in sorted(d.items(), key=lambda kv: -len(kv[1])):
    print(f"| {k} | {len(v)} | {100*len(v)/len(wrong):.1f} | {100*len(v)/len(rows):.1f} |")
rnd = random.Random(1)
if "--examples" in sys.argv:
    for k, v in sorted(d.items(), key=lambda kv: -len(kv[1])):
        print(f"\n### {k}")
        for x in rnd.sample(v, min(3, len(v))):
            g = S.BYID[x["item"]]
            print(f"Q: {g['question']}\n  GOLD : {S.split_src(g['answer'])[0][:200]}\n  MODEL: {S.split_src(x['rec']['text'])[0][:200]}")
