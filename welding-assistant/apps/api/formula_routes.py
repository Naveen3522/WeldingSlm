"""Formula routes for the server (after auth_routes.handle, so the user is signed in and has no pending password change).

    GET  /api/formulas                list (editors and administrators also see suggestions and removed ones)
    GET  /api/formulas/<id>           one formula in full, with the chunk of the document its passage is in
    POST /api/formulas/<id>/calculate {values}          the calculator
    POST /api/formulas/ask            {q, formula_id?, values?}   the formula agent: question -> formula, numbers, result
    POST /api/formulas/locate         {document, anchor}   is that passage in that document? -> {found, chunk_id}
    POST /api/formulas/suggest        {id, ...formula}  anyone: goes to the administrators
    POST /api/formulas                {id, ...formula}  editor: add
    POST /api/formulas/<id>/update | delete | restore | approve   editor     GET /api/formulas/<id>/history   editor
"""
import re

from welding_ai.accounts import levels
from welding_ai.formulas import ask as agent, simple
from welding_ai.formulas.calc import CalcError, calculate
from welding_ai.formulas.store import FormulaError

ROUTE = re.compile(r"^/api/formulas/([a-z][a-z0-9_]{2,40})(?:/(calculate|update|delete|restore|approve|history))?$")
STATUS = {"not_found": 404, "exists": 409, "from_documents": 409, "not_suggested": 409, "invalid": 422, "bad_id": 400}
MAX_QUESTION = 500
ACTIONS = ("update", "delete", "restore", "approve")


def handle(handler, store, locate, method, path, body):
    """Answers the request and returns True, or returns False if the path is not a formula route.
    locate(document, anchor) -> index of the chunk holding the passage, or None."""
    if path != "/api/formulas" and not path.startswith("/api/formulas/"):
        return False
    user, body = handler.user, body or {}
    editor = levels.allowed(user["level"], "edit_formula")
    if not levels.allowed(user["level"], "agents"):
        handler.send_json({"error": "Your role does not include formulas."}, 403)
        return True

    def reply(obj, code=200):
        handler.send_json(obj, code)
        return True

    def visible(formula):
        return editor or (formula["active"] and formula["status"] in ("documents", "admin"))

    def readable(formula_id):
        formula = store.get(formula_id)
        if not visible(formula):
            raise FormulaError("not_found", "No such formula.")
        return formula

    def with_chunk(formula):
        source = dict(formula["source"])
        source["chunk_id"] = locate(source["document"], source["anchor"]) if source.get("document") and source.get("anchor") else None
        return {**formula, "source": source}

    try:
        if path == "/api/formulas" and method == "GET":
            return reply([store.summary(f) for f in (store.everything() if editor else store.usable())])

        if path == "/api/formulas/ask" and method == "POST":
            values = body.get("values") if isinstance(body.get("values"), dict) else None
            answer = agent.ask(store.usable(), str(body.get("q", ""))[:MAX_QUESTION], formula_id=body.get("formula_id"), values=values)
            if answer["formula_id"]:
                answer["formula_name"] = store.get(answer["formula_id"])["name"]
            return reply(answer)

        if path == "/api/formulas/locate" and method == "POST":                  # does this quoted passage exist in that document? (the source picker's tick)
            document, anchor = str(body.get("document", "")), str(body.get("anchor", ""))[:600]
            chunk = locate(document, anchor) if document and anchor.strip() else None
            return reply({"found": chunk is not None, "chunk_id": chunk})

        if path == "/api/formulas/suggest" and method == "POST":
            if not levels.allowed(user["level"], "suggest_formula"):
                return reply({"error": "Your role cannot suggest formulas."}, 403)
            return reply(store.summary(store.suggest(str(body.get("id") or ""), body, user["username"])), 201)

        if path == "/api/formulas" and method == "POST":
            if not editor:
                return reply({"error": "Editors and administrators only."}, 403)
            return reply(store.summary(store.create(str(body.get("id") or ""), body, user["username"])), 201)

        m = ROUTE.match(path)
        if not m:
            handler.send_bytes(404, b"not found", "text/plain")
            return True
        formula_id, action = m.group(1), m.group(2)

        if method == "GET" and action is None:
            full = with_chunk(readable(formula_id))
            return reply({**full, "simple": simple.to_simple(full)})

        if method == "POST" and action == "calculate":
            formula = readable(formula_id)
            if not isinstance(body.get("values"), dict):
                return reply({"error": "Send the inputs as values: {symbol: number}."}, 400)
            try:
                return reply({"formula_id": formula_id, **calculate(formula, body["values"])})
            except CalcError as e:
                return reply({"error": e.message, "fields": e.fields, "missing": e.missing}, 400)

        if (method == "GET" and action == "history") or (method == "POST" and action in ACTIONS):
            if not editor:
                return reply({"error": "Editors and administrators only."}, 403)
            if action == "history":
                return reply(store.history(formula_id))
            who = user["username"]
            changed = {"update": lambda: store.update(formula_id, body, who), "delete": lambda: store.delete(formula_id, who),
                       "restore": lambda: store.restore(formula_id, who), "approve": lambda: store.approve(formula_id, who)}[action]()
            return reply(store.summary(changed))
    except FormulaError as e:
        out = {"error": e.message, "code": e.code}
        if e.problems:
            out["problems"] = e.problems
        return reply(out, STATUS.get(e.code, 400))
    handler.send_bytes(404, b"not found", "text/plain")
    return True
