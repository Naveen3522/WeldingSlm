"""What trains the SLM, and what only answers.

    GET  /api/v1/training/summary           how many training rows there are (generated from the documents, or added by an administrator) and how many learned pairs answer at question time

The question pairs people add are learned pairs: they answer through the lookup and are never trained on. Training itself is started with POST /api/refresh/start (retrain_routes.py).
"""
from welding_ai.training import baseset

from gateway import app, route


def _counts():
    q = app.queue
    rows = q.trainable()
    docs = {c["source"] for c in app.chunks() if c["source"] != "Added by hand" and not c.get("manual")}
    return {"documents": len(docs), "size": baseset.TARGET, "training": len(rows), "new": len(q.trainable(only_unused=True)), "added": sum(1 for p in rows if p["kind"] == "training"), "learned": len(q.learned_only())}


@route("GET", "/api/training/summary", right="training_pairs", doc="Training rows, learned pairs, and how many training rows are new since the last training.")
def summary(req):
    return _counts()
