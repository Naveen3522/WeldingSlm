"""The account routes end to end (no network): sign-up, sign-in by email, the rules the pages show, roles, users, the Admin reset and the permissions tab."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import auth_routes  # noqa: E402
from welding_ai.accounts import permissions, rules  # noqa: E402
from welding_ai.accounts.recovery import Recovery  # noqa: E402
from welding_ai.accounts.service import AccountService  # noqa: E402

PW = "Welding#2026"


class Handler:
    """Just enough of the server's request handler: the headers in, the answer out."""

    def __init__(self, cookie=""):
        self.headers = {"Cookie": cookie} if cookie else {}
        self.user = None
        self.status, self.body, self.cookie = None, None, ""

    def send_json(self, obj, status=200):
        self.status, self.body = status, obj

    def send_bytes(self, status, data, ctype, headers=None):
        self.status = status
        try:
            self.body = json.loads(data)
        except ValueError:
            self.body = data
        self.cookie = (headers or {}).get("Set-Cookie", "")


class Mail:
    def send(self, *a, **k):
        pass

    def describe(self):
        return {}


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._saved = (permissions._current, rules._current)
        permissions._current = permissions.Permissions(self.dir / "permissions.xml")
        rules._current = rules.Rules(self.dir / "account-rules.json")
        self.accounts = AccountService(":memory:")
        self.recovery = Recovery(":memory:", self.accounts, Mail())
        self.accounts.create_user("asha", "asha@example.com", "admin", password=PW)

    def tearDown(self):
        permissions._current, rules._current = self._saved

    def call(self, method, path, body=None, cookie=""):
        h = Handler(cookie)
        auth_routes.handle(h, self.accounts, method, path, body, self.recovery)
        return h

    def sign_in(self, email, password=PW):
        h = self.call("POST", "/api/auth/login", {"email": email, "password": password})
        self.assertEqual(h.status, 200, h.body)
        return h.cookie.split(";")[0]

    def make_user(self, name, email, level=1):
        self.accounts.create_user(name, email, level=level, password=PW)
        return self.sign_in(email)

    # ------------------------------------------------------------------ signing up and in
    def test_the_rules_are_public_and_carry_the_messages(self):
        h = self.call("GET", "/api/auth/rules")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.body["rules"]["password"]["message"], rules.DEFAULTS["password"]["message"])
        self.assertIn("pattern", h.body["rules"]["email"])

    def test_sign_up_makes_a_user_signs_them_in_and_gives_a_recovery_key_once(self):
        h = self.call("POST", "/api/auth/signup", {"username": "mina", "email": "mina@example.com", "password": "Mina password 4"})
        self.assertEqual(h.status, 201, h.body)
        self.assertEqual((h.body["user"]["role_name"], h.body["user"]["level"]), ("User", 1))
        self.assertRegex(h.body["recovery_key"], r"^[A-Z2-9]{4}(-[A-Z2-9]{4}){3}$")
        me = self.call("GET", "/api/auth/me", cookie=h.cookie.split(";")[0])
        self.assertEqual(me.status, 200)
        self.assertEqual(me.body["user"]["email"], "mina@example.com")
        self.assertTrue(me.body["permissions"]["controls"]["4.1"]["write"])             # a User may make a draft
        self.assertFalse(me.body["permissions"]["controls"]["4.5"]["read"])             # and never sees Delete a draft

    def test_sign_up_refuses_a_weak_password_a_bad_email_and_a_used_email_with_the_rule_messages(self):
        base = {"username": "mina", "email": "mina@example.com", "password": "Mina password 4"}
        h = self.call("POST", "/api/auth/signup", {**base, "password": "weak"})
        self.assertEqual((h.status, h.body["error"]), (400, rules.DEFAULTS["password"]["message"]))
        h = self.call("POST", "/api/auth/signup", {**base, "email": "nope"})
        self.assertEqual((h.status, h.body["error"]), (400, rules.DEFAULTS["email"]["message"]))
        h = self.call("POST", "/api/auth/signup", {**base, "email": ""})
        self.assertEqual(h.body["error"], "Enter your email.")
        self.assertEqual(self.call("POST", "/api/auth/signup", base).status, 201)
        self.assertEqual(self.call("POST", "/api/auth/signup", {**base, "username": "other"}).status, 409)

    def test_sign_in_is_by_email_only_and_the_message_does_not_say_which_part_was_wrong(self):
        self.assertTrue(self.sign_in("ASHA@example.com"))
        for who in ("asha", "nobody@example.com"):
            h = self.call("POST", "/api/auth/login", {"email": who, "password": PW})
            self.assertEqual((h.status, h.body["error"]), (401, "Wrong email or password."))
        h = self.call("POST", "/api/auth/login", {"email": "asha@example.com", "password": "Wrong password 1"})
        self.assertEqual(h.body["error"], "Wrong email or password.")

    # ------------------------------------------------------------------ a User may not administer
    def test_a_user_is_refused_everything_an_admin_does(self):
        cookie = self.make_user("mina", "mina@example.com")
        for method, path, body in (("GET", "/api/users", None), ("GET", "/api/roles", None), ("POST", "/api/roles/save", {"roles": []}),
                                   ("GET", "/api/permissions", None), ("POST", "/api/permissions/save", {"screens": []}), ("POST", "/api/users/1/reset", {}),
                                   ("POST", "/api/users/1/delete", {})):
            self.assertEqual(self.call(method, path, body, cookie).status, 403, path)

    def test_a_weld_engineer_is_refused_the_admin_pages_too(self):
        cookie = self.make_user("eddy", "ed@example.com", level=5)
        for path in ("/api/users", "/api/roles", "/api/permissions"):
            self.assertEqual(self.call("GET", path, None, cookie).status, 403, path)

    # ------------------------------------------------------------------ the Admin pages
    def test_the_admin_sees_people_and_roles_and_edits_roles_all_at_once(self):
        admin = self.sign_in("asha@example.com")
        listing = self.call("GET", "/api/users", None, admin).body
        self.assertEqual([r["name"] for r in listing["roles"]], ["User", "Weld Engineer", "Admin"])
        roles = [{"id": r["id"], "name": r["name"], "level": r["level"]} for r in listing["roles"]] + [{"name": "Trainee", "level": 3}, {"name": "Inspector", "level": 3}]
        saved = self.call("POST", "/api/roles/save", {"roles": roles}, admin)
        self.assertEqual(saved.status, 200, saved.body)
        self.assertEqual([(r["name"], r["level"]) for r in saved.body["roles"]], [("User", 1), ("Inspector", 3), ("Trainee", 3), ("Weld Engineer", 5), ("Admin", 10)])
        bad = self.call("POST", "/api/roles/save", {"roles": roles + [{"name": "admin", "level": 4}]}, admin)
        self.assertEqual((bad.status, bad.body["error"]), (400, "Admin is a reserved name."))

    def test_a_role_people_hold_cannot_be_removed_and_the_message_says_how_many(self):
        admin = self.sign_in("asha@example.com")
        self.make_user("eddy", "ed@example.com", level=5)
        roles = [{"id": r["id"], "name": r["name"], "level": r["level"]} for r in self.call("GET", "/api/roles", None, admin).body["roles"] if r["name"] != "Weld Engineer"]
        h = self.call("POST", "/api/roles/save", {"roles": roles}, admin)
        self.assertEqual((h.status, h.body["error"]), (409, "Weld Engineer is used by 1 person. Move them to another role first."))

    def test_changing_a_persons_role_by_role_id_applies_at_once(self):
        admin = self.sign_in("asha@example.com")
        mina = self.make_user("mina", "mina@example.com")
        users = self.call("GET", "/api/users", None, admin).body
        person = next(u for u in users["users"] if u["email"] == "mina@example.com")
        engineer = next(r for r in users["roles"] if r["name"] == "Weld Engineer")
        h = self.call("POST", f"/api/users/{person['id']}/update", {"username": "mina", "email": "mina@example.com", "role_id": engineer["id"]}, admin)
        self.assertEqual((h.status, h.body["user"]["level_name"]), (200, "Weld Engineer"))
        me = self.call("GET", "/api/auth/me", cookie=mina)
        self.assertTrue(me.body["permissions"]["controls"]["5.1"]["write"])             # no sign-out needed: the new level applies on the next request

    def test_an_admin_reset_gives_a_24_hour_password_and_ends_the_old_recovery_key(self):
        admin = self.sign_in("asha@example.com")
        mina = self.call("POST", "/api/auth/signup", {"username": "mina", "email": "mina@example.com", "password": "Mina password 4"})
        key, uid = mina.body["recovery_key"], mina.body["user"]["id"]
        reset = self.call("POST", f"/api/users/{uid}/reset", {}, admin)
        self.assertEqual(reset.status, 200)
        temp = reset.body["temp_password"]
        old_key = self.call("POST", "/api/auth/reset-key", {"email": "mina@example.com", "key": key, "password": "Another password 5"})
        self.assertEqual((old_key.status, old_key.body["code"]), (400, "bad_key"))                  # the old key stopped working
        cookie = self.sign_in("mina@example.com", temp)
        forced = self.call("GET", "/api/users", None, cookie)
        self.assertEqual((forced.status, forced.body.get("must_change")), (403, True))             # only a password change is allowed now
        changed = self.call("POST", "/api/auth/change-password", {"old": temp, "new": "Chosen password 6"}, cookie)
        self.assertEqual(changed.status, 200)
        self.assertRegex(changed.body["recovery_key"], r"^[A-Z2-9]{4}(-[A-Z2-9]{4}){3}$")            # a fresh key, shown to the person alone

    def test_the_recovery_key_resets_a_password_by_email(self):
        mina = self.call("POST", "/api/auth/signup", {"username": "mina", "email": "mina@example.com", "password": "Mina password 4"})
        h = self.call("POST", "/api/auth/reset-key", {"email": "MINA@example.com", "key": mina.body["recovery_key"], "password": "Another password 5"})
        self.assertEqual((h.status, bool(h.body["recovery_key"])), (200, True))
        self.assertTrue(self.sign_in("mina@example.com", "Another password 5"))

    # ------------------------------------------------------------------ the Permissions tab
    def test_the_admin_reads_and_saves_the_permissions_and_a_bad_tree_is_refused(self):
        admin = self.sign_in("asha@example.com")
        got = self.call("GET", "/api/permissions", None, admin)
        self.assertEqual(got.status, 200)
        screens = got.body["screens"]
        self.assertEqual([s["name"] for s in screens][:3], ["Chat (Ask)", "Agents", "Library"])
        drafts = next(s for s in screens if s["name"] == "Drafts")
        for c in drafts["controls"]:
            if c["name"] == "Delete a draft":
                c["read"] = c["write"] = 5
        ok = self.call("POST", "/api/permissions/save", {"screens": screens}, admin)
        self.assertEqual(ok.status, 200, ok.body)
        self.assertTrue(permissions.current().can(5, "4.5", "write"))
        for c in drafts["controls"]:
            if c["name"] == "Delete a draft":
                c["read"], c["write"] = 5, 3
        bad = self.call("POST", "/api/permissions/save", {"screens": screens}, admin)
        self.assertEqual((bad.status, bad.body["code"]), (400, "bad_permissions"))
        self.assertIn("write level cannot be below", bad.body["error"])


if __name__ == "__main__":
    unittest.main()
