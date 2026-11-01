"""Model names and the active model, for the server (administrators only; after auth_routes.handle).

    GET  /api/admin/models                  every installed model with its name, notes, dataset, gate scores and whether it is active
    POST /api/admin/models/<id>/rename      {display_name, notes}   (an empty name goes back to the built-in one)
    POST /api/admin/models/<id>/activate    makes it the model used when a person has not picked one
    POST /api/admin/models/<id>/delete      removes a retrained model from Ollama (never the active one, never one that was not made by a retrain)

The model picker everyone sees comes from /api/status, which uses the same names.
"""
import re

from welding_ai.accounts import levels

from welding_ai.llm.models import installed_models
from welding_ai.llm.registry import ModelError
from welding_ai.system import specs as sp

ROUTE = re.compile(r"^/api/admin/models/([A-Za-z0-9._:-]{1,80})/(rename|activate|delete)$")
STATUS = {"is_active": 409, "not_deletable": 409, "bad_name": 400, "bad_notes": 400, "name_taken": 409, "not_installed": 404, "offline": 503}


BUILTIN = "welding-slm-q8"           # the model that comes with the app and is never changed


def shown(models, builtin=BUILTIN):
    """The two models the page lists: the trained one (the model in use when a retrain made it, else the newest one that is not hidden) and the built-in one."""
    trained = [m for m in models if m["dataset"] and not m["hidden"]]
    pick = next((m for m in trained if m["active"]), None) or max(trained, key=lambda m: m["updated"], default=None)
    built = next((m for m in models if m["id"] == builtin), None)
    return ([dict(pick, kind="trained")] if pick else []) + ([dict(built, kind="builtin")] if built else [])


def listing(registry, default, specs=None):
    """-> [models with names applied] or None when Ollama cannot be reached. With the specs of this computer (after a system check) each model also says whether it fits."""
    installed = installed_models(default)
    if installed is None:
        return None
    models = registry.describe(installed, default)
    for m in models:
        m["fit"] = sp.assess(specs, m["params_b"])["verdict"] if specs and m.get("params_b") else ""
    return models


def handle(handler, registry, config, method, path, body, on_activate=lambda model: None, remove=None, specs=lambda: None):
    if not path.startswith("/api/admin/models"):
        return False
    right = "view_models" if method == "GET" else "model_edit" if path.endswith(("/rename", "/delete")) else "models"        # 6.1 view, 6.3 rename or delete, 6.2 use a model
    if not levels.allowed(handler.user["level"], right):
        handler.send_json({"error": "Admins only." if method != "GET" else "Weld Engineers and Admins only."}, 403)
        return True
    current = registry.active() or config.default_model

    if path == "/api/admin/models" and method == "GET":
        models = listing(registry, config.default_model, specs())
        if models is None:
            handler.send_json({"error": "Ollama is not running, so the models cannot be listed.", "code": "offline"}, 503)
        else:
            handler.send_json({"models": shown(models), "active": current})
        return True

    m = ROUTE.match(path)
    if not (m and method == "POST"):
        handler.send_bytes(404, b"not found", "text/plain")
        return True
    model_id, action = m.groups()
    models = listing(registry, config.default_model, specs())
    if models is None:
        handler.send_json({"error": "Ollama is not running.", "code": "offline"}, 503)
        return True
    chosen = next((x for x in models if x["id"] == model_id), None)
    if chosen is None:
        handler.send_json({"error": "That model is not installed in Ollama.", "code": "not_installed"}, 404)
        return True
    body = body or {}
    try:
        if action == "delete":
            entry = registry.entry(model_id)
            if model_id == current:
                raise ModelError("is_active", "This is the model in use. Switch to another one first.")
            if not entry["dataset"] or remove is None:
                raise ModelError("not_deletable", "Only models made by a retrain can be deleted here.")
            if not remove(model_id):
                raise ModelError("offline", "Ollama could not delete the model.")
            registry.forget(model_id)
        elif action == "rename":
            taken = [x["label"] for x in models if x["id"] != model_id]
            registry.rename(model_id, str(body.get("display_name", "")), str(body.get("notes", "")), handler.user["username"], taken=taken)
        else:
            registry.set_active(model_id)
            config.default_model = model_id
            on_activate(model_id)
    except ModelError as e:
        handler.send_json({"error": e.message, "code": e.code}, STATUS.get(e.code, 400))
        return True
    handler.send_json({"models": shown(listing(registry, config.default_model, specs())), "active": registry.active() or config.default_model})
    return True
