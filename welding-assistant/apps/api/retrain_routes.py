"""Question-and-answer review and the retrain job, for the server (administrators only; after auth_routes.handle).

    GET  /api/admin/qa/summary                      counts by status, the documents pairs came from, and whether new pairs are approved automatically
    GET  /api/admin/qa?status&doc&q&offset&limit    the pairs, newest first
    POST /api/admin/qa/decide   {ids | doc, status, only_pending}     approve or reject
    POST /api/admin/qa/auto     {on}                approve new pairs automatically (they are quoted word for word) or hold them for review

    POST /api/admin/retrain/auto {on}               retrain by itself after documents have been added (on unless turned off)  (administrators)
    GET  /api/refresh/check     documents waiting, new pairs, what each kind of retrain would take, and the job (everything the refresh icon needs)
    GET  /api/refresh/status    the job alone (the page polls this while one runs)
    POST /api/refresh/start     {mode: short | full | test}    start a retrain as its own process
    POST /api/refresh/cancel    stop it and put things back
"""
from urllib.parse import parse_qs, urlparse

from welding_ai.accounts import levels
from welding_ai.training.queue import QaError

AUTO_RETRAIN = "auto_retrain"
ADMIN_ONLY = ("/api/refresh/start", "/api/refresh/cancel", "/api/admin/retrain/auto")      # everything else here is open to Managers


def _q(handler, key):
    return (parse_qs(urlparse(handler.path).query).get(key) or [""])[0].strip()


def handle(handler, queue, retrain, manifest, index_job, method, path, body, on_change=lambda: None):
    if not (path.startswith("/api/admin/qa") or path.startswith("/api/refresh") or path.startswith("/api/admin/retrain")):
        return False
    body = body or {}
    needed = "train" if path in ADMIN_ONLY else "training_pairs"
    if path == "/api/refresh/start" and str(body.get("mode", "")) == "test":
        needed = "train_test"                                                  # the test run is its own control (6.5), not a full retrain (6.4)
    if not levels.allowed(handler.user["level"], needed):
        handler.send_json({"error": "Admins only." if needed in ("train", "train_test") else "Weld Engineers and Admins only."}, 403)
        return True

    if path == "/api/admin/qa/summary" and method == "GET":
        handler.send_json({**queue.counts(), "documents": queue.documents()})
    elif path == "/api/admin/qa" and method == "GET":
        try:
            offset, limit = int(_q(handler, "offset") or 0), int(_q(handler, "limit") or 50)
        except ValueError:
            handler.send_json({"error": "offset and limit must be numbers."}, 400)
            return True
        handler.send_json(queue.page(status=_q(handler, "status"), doc=_q(handler, "doc"), text=_q(handler, "q"), offset=offset, limit=limit))
    elif path == "/api/admin/qa/decide" and method == "POST":
        try:
            ids = [int(i) for i in (body.get("ids") or [])]
            changed = queue.decide(ids, str(body.get("status", "")), handler.user["username"], doc=str(body.get("doc", "")), only_pending=bool(body.get("only_pending")), note=str(body.get("note", "")))
        except (QaError, TypeError, ValueError) as e:
            handler.send_json({"error": getattr(e, "message", "Send ids or a document, and a status.")}, 400)
            return True
        handler.send_json({"changed": changed, **queue.counts()})
    elif path == "/api/admin/qa/auto" and method == "POST":
        queue.set_auto_approve(bool(body.get("on")))
        handler.send_json(queue.counts())

    elif path == "/api/admin/retrain/auto" and method == "POST":
        queue.set_setting(AUTO_RETRAIN, "1" if body.get("on") else "0")
        handler.send_json({"auto_retrain": queue.setting(AUTO_RETRAIN, "0") == "1"})
    elif path == "/api/refresh/check" and method == "GET":
        handler.send_json({"documents": {k: v for k, v in manifest.detect().items() if k != "documents"}, "index_job": index_job.status(), "plans": retrain.plans(),
                           "qa": queue.counts(), "job": retrain.status(), "auto_retrain": queue.setting(AUTO_RETRAIN, "0") == "1"})
    elif path == "/api/refresh/status" and method == "GET":
        handler.send_json(retrain.status())
    elif path == "/api/refresh/start" and method == "POST":
        if index_job.running:
            handler.send_json({"error": "The documents are being re-indexed. Start the retrain when that has finished."}, 409)
            return True
        mode = str(body.get("mode", "short"))
        ids = None
        if body.get("ids") is not None:                                       # the ticked pairs: a short run on only those (the base training set is replayed with them)
            try:
                ids = [int(i) for i in body["ids"]]
            except (TypeError, ValueError):
                handler.send_json({"error": "Send the ids of the pairs."}, 400)
                return True
        try:
            ok, result = retrain.start(mode, ids)
        except ValueError as e:
            handler.send_json({"error": str(e)}, 400)
            return True
        if not ok:
            handler.send_json({"error": result}, 409)
        else:
            on_change()
            handler.send_json({"job": result}, 202)
    elif path == "/api/refresh/cancel" and method == "POST":
        ok, result = retrain.cancel()
        on_change()
        handler.send_json({"job": result} if ok else {"error": result}, 200 if ok else 409)
    else:
        handler.send_bytes(404, b"not found", "text/plain")
    return True
