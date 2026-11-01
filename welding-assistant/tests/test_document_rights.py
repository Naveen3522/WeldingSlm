"""Each document route has its own right: saving (library), deleting (doc_delete), reindexing and cancelling it (reindex). The state is open to anyone holding one of them."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import document_routes  # noqa: E402

ROUTES = [("GET", "/api/admin/documents"), ("POST", "/api/admin/documents/upload"), ("POST", "/api/admin/documents/delete"),
          ("POST", "/api/admin/documents/refresh"), ("POST", "/api/admin/documents/refresh/cancel")]


class Manifest:
    def detect(self):
        return {"new": ["a.docx"], "changed": [], "removed": [], "documents": [], "pending": True, "last": None}


class Job:
    def status(self):
        return {"state": "idle"}

    def start(self):
        return True

    def cancel(self):
        pass


class Handler:
    def __init__(self):
        self.user, self.sent, self.headers, self.raw_body = {"level": 1}, None, {"Content-Type": ""}, b""

    def send_json(self, obj, code=200):
        self.sent = (code, obj)

    def send_bytes(self, code, data, kind, headers=None):
        self.sent = (code, None)


def call(held, method, path, body=None):
    """Runs a route as someone who holds exactly the rights named in `held`."""
    h = Handler()
    with mock.patch.object(document_routes.levels, "allowed", side_effect=lambda level, right: right in held):
        document_routes.handle(h, Path("."), Path("."), Manifest(), Job(), method, path, body, lambda: False)
    return h.sent[0]


class Rights(unittest.TestCase):
    def test_someone_with_no_right_is_refused_everywhere(self):
        self.assertEqual([call(set(), m, p, {"names": ["a.docx"]}) for m, p in ROUTES], [403] * 5)

    def test_reindex_alone_may_read_the_state_and_reindex_but_not_save_or_delete(self):
        got = {p: call({"reindex"}, m, p, {"names": ["a.docx"]}) for m, p in ROUTES}
        self.assertEqual(got["/api/admin/documents"], 200)
        self.assertEqual(got["/api/admin/documents/refresh"], 202)
        self.assertEqual(got["/api/admin/documents/refresh/cancel"], 200)
        self.assertEqual((got["/api/admin/documents/upload"], got["/api/admin/documents/delete"]), (403, 403))

    def test_delete_alone_may_delete_but_not_save_or_reindex(self):
        got = {p: call({"doc_delete"}, m, p, {"names": []}) for m, p in ROUTES}
        self.assertEqual(got["/api/admin/documents/delete"], 400)             # allowed in: the empty list is what it refuses
        self.assertEqual((got["/api/admin/documents/upload"], got["/api/admin/documents/refresh"], got["/api/admin/documents/refresh/cancel"]), (403, 403, 403))
        self.assertEqual(got["/api/admin/documents"], 200)

    def test_library_alone_may_save_and_read_the_state_but_not_delete_or_reindex(self):
        got = {p: call({"library"}, m, p, {"names": ["a.docx"]}) for m, p in ROUTES}
        self.assertEqual(got["/api/admin/documents"], 200)
        self.assertEqual((got["/api/admin/documents/delete"], got["/api/admin/documents/refresh"], got["/api/admin/documents/refresh/cancel"]), (403, 403, 403))
        self.assertNotEqual(got["/api/admin/documents/upload"], 403)

    def test_an_unknown_path_is_not_found(self):
        self.assertEqual(call({"library", "reindex", "doc_delete"}, "POST", "/api/admin/documents/nothing"), 404)


if __name__ == "__main__":
    unittest.main()
