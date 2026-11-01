"""Generated files for the server (after auth_routes.handle, so the person is signed in).

    GET  /api/agents/wps/<token>   the WPS draft (.docx) the chat offered; the token is secret and the file is forgotten after half an hour
    POST /api/export/docx          {question, label, blocks, sources}: any answer the page shows, as a Word document (nothing is stored)
"""
import re

from welding_ai.agents import export

ROUTE = re.compile(r"^/api/agents/wps/([A-Za-z0-9_-]{20,64})$")
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def handle(handler, drafts, method, path, body=None):
    if path == "/api/export/docx" and method == "POST":
        body = body if isinstance(body, dict) else {}
        try:
            data = export.answer_docx(body.get("question"), body.get("label"), body.get("blocks"), body.get("sources"), handler.user["username"])
        except Exception:
            handler.send_json({"error": "The Word document could not be made."}, 400)
            return True
        handler.send_bytes(200, data, DOCX, {"Content-Disposition": 'attachment; filename="weld-assist-answer.docx"'})
        return True
    m = ROUTE.match(path)
    if not m or method != "GET":
        return False
    item = drafts.get(m.group(1))
    if item is None:
        handler.send_json({"error": "This download has expired. Ask for the draft again."}, 404)
        return True
    name, data = item
    handler.send_bytes(200, data, DOCX, {"Content-Disposition": f'attachment; filename="{name}"'})
    return True
