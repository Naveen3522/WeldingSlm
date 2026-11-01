"""The gateway: one route table, versioned addresses, rights, query and page parameters, and the Activity routes moved onto it."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

import gateway  # noqa: E402
import activity_routes  # noqa: E402,F401  (registers its routes)
import static_files  # noqa: E402
from welding_ai.activity.service import ActivityLog  # noqa: E402

WEB = ROOT / "apps" / "web"


class Handler:
    def __init__(self, level=10, signed_in=True):
        self.user = {"username": "ann", "email": "ann@example.com", "level": level} if signed_in else None
        self.sent = None

    def send_json(self, obj, code=200):
        self.sent = (code, obj)

    def send_bytes(self, code, data, ctype, extra=None):
        self.sent = (code, data, ctype, extra)


class Addresses(unittest.TestCase):
    def test_v1_and_the_plain_address_are_one_route(self):
        self.assertEqual(gateway.versioned("/api/v1/audit"), "/api/audit")
        self.assertEqual(gateway.versioned("/api/audit"), "/api/audit")
        self.assertEqual(gateway.versioned("/library"), "/library")

    def test_path_parameters_are_matched(self):
        @gateway.route("GET", "/api/_test/{id}/items/{kind}")
        def probe(req):
            return req.params
        found = gateway.find("GET", "/api/_test/7/items/a")
        self.assertEqual(found[1], {"id": "7", "kind": "a"})
        self.assertIsNone(gateway.find("POST", "/api/_test/7/items/a"))
        self.assertIsNone(gateway.find("GET", "/api/_test/7/items"))
        gateway.ROUTES.remove(found[0])

    def test_pages_have_clean_addresses_and_the_old_ones_still_work(self):
        for clean, name in (("/library", "library.html"), ("/admin/model", "admin/model.html"), ("/library.html", "library.html"), ("/", "index.html")):
            self.assertEqual(static_files.static_file(WEB, clean), WEB / "pages" / name)
        for bad in ("/pages/library", "/../library", "/library/x", "/admin/x/y"):
            self.assertIsNone(static_files.static_file(WEB, bad), bad)


class Requests(unittest.TestCase):
    def setUp(self):
        gateway.app.activity = ActivityLog(":memory:")

    def call(self, method, path, query="", body=None, level=10, signed_in=True):
        h = Handler(level, signed_in)
        return gateway.dispatch(h, method, gateway.versioned(path), query, body), h.sent

    def test_an_unregistered_route_is_left_to_the_older_handlers(self):
        handled, sent = self.call("GET", "/api/library")
        self.assertFalse(handled)
        self.assertIsNone(sent)

    def test_nobody_signed_in_is_refused(self):
        handled, sent = self.call("GET", "/api/v1/audit", signed_in=False)
        self.assertEqual((handled, sent[0]), (True, 401))

    def test_a_route_that_needs_a_right_refuses_a_person_without_it(self):
        handled, sent = self.call("GET", "/api/v1/audit", level=1)
        self.assertEqual((handled, sent[0], sent[1]["code"]), (True, 403, "forbidden"))

    def test_the_audit_list_is_paged_and_the_limit_is_capped(self):
        for n in range(5):
            gateway.app.activity.record("ann", f"question {n}", "answer", email="ann@example.com")
        _, sent = self.call("GET", "/api/v1/audit", "limit=2&offset=1")
        self.assertEqual((sent[0], len(sent[1]["rows"]), sent[1]["total"]), (200, 2, 5))
        _, sent = self.call("GET", "/api/v1/audit", "q=question+3")
        self.assertEqual([r["question"] for r in sent[1]["rows"]], ["question 3"])
        _, sent = self.call("GET", "/api/audit", "limit=9999")                    # the plain address reaches the same route
        self.assertEqual(sent[0], 200)

    def test_a_bad_number_is_a_400_with_a_reason(self):
        _, sent = self.call("GET", "/api/v1/audit", "limit=lots")
        self.assertEqual((sent[0], sent[1]["code"]), (400, "bad_number"))

    def test_feedback_is_checked(self):
        _, sent = self.call("POST", "/api/v1/feedback", body={"score": "x"})
        self.assertEqual(sent[0], 400)
        _, sent = self.call("POST", "/api/v1/feedback", body={"log_id": 999, "score": 1})
        self.assertEqual(sent[0], 404)

    def test_the_excel_export_is_a_file_named_for_the_activity(self):
        _, sent = self.call("GET", "/api/v1/audit/export.xlsx")
        self.assertEqual(sent[0], 200)
        self.assertIn("weld-assist-activity.xlsx", sent[3]["Content-Disposition"])
        self.assertTrue(sent[1][:2] == b"PK")

    def test_the_route_list_documents_every_route(self):
        _, sent = self.call("GET", "/api/v1/routes", level=1)
        listed = {(r["method"], r["path"]) for r in sent[1]["routes"]}
        self.assertIn(("GET", "/api/v1/audit"), listed)
        self.assertIn(("GET", "/api/v1/routes"), listed)
        self.assertTrue(all(r["doc"] for r in sent[1]["routes"] if not r["path"].startswith("/api/v1/_test")))
        json.dumps(sent[1])


if __name__ == "__main__":
    unittest.main()


class Clearing(unittest.TestCase):
    """An Admin can delete audit entries: by age, by what the filters match, or everything on purpose; a count comes first and nothing is deleted without a clear choice."""

    def setUp(self):
        from welding_ai.accounts import permissions
        permissions._current = permissions.Permissions()
        self.now = [1_000_000.0]
        gateway.app.activity = ActivityLog(":memory:", clock=lambda: self.now[0])
        log = gateway.app.activity
        self.old = log.record("ann", "old question about porosity", "a", email="ann@example.com")
        self.now[0] += 40 * 86400
        log.record("bob", "new question about spatter", "b", email="bob@example.com")
        log.add_feedback("bob", 2, 1, "good", "bob@example.com")

    def tearDown(self):
        from welding_ai.accounts import permissions
        permissions._current = None

    def call(self, body, level=10):
        h = Handler(level)
        gateway.dispatch(h, "POST", "/api/audit/clear", "", body)
        return h.sent

    def total(self):
        return gateway.app.activity.audit_page()["total"]

    def test_a_count_first_changes_nothing(self):
        code, data = self.call({"days": 30, "dry_run": True})
        self.assertEqual((code, data["would_delete"], data["deleted"]), (200, 1, 0))
        self.assertEqual(self.total(), 2)

    def test_older_than_days_deletes_only_the_old_ones(self):
        code, data = self.call({"days": 30})
        self.assertEqual((code, data["deleted"], self.total()), (200, 1, 1))
        self.assertEqual(gateway.app.activity.audit_page()["rows"][0]["username"], "bob")

    def test_the_filters_name_what_goes_and_the_feedback_goes_with_it(self):
        self.assertEqual(self.call({"q": "spatter"})[1]["deleted"], 1)
        self.assertEqual(gateway.app.activity.usage()["helpful"], 0)
        self.assertEqual(self.total(), 1)

    def test_nothing_is_deleted_without_a_clear_choice(self):
        code, data = self.call({})
        self.assertEqual((code, self.total()), (400, 2))
        self.assertEqual(self.call({"everything": True})[1]["deleted"], 2)
        self.assertEqual(self.total(), 0)

    def test_only_an_admin_may_clear(self):
        code, data = self.call({"everything": True}, level=5)
        self.assertEqual((code, self.total()), (403, 2))
