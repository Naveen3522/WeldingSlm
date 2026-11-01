"""The template and draft routes with a User, a Weld Engineer and an Admin: each action needs its own permission, and a Word upload is checked."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import form_routes  # noqa: E402
from docx import Document  # noqa: E402
from welding_ai.accounts import permissions  # noqa: E402
from welding_ai.forms.store import FormStore  # noqa: E402

HEADS = ["Field", "Value", "Remarks"]
ROWS = [["Process", "", ""], ["Material", "", ""], ["Standard", "ISO 15614", ""]]
USER = {"level": 1, "username": "mina", "email": "mina@example.com"}
ENGINEER = {"level": 5, "username": "ravi", "email": "ravi@example.com"}
ADMIN = {"level": 10, "username": "asha", "email": "asha@example.com"}


class Handler:
    def __init__(self, user, content_type="application/json", raw=b""):
        self.user, self.headers, self.raw_body = user, {"Content-Type": content_type}, raw
        self.status, self.body, self.sent = None, None, None

    def send_json(self, obj, status=200):
        self.status, self.body = status, obj

    def send_bytes(self, status, data, ctype, headers=None):
        self.status, self.sent = status, (data, ctype, headers or {})


def multipart(name, data):
    boundary = "xBOUNDARYx"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", body


def docx_bytes(heads, rows):
    d = Document()
    t = d.add_table(rows=1, cols=len(heads))
    for i, h in enumerate(heads):
        t.rows[0].cells[i].text = h
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = v
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


class Routes(unittest.TestCase):
    def setUp(self):
        self._saved = permissions._current
        permissions._current = permissions.Permissions(Path(tempfile.mkdtemp()) / "permissions.xml")
        self.forms = FormStore(":memory:")

    def tearDown(self):
        permissions._current = self._saved

    def call(self, user, method, path, body=None, query="", content_type="application/json", raw=b""):
        h = Handler(user, content_type, raw)
        form_routes.handle(h, self.forms, method, path, parse_qs(query), body)
        return h

    def make_template(self):
        h = self.call(ENGINEER, "POST", "/api/templates/save", {"name": "Company WPS form", "heads": HEADS, "rows": ROWS})
        self.assertEqual(h.status, 200, h.body)
        return h.body["template"]

    def test_a_user_picks_a_template_but_cannot_change_templates(self):
        t = self.make_template()
        self.assertEqual(self.call(USER, "GET", "/api/templates").body["templates"][0]["name"], "Company WPS form")
        self.assertEqual(len(self.call(USER, "GET", f"/api/templates/{t['id']}").body["template"]["rows"]), 3)
        for method, path, body in (("POST", "/api/templates/save", {"name": "x", "heads": HEADS, "rows": ROWS}), ("POST", "/api/templates/save", {"id": t["id"], "name": "x", "heads": HEADS, "rows": ROWS}),
                                   ("POST", f"/api/templates/{t['id']}/delete", {}), ("GET", f"/api/templates/{t['id']}/download.docx", None)):
            self.assertEqual(self.call(USER, method, path, body).status, 403, path)

    def test_a_weld_engineer_manages_templates_and_downloads_one_as_word(self):
        t = self.make_template()
        changed = self.call(ENGINEER, "POST", "/api/templates/save", {"id": t["id"], "name": "Company WPS form", "heads": HEADS, "rows": ROWS + [["Position", "", ""]]})
        self.assertEqual(len(changed.body["template"]["rows"]), 4)
        dl = self.call(ENGINEER, "GET", f"/api/templates/{t['id']}/download.docx")
        data, ctype, headers = dl.sent
        self.assertEqual((dl.status, data[:2]), (200, b"PK"))
        self.assertIn("wordprocessingml", ctype)
        self.assertIn("Company%20WPS%20form.docx", headers["Content-Disposition"])
        self.assertEqual(self.call(ENGINEER, "POST", f"/api/templates/{t['id']}/delete", {}).status, 200)

    def test_an_uploaded_word_table_is_read_and_checked_and_nothing_is_saved(self):
        ctype, raw = multipart("form.docx", docx_bytes(HEADS, ROWS + [["", "stray", ""]]))
        ok = self.call(ENGINEER, "POST", "/api/templates/upload", content_type=ctype, raw=raw)
        self.assertEqual(ok.status, 200, ok.body)
        self.assertEqual((ok.body["heads"], len(ok.body["rows"])), (HEADS, 3))
        self.assertIn("will be skipped", ok.body["report"]["warnings"][0])
        self.assertEqual(self.forms.list_templates(), [])
        ctype, raw = multipart("notes.docx", b"not a word file")
        bad = self.call(ENGINEER, "POST", "/api/templates/upload", content_type=ctype, raw=raw)
        self.assertEqual((bad.status, bad.body["code"]), (400, "bad_file"))
        self.assertIn("notes.docx is not a valid Word file", bad.body["error"])
        self.assertEqual(self.call(USER, "POST", "/api/templates/upload", content_type=ctype, raw=raw).status, 403)

    def test_everyone_makes_and_saves_a_draft_and_sees_all_of_them(self):
        t = self.make_template()
        rows = [["Process", "spot", ""], ["Material", "mild steel", "ok"], ["Standard", "ISO 15614", ""]]
        made = self.call(USER, "POST", "/api/drafts/save", {"name": "Bracket", "template_id": t["id"], "heads": HEADS, "rows": rows})
        self.assertEqual((made.status, made.body["draft"]["filled"], made.body["draft"]["by"]), (200, 3, "mina"))
        did = made.body["draft"]["id"]
        rows[0][1] = "projection"
        saved = self.call(USER, "POST", "/api/drafts/save", {"id": did, "name": "Bracket", "heads": HEADS, "rows": rows})
        self.assertEqual(saved.body["draft"]["rows"][0][1], "projection")
        listing = self.call(ADMIN, "GET", "/api/drafts", query="offset=0&limit=25")
        self.assertEqual((listing.body["total"], listing.body["drafts"][0]["email"]), (1, "mina@example.com"))
        self.assertEqual(self.call(ENGINEER, "GET", f"/api/drafts/{did}").body["draft"]["name"], "Bracket")
        dl = self.call(USER, "GET", f"/api/drafts/{did}/download.docx")
        self.assertEqual((dl.status, dl.sent[0][:2]), (200, b"PK"))
        xl = self.call(USER, "GET", f"/api/drafts/{did}/download.xlsx")
        self.assertEqual((xl.status, xl.sent[0][:2], "spreadsheetml" in xl.sent[1], xl.sent[2]["Content-Disposition"].endswith(".xlsx")), (200, b"PK", True, True))

    def test_only_the_maker_or_an_administrator_changes_a_draft(self):
        t = self.make_template()
        rows = [list(r) for r in ROWS]
        did = self.call(USER, "POST", "/api/drafts/save", {"name": "Bracket", "template_id": t["id"], "heads": HEADS, "rows": rows}).body["draft"]["id"]
        rows[0][1] = "spot"
        other = {"level": 1, "username": "joe", "email": "joe@example.com"}
        self.assertEqual(self.call(other, "POST", "/api/drafts/save", {"id": did, "name": "Bracket", "heads": HEADS, "rows": rows}).status, 403)
        self.assertFalse(self.call(other, "GET", f"/api/drafts/{did}").body["draft"]["can_edit"])
        self.assertTrue(self.call(USER, "GET", f"/api/drafts/{did}").body["draft"]["can_edit"])
        self.assertEqual(self.call(ADMIN, "POST", "/api/drafts/save", {"id": did, "name": "Bracket", "heads": HEADS, "rows": rows}).status, 200)

    def test_the_fixed_cells_and_the_shape_are_protected_unless_the_person_may_restructure(self):
        t = self.make_template()
        fixed = [["Process", "", ""], ["Material", "", ""], ["Standard", "ISO 9999", ""]]
        self.assertEqual(self.call(USER, "POST", "/api/drafts/save", {"name": "x", "template_id": t["id"], "heads": HEADS, "rows": fixed}).body["code"], "fixed_cell")
        longer = [list(r) for r in ROWS] + [["Position", "flat", ""]]
        self.assertEqual(self.call(USER, "POST", "/api/drafts/save", {"name": "x", "template_id": t["id"], "heads": HEADS, "rows": longer}).status, 403)
        self.assertEqual(self.call(ENGINEER, "POST", "/api/drafts/save", {"name": "x", "template_id": t["id"], "heads": HEADS, "rows": longer}).status, 200)

    def test_only_those_allowed_delete_a_draft_and_a_bad_request_is_refused_cleanly(self):
        t = self.make_template()
        made = self.call(USER, "POST", "/api/drafts/save", {"name": "d", "template_id": t["id"], "heads": HEADS, "rows": ROWS}).body["draft"]
        self.assertEqual(self.call(USER, "POST", f"/api/drafts/{made['id']}/delete", {}).status, 403)
        self.assertEqual(self.call(ENGINEER, "POST", f"/api/drafts/{made['id']}/delete", {}).status, 403)       # Delete a draft starts at Admin
        self.assertEqual(self.call(ADMIN, "POST", f"/api/drafts/{made['id']}/delete", {}).status, 200)
        self.assertEqual(self.call(ADMIN, "GET", f"/api/drafts/{made['id']}").status, 404)
        self.assertEqual(self.call(USER, "POST", "/api/drafts/save", {"name": "", "template_id": t["id"], "heads": HEADS, "rows": ROWS}).body["code"], "bad_name")
        self.assertEqual(self.call(USER, "POST", "/api/drafts/save", {"name": "d", "template_id": "abc", "heads": HEADS, "rows": ROWS}).status, 400)
        self.assertEqual(self.call(USER, "GET", "/api/drafts", query="offset=x").status, 400)


if __name__ == "__main__":
    unittest.main()
