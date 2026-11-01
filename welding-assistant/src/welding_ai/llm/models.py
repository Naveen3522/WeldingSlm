"""Which models the local Ollama server has installed, as the page lists them."""
import json
import urllib.request

from welding_ai.llm.ollama import OLLAMA

MODEL_INFO = {"welding-slm-q8": {"label": "Fast (recommended)", "note": "about 2 s per answer"},
              "welding-slm": {"label": "Original (slower, not recommended)", "note": "about 6 s per answer; on a 4 GB card it measured 14-17 s and was no more accurate"},
              "welding-slm-v2": {"label": "Fine-tuned v2 (RAG reader)", "note": "about 2 s per answer"}}
HIDDEN_MODELS = {"qwen3:4b"}             # a reasoning model: writes its thinking first, far too slow for chat here


def params_billions(text):
    """"1.2B" -> 1.2, "970M" -> 0.97, anything else -> 0.0."""
    try:
        number, unit = float(text[:-1]), text[-1:].upper()
    except ValueError:
        return 0.0
    return round(number if unit == "B" else number / 1000 if unit == "M" else 0.0, 2)


# List Ollama's installed chat models (excluding embedding/hidden models), default model first.
def installed_models(default_model):
    """None when Ollama cannot be reached; otherwise [{id, label, note}] with the default model first."""
    try:
        with urllib.request.urlopen(urllib.request.Request(OLLAMA + "/api/tags"), timeout=5) as r:
            tags = json.loads(r.read())["models"]
    except Exception:
        return None
    out = []
    for tag in tags:
        n = tag["name"]
        base = n.replace(":latest", "")
        if "embed" in n or base.startswith(("bge", "nomic", "mxbai", "snowflake", "all-minilm")) or base in HIDDEN_MODELS:
            continue
        info = MODEL_INFO.get(base, {})
        out.append({"id": base, "label": info.get("label", base), "note": info.get("note", ""), "size_gb": round(tag.get("size", 0) / 1024 ** 3, 1),
                    "params_b": params_billions((tag.get("details") or {}).get("parameter_size", ""))})
    return sorted(out, key=lambda m: (m["id"] != default_model, m["id"]))
