"""Answers written by hand, for the server (people who may add a question pair, 7.2; everyone signed in is answered by them).

    GET  /api/answers/manual              the answers written so far (newest first)
    POST /api/answers/manual              {question, answer, area}   write one
    POST /api/answers/manual/<id>/delete  remove one
"""
import re

from welding_ai.accounts import levels
from welding_ai.answering.manual import ManualError

ROUTE = re.compile(r"^/api/answers/manual/(\d+)/delete$")


def handle(handler, manual, method, path, body):
    if not path.startswith("/api/answers/manual"):
        return False
    if not levels.allowed(handler.user["level"], "manual_answer"):
        handler.send_json({"error": "Weld Engineers and Admins only."}, 403)
        return True
    body = body or {}
    if path == "/api/answers/manual" and method == "GET":
        handler.send_json({"answers": manual.listing()})
        return True
    if path == "/api/answers/manual" and method == "POST":
        user = handler.user
        try:
            saved = manual.add(str(body.get("question", "")), str(body.get("answer", "")), str(body.get("area") or "General"), user.get("username", ""), user.get("email", ""))
        except ManualError as e:
            handler.send_json({"error": e.message, "code": e.code}, 409 if e.code == "exists" else 400)
            return True
        handler.send_json({"answer": saved})
        return True
    m = ROUTE.match(path)
    if m and method == "POST":
        if manual.remove(m.group(1)):
            handler.send_json({"ok": True})
        else:
            handler.send_json({"error": "That answer no longer exists."}, 404)
        return True
    handler.send_bytes(404, b"not found", "text/plain")
    return True
