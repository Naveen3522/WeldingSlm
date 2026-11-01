"""A person's own question and answer pairs: send one for approval, see what happened to each, and send one again when an administrator asked for changes.

    GET  /api/v1/my/pairs                    the pairs this person sent, newest first, each with its status (pending, approved, rejected, changes) and the administrator's note
    POST /api/v1/my/pairs                    {question, refs: [{doc, text}] | answer + passage_title + passage_text, area}  send one: it is Waiting, and used for nothing until approved
    POST /api/v1/my/pairs/{id}/resend        {question, answer}  correct a pair that is in "changes" and send it again
    POST /api/v1/my/pairs/delete             {ids}  delete your own pairs that are not approved (an approved pair is removed by an administrator)

An approved pair joins the learned pairs (see welding_ai/training/learned.py); an own passage is added to the knowledge base only when the pair is approved (server.py).
"""
import re

from welding_ai.accounts import levels  # noqa: F401  (rights are named in gateway.route)
from welding_ai.knowledge.manual_passages import SOURCE

from gateway import ApiError, app, route
from hand_routes import AREAS
from model_check_routes import pair_from_documents


@route("GET", "/api/my/pairs", right="my_pairs", query="offset, limit", doc="The pairs you sent, newest first, a page at a time, with what happened to each.")
def mine(req):
    offset, limit = req.page(default=25, high=100)
    everything = app.queue.mine(req.user["username"])
    return {"pairs": everything[offset:offset + limit], "total": len(everything), "areas": list(AREAS)}


@route("POST", "/api/my/pairs", right="suggest_pair", doc="Send a question and answer for approval.")
def send(req):
    question, area = str(req.body.get("question", "")).strip(), str(req.body.get("area") or AREAS[0])
    if area not in AREAS:
        raise ApiError(400, "Choose one of: " + ", ".join(AREAS) + ".")
    if req.body.get("refs"):
        pair, problem = pair_from_documents(question, req.body, app.chunks())
        if problem:
            raise ApiError(400, problem)
    else:
        title, text, answer = " ".join(str(req.body.get("passage_title", "")).split()), str(req.body.get("passage_text", "")).strip(), str(req.body.get("answer", "")).strip()
        if not 5 <= len(question) <= 300 or not answer:
            raise ApiError(400, "Write the question (5 to 300 characters) and the answer.")
        if not 3 <= len(title) <= 120 or not 20 <= len(text) <= 3000:
            raise ApiError(400, "Write the passage: a title of 3 to 120 characters and a text of 20 to 3000.")
        pair = {"doc": SOURCE, "section": title, "question": question, "answer": answer, "evidence": text}
    pair["area"] = area
    pair_id = app.queue.suggest(pair, req.user["username"])
    if pair_id is None:
        raise ApiError(409, "This question and answer have already been sent.", "exists")
    return {"ok": True, "id": pair_id}


def draft_question(draft_name, first_head, field, column):
    """The question a draft cell answers, worded so it makes sense on its own: 'What is the Remarks for Gap we leave in the Line 4 setup draft?'. A row named by a number (Sl.No 2)
    is asked as 'at Sl.No 2'; the column title 'Value' is left out ('What is the Gap we leave ...')."""
    name = re.sub(r"\s+draft$", "", draft_name.strip(), flags=re.I) or draft_name.strip()
    where = f"in the {name} draft"
    if re.fullmatch(r"[\d.]+", field):
        return f"What is the {column} at {first_head} {field} {where}?"
    if column.strip().lower() in ("value", "values", ""):
        return f"What is the {field} {where}?"
    return f"What is the {column} for {field} {where}?"


@route("POST", "/api/my/pairs/from-draft", right="suggest_pair", doc="Send one filled cell of a draft as a question and answer for approval.")
def from_draft(req):
    """{draft_id, row, col, question?, area?}: the cell is the answer, its row is the passage. It waits for an administrator like any pair, and once approved it is only looked up
    (a learned pair): a draft is a person's own entry, so it is never trained on."""
    from welding_ai.forms.store import FormError
    try:
        draft = app.forms.get_draft(int(req.body.get("draft_id")))
        row, col = int(req.body.get("row")), int(req.body.get("col"))
        fields = draft["rows"][row]
        heads, value, field = draft["heads"], str(fields[col]).strip(), str(fields[0]).strip()
    except (FormError, TypeError, ValueError, IndexError):
        raise ApiError(400, "Choose a filled cell of a draft.")
    if col == 0 or not value or not field:
        raise ApiError(400, "Choose a filled cell that is not the field name.")
    area = str(req.body.get("area") or AREAS[0])
    if area not in AREAS:
        raise ApiError(400, "Choose one of: " + ", ".join(AREAS) + ".")
    column = str(heads[col]) if col < len(heads) else ""
    question = " ".join(str(req.body.get("question") or draft_question(draft["name"], str(heads[0]), field, column)).split())
    text = "; ".join(f"{h}: {v}" for h, v in zip(heads, fields) if str(v).strip())
    title = f"Draft {draft['name']}: {field}"[:120]
    if not 5 <= len(question) <= 300 or len(value) > 1000 or not 20 <= len(text) <= 3000:
        raise ApiError(400, "That cell is too short or too long to send as a question and answer.")
    pair = {"doc": SOURCE, "section": title, "question": question, "answer": value, "evidence": text, "area": area,
            "draft": {"id": draft["id"], "name": draft["name"], "template": draft["template"], "field": field, "column": str(heads[col]) if col < len(heads) else ""}}
    pair_id = app.queue.suggest(pair, req.user["username"])
    if pair_id is None:
        raise ApiError(409, "This question and answer have already been sent.", "exists")
    return {"ok": True, "id": pair_id}


@route("POST", "/api/my/pairs/delete", right="suggest_pair", doc="Delete several of your own pairs that are not approved.")
def delete(req):
    try:
        ids = {int(i) for i in req.body.get("ids") or []}
    except (TypeError, ValueError):
        raise ApiError(400, "Send the ids of the pairs.")
    mine = {p["id"]: p for p in app.queue.mine(req.user["username"])}
    chosen = [i for i in ids if i in mine and mine[i]["status"] != "approved"]
    gone = app.queue.delete(chosen) if chosen else 0
    return {"deleted": gone, "left": len(ids) - gone}


@route("POST", "/api/my/pairs/{id}/edit", right="suggest_pair", doc="Change the question (and the answer, when it was written by hand) of your own pair that is not approved; it waits again.")
def edit(req):
    pair = app.queue.get(req.params["id"]) if req.params["id"].isdigit() else None
    if not pair or pair["added_by"] != req.user["username"]:
        raise ApiError(404, "That pair is not yours.")
    if pair["status"] == "approved":
        raise ApiError(409, "An approved pair is changed by an administrator.")
    question, answer = str(req.body.get("question", "")).strip(), str(req.body.get("answer", pair["answer"])).strip()
    if not 5 <= len(question) <= 300 or not answer:
        raise ApiError(400, "Write the question (5 to 300 characters) and the answer.")
    if pair["refs"] or pair.get("draft"):
        answer = pair["answer"]                                           # an answer picked from documents or a draft cell stays as picked
    if not app.queue.edit_own(pair["id"], req.user["username"], question, answer):
        raise ApiError(409, "That pair could not be changed.")
    return {"ok": True}


@route("POST", "/api/my/pairs/{id}/resend", right="suggest_pair", doc="Correct a pair an administrator asked you to change, and send it again.")
def resend(req):
    pair = app.queue.get(req.params["id"]) if req.params["id"].isdigit() else None
    if not pair or pair["added_by"] != req.user["username"]:
        raise ApiError(404, "That pair is not yours.")
    if pair["status"] != "changes":
        raise ApiError(409, "Only a pair the administrator asked you to change can be sent again.")
    question, answer = str(req.body.get("question", "")).strip(), str(req.body.get("answer", pair["answer"])).strip()
    if not 5 <= len(question) <= 300 or not answer:
        raise ApiError(400, "Write the question (5 to 300 characters) and the answer.")
    if pair["refs"]:
        answer = pair["answer"]                                           # an answer picked from documents stays as picked
    if not app.queue.resend(pair["id"], req.user["username"], question, answer):
        raise ApiError(409, "That pair could not be sent again.")
    return {"ok": True}
