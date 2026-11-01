"""Templates and drafts for the server (after auth_routes.handle, so the person is signed in). Every action is checked against its own permission.

    GET  /api/templates                      the templates (anyone who can make a draft, or manage templates)
    GET  /api/templates/<id>                 one template with its rows
    POST /api/templates/save                 {id?, name, heads, rows}  a new template (5.5) or a changed one (5.2)
    POST /api/templates/<id>/delete          remove a template (5.6); drafts made from it keep their own copy
    POST /api/templates/upload               multipart .docx: the table is read and checked, nothing is saved (5.4)  -> {heads, rows, report}
    GET  /api/templates/<id>/download.docx   the template as a Word file (5.3)
    GET  /api/drafts[?offset&limit&q]        every draft, newest first (everyone sees all)
    GET  /api/drafts/<id>                    one draft with its rows
    POST /api/drafts/save                    {id?, name, template_id?, heads, rows}  a draft exists from its first save (4.1 to make, 4.3 to save, 4.6 to change the shape)
    POST /api/drafts/<id>/delete             remove a draft (4.5)
    GET  /api/drafts/<id>/download.docx      the draft as a Word file, unfilled cells left empty (4.4)
    GET  /api/drafts/<id>/download.xlsx      the draft as an Excel file: the column titles, then every row as entered (4.4)
"""
import re
from urllib.parse import quote

from welding_ai.accounts import levels
from welding_ai.forms.docx_table import DocxProblem, read_table, read_xlsx_table, write_docx
from welding_ai.forms.store import PAGE, FormError
from welding_ai.ingestion import uploads

MAX_UPLOAD = 6 * 1024 * 1024
ROUTE = re.compile(r"^/api/(templates|drafts)/(\d+)(?:/(delete|download\.docx|download\.xlsx))?$")
STATUS = {"not_found": 404, "exists": 409, "no_restructure": 403, "no_rename": 403, "fixed_cell": 403}
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _one(query, key, default=""):
    return ((query or {}).get(key) or [default])[0].strip()


def _file_name(name):
    keep = re.sub(r"[^A-Za-z0-9._ -]+", "", name).strip() or "table"
    return keep[:80] + ".docx"


def handle(handler, forms, method, path, query, body):
    if not (path.startswith("/api/templates") or path.startswith("/api/drafts")):
        return False
    user = handler.user
    allowed = lambda right: levels.allowed(user["level"], right)

    def refuse(text="You do not have access to this."):
        handler.send_json({"error": text}, 403)
        return True

    def fail(e):
        handler.send_json({"error": e.message, "code": e.code}, STATUS.get(e.code, 400))
        return True

    def download(title, heads, rows, note=""):
        handler.send_bytes(200, write_docx(title, heads, rows, note), DOCX, {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(_file_name(title))}"})
        return True

    body = body or {}

    def mine(draft):
        """Only the person who made a draft, or an administrator (who may delete drafts), changes it; everyone else reads it."""
        return draft["email"] == user["email"] or allowed("draft_delete")

    def shown(draft):
        return dict(draft, can_edit=mine(draft))

    try:
        # ---------------------------------------------------------------- templates
        if path == "/api/templates" and method == "GET":
            if not (allowed("draft_new") or allowed("template_view")):
                return refuse()
            handler.send_json({"templates": forms.list_templates()})
            return True
        if path == "/api/templates/upload" and method == "POST":
            if not allowed("template_upload"):
                return refuse("You cannot upload a table.")
            try:
                files = uploads.parse_multipart(handler.headers.get("Content-Type", ""), handler.raw_body)
                if not files:
                    raise DocxProblem("No file was sent.")
                reader = read_xlsx_table if str(files[0][0]).lower().endswith(".xlsx") else read_table
                handler.send_json(reader(files[0][1], files[0][0]))
            except (uploads.UploadError, DocxProblem) as e:
                handler.send_json({"error": e.message, "code": "bad_file"}, 400)
            return True
        if path == "/api/templates/save" and method == "POST":
            creating = body.get("id") in (None, "")
            if not allowed("template_new" if creating else "template_save"):
                return refuse("You cannot save templates.")
            saved = forms.save_template(None if creating else int(body["id"]), body.get("name"), body.get("heads"), body.get("rows"), user["username"], user["email"])
            handler.send_json({"template": saved})
            return True

        m = ROUTE.match(path)
        if m:
            kind, ident, action = m.group(1), int(m.group(2)), m.group(3)
            if kind == "templates":
                if method == "GET" and action is None:
                    if not (allowed("draft_new") or allowed("template_view")):
                        return refuse()
                    handler.send_json({"template": forms.get_template(ident)})
                    return True
                if method == "GET" and action == "download.docx":
                    if not allowed("template_download"):
                        return refuse("You cannot download templates.")
                    t = forms.get_template(ident)
                    return download(t["name"], t["heads"], t["rows"])
                if method == "POST" and action == "delete":
                    if not allowed("template_remove"):
                        return refuse("You cannot remove templates.")
                    forms.delete_template(ident)
                    handler.send_json({"ok": True})
                    return True
            else:
                if method == "GET" and action is None:
                    if not allowed("draft_view"):
                        return refuse()
                    handler.send_json({"draft": shown(forms.get_draft(ident))})
                    return True
                if method == "GET" and action == "download.docx":
                    if not allowed("draft_download"):
                        return refuse("You cannot download drafts.")
                    d = forms.get_draft(ident)
                    return download(d["name"], d["heads"], d["rows"], "Draft only. An engineer must confirm every value.")
                if method == "GET" and action == "download.xlsx":
                    if not allowed("draft_download"):
                        return refuse("You cannot download drafts.")
                    from welding_ai.core import xlsx
                    d = forms.get_draft(ident)
                    data = xlsx.write([d["heads"]] + d["rows"], widths=[28] + [34] * (len(d["heads"]) - 1))
                    handler.send_bytes(200, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                       {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(_file_name(d['name'])[:-5] + '.xlsx')}"})
                    return True
                if method == "POST" and action == "delete":
                    if not allowed("draft_delete"):
                        return refuse("You cannot delete drafts.")
                    forms.delete_draft(ident)
                    handler.send_json({"ok": True})
                    return True

        # ---------------------------------------------------------------- drafts
        if path == "/api/drafts" and method == "GET":
            if not allowed("draft_view"):
                return refuse()
            try:
                offset, limit = int(_one(query, "offset", "0")), int(_one(query, "limit", str(PAGE)))
            except ValueError:
                handler.send_json({"error": "offset and limit must be numbers."}, 400)
                return True
            listing = forms.list_drafts(offset, limit, _one(query, "q"))
            handler.send_json(dict(listing, drafts=[shown(d) for d in listing["drafts"]]))
            return True
        if path == "/api/drafts/save" and method == "POST":
            creating = body.get("id") in (None, "")
            if not allowed("draft_new" if creating else "draft_save"):
                return refuse("You cannot save drafts.")
            shape = allowed("draft_shape")
            if creating:
                saved = forms.create_draft(body.get("name"), int(body.get("template_id") or 0), body.get("heads"), body.get("rows"), user["username"], user["email"], shape)
            else:
                if not mine(forms.get_draft(int(body["id"]))):
                    return refuse("This draft was made by someone else. Only they or an administrator can change it.")
                saved = forms.save_draft(int(body["id"]), body.get("name"), body.get("heads"), body.get("rows"), shape)
            handler.send_json({"draft": shown(saved)})
            return True
    except FormError as e:
        return fail(e)
    except (TypeError, ValueError):
        handler.send_json({"error": "That request was not understood."}, 400)
        return True
    handler.send_bytes(404, b"not found", "text/plain")
    return True
