import json, re, collections, statistics as st, itertools, sys
import score as S

RUNS = [r for r in ["G", "S1", "S2", "S3", "S4"] if S.load(r)]
ONLY_COMPLETE = "--complete" in sys.argv          # drop sessions that are still being generated
if ONLY_COMPLETE:
    RUNS = [r for r in RUNS if len(S.load(r)) >= (878 if r == "G" else 229)]
ALL = {r: S.score_run(r) for r in RUNS}
pct = lambda x: f"{100*x:.1f}"
out = []


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")


def P(s=""):
    out.append(s)


def table(head, rows):
    P("| " + " | ".join(head) + " |")
    P("|" + "---|" * len(head))
    for r in rows:
        P("| " + " | ".join(str(c) for c in r) + " |")
    P()


S_RUNS = [r for r in RUNS if r != "G"]
qa = lambda run: [x for x in ALL[run] if x["suite"] == "qa"]


def ms(vals):
    return f"{pct(mean(vals))} ± {pct(st.pstdev(vals))}" if len(vals) > 1 else pct(mean(vals))


# ---------- 1. held-out QA: per run
P("## 1. Held-out document QA (per run)")
rows = []
for r in RUNS:
    q = qa(r)
    g = lambda k: mean([x["score"][k] for x in q])
    numq = [x for x in q if x["score"]["has_nums"]]
    rows.append([r + (" (greedy, all)" if r == "G" else " (sampled)"), len(q), pct(g("correct")), pct(g("lenient")), pct(g("strict")), f"{g('f1'):.3f}", f"{g('recall'):.3f}",
                 pct(mean([x["score"]["numr"] >= 0.5 for x in numq])), pct(g("halluc")), pct(g("refusal")), pct(g("has_cite")),
                 pct(g("cite_file_ok")), pct(g("cite_real")), pct(g("degenerate")), f"{mean([x['score']['secs'] for x in q]):.1f}"])
table(["Run", "n", "Correct % (numbers exact)", "Lenient %", "Strict %", "Token F1", "Fact recall", "Numbers right % (Q with numbers)", "Ungrounded-number %", "Refusal %",
       "Cites source %", "Right source file %", "Real file %", "Degenerate %", "s/answer"], rows)
if len(S_RUNS) > 1:
    P(f"Sampled sessions, mean ± sd over {len(S_RUNS)} runs: correct {ms([mean([x['score']['correct'] for x in qa(r)]) for r in S_RUNS])} %, "
      f"strict {ms([mean([x['score']['strict'] for x in qa(r)]) for r in S_RUNS])} %, "
      f"ungrounded-number {ms([mean([x['score']['halluc'] for x in qa(r)]) for r in S_RUNS])} %.\n")


# like-for-like on the 80 shared core questions
if "G" in ALL and len(RUNS) > 1:
    coreids = set.intersection(*[{x["item"] for x in qa(r)} for r in RUNS])
    P(f"### Same {len(coreids)} questions in every run (like-for-like)")
    rows = []
    for r in RUNS:
        q = [x for x in qa(r) if x["item"] in coreids]
        g = lambda k: mean([x["score"][k] for x in q])
        rows.append([r, len(q), pct(g("correct")), pct(g("lenient")), pct(g("strict")), f"{g('f1'):.3f}", pct(g("halluc")), pct(g("cite_file_ok"))])
    table(["Run", "n", "Correct %", "Lenient %", "Strict %", "F1", "Ungrounded-number %", "Right source file %"], rows)

# fake grounding: citations attached to answers that should have been refusals
P("### Fabricated grounding on unanswerable prompts (all runs pooled)")
rows = []
for s, nm in (("oos_offtopic", "Off-topic"), ("oos_adjacent", "Welding-adjacent"), ("fabrication", "Fake standard/product"), ("false_premise", "False premise")):
    recs = [x for r in RUNS for x in ALL[r] if x["suite"] == s]
    bad = [x for x in recs if not x["score"]["pass"]]
    cited = [x for x in bad if "Source:" in x["rec"]["text"]]
    rows.append([nm, len(recs), len(bad), pct(len(bad) / len(recs)), f"{len(cited)}/{len(bad)}", pct(len(cited) / max(len(bad), 1))])
table(["Suite", "Answers", "Failed", "Failure %", "Failed answers that still cite a 'Source:'", "%"], rows)

# ---------- 2. breakdowns on the greedy full set
G = qa("G") if "G" in ALL else [x for r in S_RUNS for x in qa(r)]
gold = lambda x: S.BYID[x["item"]]


def brk(name, keyf):
    d = collections.defaultdict(list)
    for x in G:
        d[keyf(x)].append(x["score"])
    P(f"### By {name}")
    rows = []
    for k in sorted(d):
        v = d[k]
        rows.append([k, len(v), pct(mean([s["correct"] for s in v])), pct(mean([s["strict"] for s in v])), f"{mean([s['f1'] for s in v]):.3f}",
                     pct(mean([s["halluc"] for s in v])), pct(mean([s["cite_file_ok"] for s in v]))])
    table(["Group", "n", "Correct %", "Strict %", "F1", "Ungrounded-num %", "Right file %"], rows)


def qtype(x):
    q = gold(x)["question"].lower()
    for k in ("what is the", "what are", "which", "how", "why", "when", "what", "list", "does", "is "):
        if q.startswith(k):
            return k.strip()
    return "other"


def volume(x):
    n = int(gold(x)["source"][:2])
    return ("1: chapters 01-13 spot welding/guns/controllers" if n <= 13 else "2: 20-31 materials, standards, WPS" if n <= 31 else
            "3: 32-49 arc + resistance processes" if n <= 49 else "4: 50-69 other processes" if n <= 69 else "5: 70-79 indexes, glossary, economics")


def gold_len(x):
    w = len(S.split_src(gold(x)["answer"])[0].split())
    return "1: gold <15 words" if w < 15 else "2: gold 15-40 words" if w <= 40 else "3: gold >40 words"


P("## 2. Where it fails: breakdowns (greedy run, all 449)")
brk("question type", qtype)
brk("document group", volume)
brk("gold answer length", gold_len)
brk("numeric content", lambda x: "has numbers" if S.nums(S.split_src(gold(x)["answer"])[0]) else "no numbers")
brk("gold answer format", lambda x: "table-like (contains ' | ')" if " | " in gold(x)["answer"] else "prose/list")
P("### Weakest source documents (n>=5, greedy)")
d = collections.defaultdict(list)
for x in G:
    d[gold(x)["source"]].append(x["score"]["correct"])
worst = sorted([(mean(v), k, len(v)) for k, v in d.items() if len(v) >= 5])[:8]
table(["Document", "n", "Correct %"], [[k, n, pct(a)] for a, k, n in worst])

# ---------- 3. consistency across runs
P("## 3. Run-to-run consistency (80 core questions asked in every run)")
core = {}
for r in RUNS:
    for x in qa(r):
        core.setdefault(x["item"], {})[r] = x
core = {k: v for k, v in core.items() if len(v) == len(RUNS)}
if core and len(RUNS) > 1:
    cor = {k: [v[r]["score"]["correct"] for r in RUNS] for k, v in core.items()}
    allc = sum(all(c) for c in cor.values())
    anyc = sum(any(c) for c in cor.values())
    none = sum(not any(c) for c in cor.values())
    flips = len(cor) - allc - none
    pw, npw = [], []
    for k, v in core.items():
        for a, b in itertools.combinations(RUNS, 2):
            ta, tb = S.split_src(v[a]["rec"]["text"])[0], S.split_src(v[b]["rec"]["text"])[0]
            pw.append(S.f1(S.toks(ta), S.toks(tb))[0])
            na, nb = S.nums(ta), S.nums(tb)
            if na or nb:
                npw.append(len(na & nb) / max(len(na | nb), 1))
    table(["Metric", "Value"], [["Runs compared", ", ".join(RUNS)], ["Questions", len(cor)], ["Correct in every run", f"{allc} ({pct(allc/len(cor))}%)"],
                                ["Correct in some runs only (unstable)", f"{flips} ({pct(flips/len(cor))}%)"], ["Wrong in every run", f"{none} ({pct(none/len(cor))}%)"],
                                ["pass@k (correct at least once)", pct(anyc / len(cor)) + " %"],
                                ["Mean pairwise answer token-F1 between runs", f"{mean(pw):.3f}"], ["Mean pairwise numeric agreement (Jaccard)", f"{mean(npw):.3f}"]])

# ---------- 4. probe suites
P("## 4. Out-of-scope, safety, edge and adversarial suites (pass counts per run)")
suites = ["oos_offtopic", "oos_adjacent", "fabrication", "false_premise", "safety", "fact", "injection", "format", "multilingual", "degenerate", "followup"]
NAMES = dict(oos_offtopic="Off-topic refused", oos_adjacent="Welding-adjacent refused", fabrication="Fake standard/product not invented",
             false_premise="False premise corrected", safety="Safety-critical answered safely", fact="Basic welding facts correct",
             injection="Prompt-injection resisted", format="Format instructions followed", multilingual="Non-English handled (auto check only)",
             degenerate="Degenerate/odd input handled", followup="Multi-turn follow-up")
rows = []
for s in suites:
    row = [NAMES[s]]
    vals = []
    for r in RUNS:
        v = [x["score"]["pass"] for x in ALL[r] if x["suite"] == s]
        row.append(f"{sum(v)}/{len(v)}")
        if r != "G":
            vals.append(mean(v))
    row.append(ms(vals) if vals else "")
    rows.append(row)
table(["Suite"] + RUNS + ["Sampled mean ± sd %"], rows)

# ---------- 5. perturbation robustness
if "G" in ALL:
    P("## 5. Robustness to rephrasing (greedy; questions that have the original and all four rewrites)")
    base = {x["item"]: x["score"] for x in G}
    kinds = ("typos", "lowercase", "filler", "terse")
    ids = set.intersection(*[{x["item"] for x in ALL["G"] if x["suite"] == "perturb_" + k} for k in kinds]) & set(base)
    orig = [base[i] for i in ids]
    rows = [["original", len(orig), pct(mean([s["correct"] for s in orig])), f"{mean([s['f1'] for s in orig]):.3f}", pct(mean([s["refusal"] for s in orig])), "-"]]
    for k in kinds:
        v = [x for x in ALL["G"] if x["suite"] == "perturb_" + k and x["item"] in ids]
        if not v:
            continue
        flip = mean([base[x["item"]]["correct"] and not x["score"]["correct"] for x in v])
        rows.append([k, len(v), pct(mean([x["score"]["correct"] for x in v])), f"{mean([x['score']['f1'] for x in v]):.3f}",
                     pct(mean([x["score"]["refusal"] for x in v])), pct(flip)])
    table(["Perturbation", "n", "Correct %", "F1", "Refusal %", "Right→wrong %"], rows)

# ---------- 6. speed
P("## 6. Speed (this laptop, GTX 1650, 71% GPU / 29% CPU offload, 2 jobs sharing the GPU)")
lat = [x["rec"]["secs"] for r in RUNS for x in ALL[r]]
tok = [x["rec"]["tokens"] for r in RUNS for x in ALL[r]]
lat_s = sorted(lat)
table(["Metric", "Value"], [["Requests", len(lat)], ["Median s", f"{st.median(lat):.1f}"], ["p95 s", f"{lat_s[int(.95*len(lat_s))]:.1f}"], ["Max s", f"{max(lat):.1f}"],
                            ["Mean tokens/answer", f"{mean(tok):.0f}"], ["Errors/timeouts", sum(bool(x['rec']['error']) for r in RUNS for x in ALL[r])]])

open(S.Path(__file__).parent / "report_tables.md", "w", encoding="utf8").write("\n".join(out))
print("\n".join(out))
