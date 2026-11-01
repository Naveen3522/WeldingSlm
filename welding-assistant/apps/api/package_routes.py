"""Model files for the Model management page (Admins): take a model, or the data to train one, to another computer, and add one that was made elsewhere.

    POST /api/admin/models/<id>/export       {kind: "model" | "kit", pairs, gate, hand}  -> the .weldmodel file. A model needs its trained adapter on this computer; a kit is the
                                             question pairs and check scores only. `hand` adds the pairs written by hand (otherwise they are left out).
    POST /api/admin/models/package/check     the file itself as the body (application/octet-stream) -> what is in it and whether this computer can use it; always run first
    POST /api/admin/models/package/add       {token}  a model: unpack it and add it to Ollama in the background (never made active); a kit: its pairs join the question pairs
    GET  /api/admin/models/package/status    how the background add is going
"""
import json
import os
import re
import secrets
import time

from welding_ai import paths
from welding_ai.accounts import levels
from welding_ai.llm import package
from welding_ai.system import specs as sp

EXPORT = re.compile(r"^/api/admin/models/([A-Za-z0-9._:-]{1,80})/export$")
MAX_FILE = 400 * 1024 ** 2
KEEP_SECONDS = 24 * 3600


def slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:40] or "model"


class Imports:
    """Files received and checked, kept for a day so that "Add" does not need them sent again."""

    def __init__(self, folder, clock=time.time):
        self.folder, self.clock = folder, clock

    def keep(self, data):
        self.folder.mkdir(parents=True, exist_ok=True)
        for f in self.folder.glob("*.weldmodel"):
            if self.clock() - f.stat().st_mtime > KEEP_SECONDS:
                f.unlink(missing_ok=True)
        token = secrets.token_urlsafe(16)
        (self.folder / f"{token}.weldmodel").write_bytes(data)
        return token

    def get(self, token):
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,32}", token or ""):
            return None
        f = self.folder / f"{token}.weldmodel"
        return f.read_bytes() if f.is_file() else None


def check(pkg, specs, library):
    """What is in a file and whether this computer can use it. specs: this computer's, as scanned now. library: the document names this computer has."""
    m = pkg.manifest
    out = {"name": m["name"], "kind": m["kind"], "base": m["base"], "made": m.get("made"), "made_on": m.get("made_on"), "pairs": m.get("pairs", 0), "gate": m.get("gate"), "lines": []}
    lines = out["lines"]
    if m["kind"] == "model":
        same = m["base"]["id"] == package.BASE_ID
        lines.append({"state": "ok" if same else "bad", "text": f"Built on {m['base'].get('name', m['base']['id'])}" + (", the same base this computer uses." if same
                      else f". This computer's training and export are set up for {package.BASE_NAME}, so this adapter cannot be used here. Export a training kit from the other computer instead.")})
        fit = sp.assess(specs, float(m["base"].get("params_b") or package.BASE_PARAMS_B))
        lines += fit["lines"]
        out["fit"] = fit
        out["can_add"] = same and fit["usable"]
    else:
        rows = pkg.pairs()
        docs = sorted({r.get("doc", "") for r in rows if r.get("doc") and r.get("doc") != "Added by hand"})
        missing = [d for d in docs if d not in library]
        lines.append({"state": "ok" if rows else "bad", "text": f"{len(rows)} question pair{'s' if len(rows) != 1 else ''} from {len(docs)} document{'s' if len(docs) != 1 else ''}. They are added to the question pairs; retrain to make a model of your own."})
        if missing:
            lines.append({"state": "warn", "text": f"{len(missing)} of those documents are not in this library ({', '.join(d.rsplit('.', 1)[0] for d in missing[:3])}{'...' if len(missing) > 3 else ''}). Their pairs are added but are checked against the documents you have."})
        out["fit"], out["can_add"] = None, bool(rows)
    return out


def handle(handler, registry, queue, installer, imports, library, retraining, method, path, body, scan=sp.scan, folder=None):
    """library() -> the document names; retraining() -> whether a retrain is running."""
    m = EXPORT.match(path)
    if not (m or path.startswith("/api/admin/models/package/")):
        return False
    if not levels.allowed(handler.user["level"], "models"):
        handler.send_json({"error": "Admins only."}, 403)
        return True
    body = body or {}

    if m and method == "POST":
        model_id, kind = m.group(1), str(body.get("kind", "model"))
        entry = registry.entry(model_id)
        label = entry["display_name"] or model_id
        pairs = [p for p in queue.approved() if p["kind"] not in ("manual", "suggested") or body.get("hand")] if (body.get("pairs") or kind == "kit") else None
        gate = json.loads(entry["gate"]) if entry["gate"] and body.get("gate", True) else None
        specs = scan(folder or paths.RETRAIN_DIR) if body.get("specs", True) else None
        try:
            if kind == "kit":
                data = package.make_kit(label, pairs or [], gate=gate, specs=specs)
            elif kind == "model":
                if not entry["dataset"]:
                    raise package.PackageError("This model was not made by a retrain on this computer, so there is no adapter to export. Export a training kit instead.")
                data = package.make_model(paths.MODEL_DIR / "runs" / entry["dataset"] / "adapter", label, notes=entry["notes"], gate=gate, specs=specs, pairs=pairs)
            else:
                raise package.PackageError("Choose a model or a training kit.")
        except package.PackageError as e:
            handler.send_json({"error": e.message}, 400)
            return True
        handler.send_bytes(200, data, "application/octet-stream", {"Content-Disposition": f'attachment; filename="{slug(label)}-{time.strftime("%Y%m%d")}.weldmodel"'})
        return True

    if path == "/api/admin/models/package/status" and method == "GET":
        handler.send_json(installer.status())
        return True

    if path == "/api/admin/models/package/check" and method == "POST":
        data = getattr(handler, "raw_body", b"")
        try:
            pkg = package.read(data)
        except package.PackageError as e:
            handler.send_json({"error": e.message}, 400)
            return True
        result = check(pkg, scan(folder or paths.RETRAIN_DIR), set(library()))
        result["token"] = imports.keep(data)
        handler.send_json(result)
        return True

    if path == "/api/admin/models/package/add" and method == "POST":
        data = imports.get(str(body.get("token", "")))
        if data is None:
            handler.send_json({"error": "That file is no longer here. Choose it again."}, 404)
            return True
        pkg = package.read(data)
        verdict = check(pkg, scan(folder or paths.RETRAIN_DIR), set(library()))               # always checked again here: the page cannot skip it
        if not verdict["can_add"]:
            handler.send_json({"error": "This computer cannot use this file: " + next((l["text"] for l in verdict["lines"] if l["state"] == "bad"), "see the check.")}, 409)
        elif pkg.kind == "kit":
            made = queue.add_many([{**{k: r.get(k, "") for k in ("doc", "section", "question", "answer", "evidence")}, "kind": r.get("kind") or "table"} for r in pkg.pairs() if r.get("kind") != "manual" or body.get("hand")],
                                  user=handler.user["username"])
            handler.send_json({"kind": "kit", "added": made["added"], "skipped": made["skipped"]})
        elif retraining():
            handler.send_json({"error": "A retrain is running. Add the model when it has finished."}, 409)
        else:
            ok, result = installer.start(pkg, handler.user["username"])
            handler.send_json(result if ok else {"error": result}, 202 if ok else 409)
        return True
    handler.send_bytes(404, b"not found", "text/plain")
    return True
