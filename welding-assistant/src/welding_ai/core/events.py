"""The event vocabulary between the pipeline and any client (the web page, the evaluation, a remote caller).

Events are plain dicts (JSON lines on the wire); these constructors are the only place their shape is defined.

    sources  the passages an answer is based on           token    a piece of answer text
    table    a table to draw                              cite     short source tags
    clarify  options for a vague question                 ai_*     optional AI summary (start / token / drop)
    replace  swap the text shown so far                   error    a problem worth telling the user
    exact    the document text that goes with an AI answer (shown above it; dropped by `replace`)
    download a generated file to offer (label, url)
    stage    the pipeline step now running (id, label), for the "working on it" strip
    done     final event: route, timings, confidence, and the answer's `provenance` label (added by Harness.stream)
"""


# The passages an answer is based on, plus match quality and any notes/key details.
def sources(hits, used, words, match, notes, route, key_details=None, qtype=None):
    ev = {"type": "sources", "sources": hits, "used": used, "words": words, "match": match, "key_details": key_details or [], "notes": notes, "route": route}
    if qtype is not None:
        ev["qtype"] = qtype
    return ev


# A "sources" event for when nothing matched.
def no_sources(route, notes, match="n/a", words=None):
    return sources([], 0, words or [], match, notes, route)


# One piece of streamed answer text.
def token(text):
    return {"type": "token", "t": text}


# A table to render, with its source citation.
def table(title, source, chunk, header, rows, note=""):
    return {"type": "table", "title": title, "source": source, "chunk": chunk, "header": header, "rows": rows, "note": note}


# Short source tags to attach to the answer.
def cite(items):
    return {"type": "cite", "items": items}


# Disambiguation options for a vague question.
def clarify(options):
    return {"type": "clarify", "options": options}


# Swap the text shown so far for a different final answer.
def replace(text, reason):
    return {"type": "replace", "text": text, "reason": reason}


# The document's own text for the question, sent ahead of an AI answer so the reader sees both.
def exact(text):
    return {"type": "exact", "text": text}


# A file the person can download (a generated document), with the label for its button.
def download(label, url):
    return {"type": "download", "label": label, "url": url}


# The step the pipeline has just started, so the page can show where a slow answer is.
def stage(stage_id, label, detail=""):
    return {"type": "stage", "id": stage_id, "label": label, "detail": detail}


# What the question was understood to be: the kinds of task it asks for and, for each ask, what is wanted of what.
def plan(tasks, asks):
    return {"type": "plan", "tasks": tasks, "asks": asks}


# A problem worth telling the user about.
def error(message):
    return {"type": "error", "message": message}


# The final event for a request: route taken, timings, confidence and any extra fields.
def done(timings, route, **kw):
    return {"type": "done", "timings": dict(timings), "route": route, **kw}
