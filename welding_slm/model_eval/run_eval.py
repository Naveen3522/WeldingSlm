"""Generate model outputs. Usage: python run_eval.py G | S1 | S2 ...   (resumable; writes raw/<run>.jsonl)
G  = greedy reference (temp 0): all held-out QA + all probe suites + perturbations of a core subset.
Sn = simulated deployment session: deployed sampling (Modelfile: temp .6 top_p .9 top_k 40 rep 1.05), own seed,
     random mixed workload (core QA subset shared by all sessions + session-specific QA + all probes)."""
import json, random, sys, time, urllib.request, re, collections
from pathlib import Path
import cases as C

BASE = Path(r"D:\SLM\welding_slm"); OUT = Path(__file__).parent / "raw"
EVAL = [json.loads(l) for l in (BASE / "04_eval.jsonl").read_text(encoding="utf8").splitlines() if l.strip()]
for i, r in enumerate(EVAL): r["id"] = f"qa{i:03d}"
NUM_PREDICT = 320

def chat(messages, opts, timeout=180):
    body = json.dumps({"model": "welding-slm", "messages": messages, "stream": False, "options": opts}).encode()
    t = time.time()
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:11434/api/chat", body), timeout=timeout))
        return dict(text=r["message"]["content"], secs=round(time.time() - t, 2), tokens=r.get("eval_count", 0),
                    prompt_tokens=r.get("prompt_eval_count", 0), done_reason=r.get("done_reason"), error=None)
    except Exception as e:
        return dict(text="", secs=round(time.time() - t, 2), tokens=0, prompt_tokens=0, done_reason="error", error=str(e)[:200])

# ---- perturbations (deterministic per question) ----
FILLER = ["please ", "quick question: ", "hi, ", "can you tell me ", "I was wondering, "]
def typo(q, rnd):
    w = q.split(); idx = [i for i, x in enumerate(w) if len(x) > 5]
    for i in rnd.sample(idx, min(2, len(idx))):
        x = w[i]; j = rnd.randrange(1, len(x) - 2); w[i] = x[:j] + x[j + 1] + x[j] + x[j + 2:]
    return " ".join(w)
def perturb(q, kind, rnd):
    if kind == "typos": return typo(q, rnd)
    if kind == "lowercase": return re.sub(r"[^\w\s]", "", q.lower())
    if kind == "filler": return rnd.choice(FILLER) + q[0].lower() + q[1:]
    if kind == "terse": # keyword-style
        stop = {"what", "is", "the", "of", "a", "an", "for", "in", "are", "which", "how", "does", "do", "to", "and"}
        return " ".join(w for w in re.findall(r"[\w./-]+", q) if w.lower() not in stop)
    raise ValueError(kind)

def build(run):
    rnd = random.Random(1234)
    strat = collections.defaultdict(list)
    for r in EVAL: strat[r["source"]].append(r)
    order = EVAL[:]; rnd.shuffle(order)
    core = order[:80]                       # shared by every session -> per-question run-to-run variance
    tasks = []
    def add(suite, item, messages, meta=None): tasks.append(dict(suite=suite, item=item, messages=messages, meta=meta or {}))
    sysmsg = []  # system prompt comes from the Modelfile
    if run == "G":
        qa = EVAL
    else:
        n = int(run[1:]); rr = random.Random(100 + n)
        rest = order[80:]; rr.shuffle(rest)
        qa = core + rest[:40]
    for r in qa: add("qa", r["id"], [{"role": "user", "content": r["question"]}])
    for i, q in enumerate(C.OFF_TOPIC): add("oos_offtopic", f"off{i:02d}", [{"role": "user", "content": q}])
    for i, q in enumerate(C.ADJACENT): add("oos_adjacent", f"adj{i:02d}", [{"role": "user", "content": q}])
    for i, q in enumerate(C.FABRICATION): add("fabrication", f"fab{i:02d}", [{"role": "user", "content": q}])
    for i, (q, w) in enumerate(C.FALSE_PREMISE): add("false_premise", f"fp{i:02d}", [{"role": "user", "content": q}], dict(words=w))
    for i, (q, m, n_) in enumerate(C.SAFETY): add("safety", f"saf{i:02d}", [{"role": "user", "content": q}], dict(must_any=m, no_text=n_))
    for i, (q, f, b) in enumerate(C.FACTS): add("fact", f"fact{i:02d}", [{"role": "user", "content": q}], dict(facts=f, bad=b))
    for i, (q, bad, _) in enumerate(C.INJECTION): add("injection", f"inj{i:02d}", [{"role": "user", "content": q}], dict(bad=bad))
    for i, (q, k) in enumerate(C.FORMAT): add("format", f"fmt{i:02d}", [{"role": "user", "content": q}], dict(check=k))
    for i, (q, l) in enumerate(C.MULTILINGUAL): add("multilingual", f"ml_{l}", [{"role": "user", "content": q}], dict(lang=l))
    for q, k in C.DEGENERATE: add("degenerate", f"deg_{k}", [{"role": "user", "content": q}])
    for m, k in C.FOLLOWUP: add("followup", k, m)
    if run == "G":     # robustness: perturb the 80 core questions four ways
        pr = random.Random(7)
        for r in core[:40]:
            for kind in ("typos", "lowercase", "filler", "terse"):
                add("perturb_" + kind, r["id"], [{"role": "user", "content": perturb(r["question"], kind, pr)}])
    random.Random(run).shuffle(tasks)   # session order is random, like real traffic
    return tasks

def main(run):
    OUT.mkdir(exist_ok=True); f = OUT / f"{run}.jsonl"
    done = set()
    if f.exists():
        for l in f.read_text(encoding="utf8").splitlines():
            if l.strip(): j = json.loads(l); done.add((j["suite"], j["item"]))
    tasks = [t for t in build(run) if (t["suite"], t["item"]) not in done]
    opts = dict(temperature=0, seed=0, num_predict=NUM_PREDICT) if run == "G" else \
           dict(temperature=0.6, top_p=0.9, top_k=40, repeat_penalty=1.05, seed=int(run[1:]) * 1000 + 17, num_predict=NUM_PREDICT)
    print(f"{run}: {len(tasks)} to do ({len(done)} done)", flush=True); t0 = time.time()
    with f.open("a", encoding="utf8") as fh:
        for k, t in enumerate(tasks):
            res = chat(t["messages"], opts)
            fh.write(json.dumps(dict(run=run, **t, **res), ensure_ascii=False) + "\n"); fh.flush()
            if k % 20 == 0: print(f"  {k}/{len(tasks)}  {time.time()-t0:.0f}s", flush=True)
    print("finished", run, f"{time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__": main(sys.argv[1])
