"""This computer's specs for the Model management page (Weld Engineers and Admins). Optional: models work without it; it only adds a "Fit" to each model and a recommendation.

    GET  /api/admin/system          what the last check found and when ({"specs": null} before the first one), and the advice drawn from it
    POST /api/admin/system/scan     check this computer now (graphics memory, memory, processor, free disk) and remember the result
"""
import json
import time

from welding_ai.accounts import levels
from welding_ai.system import specs as sp

KEY, WHEN = "system_specs", "system_checked"


def saved(queue):
    """-> (specs, when) from the last check, or (None, 0)."""
    raw = queue.setting(KEY, "")
    try:
        return (json.loads(raw), float(queue.setting(WHEN, "0"))) if raw else (None, 0)
    except ValueError:
        return None, 0


def view(queue):
    found, when = saved(queue)
    return {"specs": found, "checked": when, "advice": sp.advice(found) if found else ""}


def handle(handler, queue, folder, method, path, body, scan=sp.scan, clock=time.time):
    if path not in ("/api/admin/system", "/api/admin/system/scan"):
        return False
    if not levels.allowed(handler.user["level"], "view_models"):
        handler.send_json({"error": "Weld Engineers and Admins only."}, 403)
    elif path == "/api/admin/system" and method == "GET":
        handler.send_json(view(queue))
    elif path == "/api/admin/system/scan" and method == "POST":
        queue.set_setting(KEY, json.dumps(scan(folder)))
        queue.set_setting(WHEN, clock())
        handler.send_json(view(queue))
    else:
        handler.send_bytes(404, b"not found", "text/plain")
    return True
