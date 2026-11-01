"""Client for the local Ollama server (streaming chat + warm-up). Standard library only."""
import json
import os
import urllib.error
import urllib.request

OLLAMA = os.environ.get("WELD_OLLAMA_URL", "http://127.0.0.1:11434")


class LLMError(Exception):
    pass


# Turn a low-level exception into a short, user-facing message.
def _friendly(e, model):
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 404:
            return f"The model '{model}' is not installed in Ollama. Choose another model."
        return f"Ollama returned an error ({e.code})."
    if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
        return "The model took too long to answer."
    return "Could not reach Ollama. Start the Ollama app, or use Search only mode."


# Stream a chat completion from Ollama, yielding ("token", text) pieces then ("stats", dict).
def llm_stream(model, messages, opts, timeout):
    """Yield ("token", text) pieces then ("stats", dict). Raises LLMError with a user-friendly message."""
    payload = {"model": model, "messages": messages, "stream": True, "think": False, "keep_alive": "30m", "options": opts}
    req = urllib.request.Request(OLLAMA + "/api/chat", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for line in r:
                if not line.strip():
                    continue
                d = json.loads(line)
                if d.get("error"):
                    raise LLMError(str(d["error"])[:200])
                piece = (d.get("message") or {}).get("content", "")
                if piece:
                    yield "token", piece
                if d.get("done"):
                    yield "stats", d
                    return
    except LLMError:
        raise
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        if isinstance(e, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            raise
        raise LLMError(_friendly(e, model)) from e


# Trigger Ollama to load the model into memory ahead of the first real request; failures are ignored.
def warm(model, timeout=120):
    try:
        req = urllib.request.Request(OLLAMA + "/api/generate", data=json.dumps({"model": model, "keep_alive": "30m", "prompt": ""}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=timeout).read()
    except Exception:
        pass


