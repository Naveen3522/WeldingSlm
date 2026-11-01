"""The few Ollama operations a retrain needs besides chatting: free the GPU, load a model, delete one. Failures are quiet: none of them is worth failing a job over."""
import json
import urllib.request

from welding_ai.llm.ollama import OLLAMA


def _call(path, payload=None, method="POST", timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(OLLAMA + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def running():
    """The models Ollama holds in memory right now."""
    try:
        return [m["name"] for m in _call("/api/ps", method="GET").get("models", [])]
    except Exception:
        return []


def unload_all():
    """Frees the GPU for training. -> the models that were loaded."""
    names = running()
    for name in names:
        try:
            _call("/api/generate", {"model": name, "keep_alive": 0})
        except Exception:
            pass
    return names


def _base(name):
    return name.split(":")[0]


def is_embedding(name):
    """The embedding models (they turn text into vectors for the search; they never write answers) are kept loaded."""
    low = name.lower()
    return "embed" in low or low.startswith("bge")


def keep_only(active):
    """Only one answering model stays in memory: every other loaded language model is unloaded, so two never share the graphics card. The embedding model stays.
    -> the names that were unloaded."""
    gone = []
    for name in running():
        if _base(name) == _base(active) or is_embedding(name):
            continue
        try:
            _call("/api/generate", {"model": name, "keep_alive": 0})
            gone.append(name)
        except Exception:
            pass
    return gone


def partly_on_processor():
    """True when an answering model in memory does not fit the graphics card whole, so part of it runs on the processor and answers are slow."""
    try:
        models = _call("/api/ps", method="GET").get("models", [])
    except Exception:
        return False
    return any(not is_embedding(m["name"]) and m.get("size", 0) and m.get("size_vram", 0) < m["size"] * 0.98 for m in models)


def loaded_models():
    """-> the answering (language) models in memory now, without the embedding ones."""
    return [n for n in running() if not is_embedding(n)]


def warm(name):
    """Loads a model into memory ahead of the first question."""
    try:
        _call("/api/generate", {"model": name, "keep_alive": "30m", "prompt": ""}, timeout=180)
    except Exception:
        pass


def remove(name):
    """Deletes a model from Ollama (a cancelled or test model). -> True if it is gone."""
    try:
        _call("/api/delete", {"model": name}, method="DELETE")
        return True
    except Exception:
        return False
