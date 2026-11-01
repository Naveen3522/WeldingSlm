"""Turns a trained adapter into a model Ollama can serve.

    python export.py --adapter adapter_dir --name welding-slm-v3-... --work work_dir

    1. merge       the adapter into the 16-bit base weights (on the CPU: the GPU is not needed)
    2. convert     the merged model to GGUF, quantised to Q8_0, with llama.cpp's converter (run in the GGUF environment)
    3. register    `ollama create` from the same Modelfile the current model uses, with only its source file changed
    4. test        one short chat with the new model, to prove it loads and answers

It prints   STAGE {"name": ...}   lines, then   DONE {"model": name, "gguf": path}   or   FAILED {"error": ...}.
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "unsloth/llama-3.2-1b-instruct"
CONVERTER = ROOT / "llama.cpp" / "convert_hf_to_gguf.py"
GGUF_PYTHON = ROOT.parent / "venv_gguf" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
MODELFILE = ROOT / "gguf_q8" / "Modelfile"
OLLAMA = "http://127.0.0.1:11434"


def say(tag, payload):
    print(f"{tag} {json.dumps(payload)}", flush=True)


def merge(adapter, out):
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer
    model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.float16, device_map="cpu")
    merged = PeftModel.from_pretrained(model, adapter).merge_and_unload()
    Path(out).mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(out, safe_serialization=True)
    AutoTokenizer.from_pretrained(adapter).save_pretrained(out)


def convert(merged, gguf):
    done = subprocess.run([str(GGUF_PYTHON), str(CONVERTER), str(merged), "--outfile", str(gguf), "--outtype", "q8_0"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode or not Path(gguf).exists():
        raise RuntimeError("the GGUF conversion failed: " + (done.stderr.strip().splitlines() or ["no message"])[-1][:300])


def modelfile(gguf, path):
    """The production Modelfile with only its FROM line pointing at the new file, so prompt template and stop words are identical."""
    text = MODELFILE.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^FROM .*$", "FROM " + Path(gguf).resolve().as_posix(), text, count=1)
    Path(path).write_text(text, encoding="utf-8")


def register(name, path):
    done = subprocess.run(["ollama", "create", name, "-f", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode:
        raise RuntimeError("ollama create failed: " + (done.stderr.strip().splitlines() or ["no message"])[-1][:300])


def smoke(name):
    body = json.dumps({"model": name, "stream": False, "keep_alive": 0, "options": {"temperature": 0, "num_predict": 24},
                       "messages": [{"role": "user", "content": "EXCERPTS:\n[1] (document: a.docx | section: s)\nA weld is a joint.\n\nQUESTION: What is a weld?"}]}).encode()
    with urllib.request.urlopen(urllib.request.Request(OLLAMA + "/api/chat", body, {"Content-Type": "application/json"}), timeout=300) as r:
        reply = json.loads(r.read())["message"]["content"]
    if not reply.strip():
        raise RuntimeError("the new model loaded but gave an empty answer")
    return reply


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--work", required=True)
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    merged, gguf = work / "merged_16bit", work / f"{a.name}.gguf"

    say("STAGE", {"name": "merging the adapter into the model"})
    merge(a.adapter, merged)
    say("STAGE", {"name": "converting to GGUF"})
    convert(merged, gguf)
    say("STAGE", {"name": "registering with Ollama"})
    modelfile(gguf, work / "Modelfile")
    register(a.name, work / "Modelfile")
    say("STAGE", {"name": "testing the new model"})
    reply = smoke(a.name)
    say("DONE", {"model": a.name, "gguf": str(gguf), "reply": reply[:120]})


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:
        say("FAILED", {"error": f"{type(e).__name__}: {str(e)[:400]}"})
        sys.exit(1)
