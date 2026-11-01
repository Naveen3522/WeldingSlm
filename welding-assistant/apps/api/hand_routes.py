"""Question pairs written by hand, with the passage each answer comes from, for the server (Weld Engineers and Admins; the agent questions are open to everyone signed in).

    POST /api/admin/qa/hand            {question, answer, passage_title, passage_text, area}   a pair and its passage; the passage joins the library's search at once
    GET  /api/admin/qa/hand            the pairs written by hand, each with its passage, the agent it is shown under and whether it was saved as Excel
    GET  /api/admin/qa/hand.xlsx       them as an Excel file (this marks them as saved)
    POST /api/admin/qa/hand/<id>/delete   one pair (its passage stays in the library)
    GET  /api/agents/questions?area=Fault%20Finder    the questions written by hand for that agent: the "More questions" of its card

A retrain deletes the pairs written by hand (the passages stay); the page offers the Excel file first. Nothing here reads the documents again: after a passage is added the
search index is built again from what is on disk (`on_change`), which takes seconds.
"""
import re
import time
from urllib.parse import parse_qs, urlparse

from welding_ai.accounts import levels
from welding_ai.core import xlsx
from welding_ai.knowledge.manual_passages import SOURCE, PassageError
from welding_ai.training.queue import QaError

AREAS = ("Chat", "Fault Finder", "Procedure Advisor")          # where a question is shown ("Chat" is the default: the main chat's suggestions)
SAVED = "hand_saved_at"
DELETE = re.compile(r"^/api/admin/qa/hand/(\d+)/delete$")
COLUMNS = ["Question", "Answer", "Passage title", "Passage text", "Shown in", "Added by", "Added on"]


def stamp(seconds):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(seconds))


def rows(queue, checks, passages):
    saved = float(queue.setting(SAVED, "0") or 0)
    areas = checks.areas()
    out = []
    for p in queue.by_kind("manual"):
        out.append({"id": p["id"], "question": p["question"], "answer": p["answer"], "passage_title": p["section"], "passage_text": p["evidence"], "area": areas.get(p["uid"], AREAS[0]),
                    "by": p["decided_by"] if p["decided_by"] != "automatic" else "", "created": p["created"], "saved": bool(saved) and p["created"] <= saved, "saved_at": saved})
    return out


def handle(handler, queue, checks, passages, method, path, body, on_change=lambda: None, clock=time.time):
    if path == "/api/agents/questions" and method == "GET":
        area = (parse_qs(urlparse(handler.path).query).get("area") or [""])[0]
        shown = [r["question"] for r in rows(queue, checks, passages) if r["area"] == area]
        handler.send_json({"questions": shown})
        return True
    if not path.startswith("/api/admin/qa/hand"):
        return False
    level = handler.user["level"]
    # The Excel file is what a reindex warning offers, so whoever may reindex may also save it, even without access to the pairs page.
    if not (levels.allowed(level, "training_pairs") or (path == "/api/admin/qa/hand.xlsx" and levels.allowed(level, "reindex"))):
        handler.send_json({"error": "Weld Engineers and Admins only."}, 403)
        return True
    if path == "/api/admin/qa/hand" and method == "POST" and not levels.allowed(level, "pair_add"):
        handler.send_json({"error": "You are not allowed to add a pair."}, 403)
        return True
    body = body or {}

    if path == "/api/admin/qa/hand" and method == "GET":
        handler.send_json({"pairs": rows(queue, checks, passages), "areas": list(AREAS)})
        return True

    if path == "/api/admin/qa/hand.xlsx" and method == "GET":
        data = xlsx.write([COLUMNS] + [[r["question"], r["answer"], r["passage_title"], r["passage_text"], r["area"], r["by"], stamp(r["created"])] for r in rows(queue, checks, passages)],
                          widths=[40, 60, 30, 70, 18, 16, 18])
        queue.set_setting(SAVED, clock())
        handler.send_bytes(200, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           {"Content-Disposition": 'attachment; filename="by-hand-pairs.xlsx"'})
        return True

    if path == "/api/admin/qa/hand" and method == "POST":
        question, answer = str(body.get("question", "")).strip(), str(body.get("answer", "")).strip()
        area = str(body.get("area") or AREAS[0])
        if area not in AREAS:
            handler.send_json({"error": "Choose one of: " + ", ".join(AREAS) + "."}, 400)
        elif not (5 <= len(question) <= 300 and answer):
            handler.send_json({"error": "Write the question (5 to 300 characters) and the answer."}, 400)
        else:
            try:
                passage = passages.add(str(body.get("passage_title", "")), str(body.get("passage_text", "")), handler.user["username"])
            except PassageError as e:
                handler.send_json({"error": e.message}, 400)
                return True
            made = queue.add_many([{"doc": SOURCE, "section": passage["title"], "question": question, "answer": answer, "evidence": passage["text"][:850], "kind": "manual"}],
                                  user=handler.user["username"])
            if not made["added"]:
                handler.send_json({"error": "This question and answer are already there."}, 409)
                return True
            mine = next(p for p in queue.by_kind("manual") if (p["doc"], p["question"], p["answer"]) == (SOURCE, question, answer))
            queue.decide([mine["id"]], "approved", handler.user["username"])
            checks.save({mine["uid"]: "found"})
            checks.set_area(mine["uid"], area)
            on_change()
            handler.send_json({"ok": True, "id": mine["id"]}, 201)
        return True

    m = DELETE.match(path)
    if m and method == "POST":
        try:
            gone = queue.delete([int(m.group(1))])
        except (QaError, ValueError):
            gone = 0
        handler.send_json({"ok": bool(gone)} if gone else {"error": "That pair no longer exists."}, 200 if gone else 404)
        return True
    handler.send_bytes(404, b"not found", "text/plain")
    return True
