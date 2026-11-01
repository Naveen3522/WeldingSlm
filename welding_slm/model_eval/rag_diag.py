"""Diagnostic: does the fine-tuned model answer correctly when the source passage is put in the prompt?
   oracle    = gold chunk in the prompt (the assistant's RAG prompt)         -> ceiling for retrieval-augmented use
   distract  = an unrelated chunk in the prompt                              -> does it abstain, or hallucinate from memory?
   noctx_rag = RAG system prompt, no excerpts                                -> baseline with same prompt style
Usage: python rag_diag.py   (greedy, resumable)"""
import json, random, re, sys, time
from pathlib import Path
import importlib.util
import run_eval as R
import score as S

spec = importlib.util.spec_from_file_location("prompts_src", r"D:\SLM\welding-assistant\src\welding_ai\orchestration\prompts.py")
src = Path(r"D:\SLM\welding-assistant\src\welding_ai\orchestration\prompts.py").read_text(encoding="utf8")
RAG_SYSTEM = re.search(r'SYSTEM_PROMPT = \((.*?)\n\)', src, re.S).group(1)
RAG_SYSTEM = eval("(" + RAG_SYSTEM + ")", {"NOT_FOUND": "The documents do not contain this information."})
NOT_FOUND_HINT = "do not contain|does not contain|do not support|not support"


def focus(text, q, limit=1600):
    if len(text) <= limit:
        return text
    qw = set(re.findall(r"[a-z0-9]{4,}", q.lower()))
    lines = text.split("\n")
    sc = [len(qw & set(re.findall(r"[a-z0-9]{4,}", l.lower()))) for l in lines]
    i = max(range(len(lines)), key=lambda k: sc[k]); out = lines[i]; a, b = i - 1, i + 1
    while len(out) < limit and (a >= 0 or b < len(lines)):
        if a >= 0: out = lines[a] + "\n" + out; a -= 1
        if b < len(lines) and len(out) < limit: out = out + "\n" + lines[b]; b += 1
    return out[:limit]


def messages(q, chunk):
    ctx = f"[1] (document: {chunk['source']} | section: {chunk['heading'].split(' > ')[-1]})\n{focus(chunk['text'], q)}" if chunk else "(no excerpts)"
    return [{"role": "system", "content": RAG_SYSTEM}, {"role": "user", "content": f"EXCERPTS:\n{ctx}\n\nQUESTION: {q}"}]


def main():
    order = R.EVAL[:]; random.Random(1234).shuffle(order)
    items = order[:100]
    rnd = random.Random(9)
    out = Path(__file__).parent / "raw" / "rag_diag.jsonl"
    done = set()
    if out.exists():
        done = {(j["suite"], j["item"]) for j in map(json.loads, out.read_text(encoding="utf8").splitlines() if out.exists() else [])}
    opts = dict(temperature=0, seed=0, num_predict=320)
    tasks = []
    for e in items:
        tasks.append(("rag_oracle", e, messages(e["question"], S.CHUNKS[e["chunk_id"]])))
    for e in items[:40]:
        wrong = rnd.choice([c for c in S.CHUNKS if c["source"] != e["source"] and len(c["text"]) > 300])
        tasks.append(("rag_distract", e, messages(e["question"], wrong)))
    for e in items[:40]:
        tasks.append(("rag_noctx", e, messages(e["question"], None)))
    rnd.shuffle(tasks)
    with out.open("a", encoding="utf8") as fh:
        for k, (suite, e, msgs) in enumerate(tasks):
            if (suite, e["id"]) in done: continue
            res = R.chat(msgs, opts)
            fh.write(json.dumps(dict(run="RAG", suite=suite, item=e["id"], messages=msgs[1:], meta={}, **res), ensure_ascii=False) + "\n"); fh.flush()
            if k % 10 == 0: print(k, len(tasks), flush=True)
    print("finished")


if __name__ == "__main__":
    main()
