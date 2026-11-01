"""Accounts: sign-in by email, sessions, lockout, the password rule, roles, one-time passwords and the Admin safeguards (in-memory SQLite, fake clock)."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from welding_ai.accounts import rules
from welding_ai.accounts.passwords import hash_password, password_problem, temp_password, verify_password
from welding_ai.accounts.service import IDLE_SECONDS, LOCK_SECONDS, MAX_FAILED, TEMP_SECONDS, AccountError, AccountService

ADMIN_PW = "Admin password 1"
OTHER_PW = "Other password 2"


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class PasswordTests(unittest.TestCase):
    def test_hash_round_trip_and_salting(self):
        a, b = hash_password("Correct horse 42"), hash_password("Correct horse 42")
        self.assertNotEqual(a, b)                                   # a fresh salt each time
        self.assertTrue(verify_password("Correct horse 42", a))
        self.assertFalse(verify_password("Correct horse 43", a))

    def test_garbage_hash_is_rejected_not_raised(self):
        for stored in ("", "plain", "scrypt$x$y", "md5$1$1$1$aa$bb"):
            self.assertFalse(verify_password("anything", stored))

    def test_temp_password_shape_and_rules(self):
        for _ in range(200):                                         # it must meet the rule every time, not just usually
            t = temp_password()
            self.assertRegex(t, r"^[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}$")
            self.assertIsNone(password_problem(t), t)
        self.assertNotEqual(temp_password(), temp_password())

    def test_the_rule(self):
        good = ["Password1!", "Welding#26", "aaaaaaAb1", "Abcdefg 1"]
        bad = ["short", "Ab1", "abcdefgh", "Abcdefgh", "aaaaaaaA1", "AAAAAAA1", "A1" + "b" * 70, ""]
        for pw in good:
            self.assertIsNone(password_problem(pw), pw)
        for pw in bad:
            self.assertEqual(password_problem(pw), rules.DEFAULTS["password"]["message"], pw)
        self.assertIsNotNone(password_problem("Naveen.Prabhath1", "naveen.prabhath1"))            # the password must not be the username


class RulesFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "account-rules.json"

    def test_no_file_uses_the_built_in_rules_and_ensure_file_writes_them(self):
        r = rules.Rules(self.path)
        self.assertIsNone(r.password_problem("Password1!"))
        self.assertFalse(self.path.exists())
        r.ensure_file()
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["password"]["pattern"], rules.DEFAULTS["password"]["pattern"])

    def test_an_edit_applies_without_a_restart_and_a_broken_file_is_ignored(self):
        r = rules.Rules(self.path)
        r.ensure_file()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["password"] = {"pattern": "^.{4,}$", "message": "Four characters at least."}
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(r.password_problem("abcd"))
        self.assertEqual(r.password_problem("abc"), "Four characters at least.")
        self.path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(r.password_problem("abc"), "Four characters at least.")             # the last good rules stay
        self.assertIn("was not used", r.problem)
        data["password"]["pattern"] = "([unclosed"
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNotNone(r.password_problem("abc"))                                       # a pattern that does not compile is refused too
        self.assertIn("was not used", r.problem)

    def test_email_and_username_messages(self):
        r = rules.Rules(self.path)
        self.assertEqual(r.email_problem(""), "Enter your email.")
        self.assertEqual(r.email_problem("no-at-sign"), "Email doesn't match the format name@example.com.")
        self.assertEqual(r.email_problem("a@b"), "Email doesn't match the format name@example.com.")
        self.assertIsNone(r.email_problem("name@example.com"))
        self.assertEqual(r.username_problem(""), "Enter a username.")
        self.assertIsNotNone(r.username_problem("ab"))
        self.assertIsNone(r.username_problem("valid.name"))
        self.assertEqual(set(r.describe()), {"password", "email", "username"})
        self.assertNotIn("regex", r.describe()["password"])


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.svc = AccountService(":memory:", clock=self.clock)
        self.admin, _ = self.svc.create_user("admin1", "admin@example.com", "admin", password=ADMIN_PW)

    def code(self, fn, *a, **kw):
        with self.assertRaises(AccountError) as cm:
            fn(*a, **kw)
        return cm.exception.code

    def test_has_users(self):
        self.assertTrue(self.svc.has_users())
        self.assertFalse(AccountService(":memory:").has_users())

    def test_create_validates_input(self):
        self.assertEqual(self.code(self.svc.create_user, "x", "a@b.co"), "bad_username")
        self.assertEqual(self.code(self.svc.create_user, "valid.name", "not-an-email"), "bad_email")
        self.assertEqual(self.code(self.svc.create_user, "valid.name", "a@b.co", "root"), "bad_role")
        self.assertEqual(self.code(self.svc.create_user, "other", "ADMIN@example.com"), "exists")           # the email is unique, ignoring case
        for bad in (0, 10, 11, "5", True, 7):
            self.assertEqual(self.code(self.svc.create_user, "lvlx", "lvlx@example.com", level=bad), "bad_level")

    def test_usernames_may_repeat_and_the_email_tells_people_apart(self):
        a, _ = self.svc.create_user("ravi", "ravi@example.com", password=OTHER_PW)
        b, _ = self.svc.create_user("ravi", "ravi.k@example.com", password=OTHER_PW)
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual({u["email"] for u in self.svc.list_users() if u["username"] == "ravi"}, {"ravi@example.com", "ravi.k@example.com"})
        self.assertEqual(self.svc.login("RAVI.K@example.com", OTHER_PW)[1]["id"], b["id"])
        self.assertEqual(self.code(self.svc.login, "ravi", OTHER_PW), "bad_credentials")                    # a username does not sign anyone in

    def test_new_user_gets_a_temp_password_that_must_be_changed(self):
        user, temp = self.svc.create_user("eng1", "eng1@example.com")
        self.assertTrue(user["must_change"])
        token, signed = self.svc.login("eng1@example.com", temp)
        self.assertTrue(signed["must_change"])
        self.svc.change_password(user["id"], temp, "My own password 7", keep_token=token)
        self.assertFalse(self.svc.session_user(token)["must_change"])

    def test_a_temporary_password_works_for_24_hours_only(self):
        user, temp = self.svc.create_user("eng9", "eng9@example.com")
        self.clock.now += TEMP_SECONDS - 60
        self.svc.login("eng9@example.com", temp)                                                           # still valid, and again (it works until changed)
        self.clock.now += 120
        self.assertEqual(self.code(self.svc.login, "eng9@example.com", temp), "expired")
        fresh = self.svc.admin_reset(user["id"])                                                           # an Admin gives a new one, valid for another 24 hours
        self.svc.login("eng9@example.com", fresh)
        self.svc.change_password(user["id"], fresh, "Chosen password 3")
        self.clock.now += 10 * TEMP_SECONDS
        self.svc.login("eng9@example.com", "Chosen password 3")                                            # a password the person chose never expires this way

    def test_register_makes_a_user_and_nothing_else(self):
        user = self.svc.register("mina", "mina@example.com", "Mina password 4")
        self.assertEqual((user["level"], user["role_name"], user["must_change"]), (1, "User", False))
        self.assertEqual(self.code(self.svc.register, "mina2", "mina@example.com", "Mina password 4"), "exists")
        self.assertEqual(self.code(self.svc.register, "mina3", "mina3@example.com", "weak"), "weak_password")
        self.assertEqual(self.code(self.svc.register, "mina3", "bad-email", "Mina password 4"), "bad_email")
        token = self.svc.start_session(user["id"])
        self.assertEqual(self.svc.session_user(token)["email"], "mina@example.com")

    def test_login_and_session(self):
        token, user = self.svc.login("Admin@Example.com", ADMIN_PW)          # the email ignores case
        self.assertEqual(user["role"], "admin")
        self.assertEqual(self.svc.session_user(token)["username"], "admin1")
        self.svc.logout(token)
        self.assertIsNone(self.svc.session_user(token))
        self.assertIsNone(self.svc.session_user("nonsense"))
        self.assertIsNone(self.svc.session_user(None))

    def test_unknown_email_and_wrong_password_look_the_same(self):
        errors = []
        for email, password in (("nobody@example.com", "Whatever password 1"), ("admin@example.com", "Wrong password 1")):
            with self.assertRaises(AccountError) as caught:
                self.svc.login(email, password)
            errors.append((caught.exception.code, caught.exception.message))
        self.assertEqual(errors[0], errors[1])
        self.assertEqual(errors[0][1], "Wrong email or password.")

    def test_lock_time_grows_to_five_minutes(self):
        waits = []
        for _ in range(MAX_FAILED + 7):
            try:
                self.svc.login("admin@example.com", "Wrong password 1")
            except AccountError as e:
                if e.code == "bad_credentials" and e.wait:
                    waits.append(e.wait)
                    self.clock.now += e.wait + 1
        self.assertEqual(waits, [15, 30, 60, 120, 240, 300, 300, 300])

    def test_lockout_after_repeated_failures_then_expiry(self):
        for _ in range(MAX_FAILED):
            self.code(self.svc.login, "admin@example.com", "Wrong password 1")
        self.assertEqual(self.code(self.svc.login, "admin@example.com", ADMIN_PW), "locked")     # even the right password is refused
        self.clock.now += LOCK_SECONDS + 1
        token, _ = self.svc.login("admin@example.com", ADMIN_PW)
        self.assertIsNotNone(self.svc.session_user(token))

    def test_admin_can_unlock_early(self):
        for _ in range(MAX_FAILED):
            self.code(self.svc.login, "admin@example.com", "Wrong password 1")
        self.svc.unlock(self.admin["id"])
        self.svc.login("admin@example.com", ADMIN_PW)

    def test_session_expires_when_idle(self):
        token, _ = self.svc.login("admin@example.com", ADMIN_PW)
        self.clock.now += IDLE_SECONDS + 1
        self.assertIsNone(self.svc.session_user(token))

    def test_disabled_account_cannot_sign_in_and_loses_its_session(self):
        user, temp = self.svc.create_user("eng2", "eng2@example.com")
        token, _ = self.svc.login("eng2@example.com", temp)
        self.svc.set_active(user["id"], False)
        self.assertIsNone(self.svc.session_user(token))
        self.assertEqual(self.code(self.svc.login, "eng2@example.com", temp), "disabled")
        self.svc.set_active(user["id"], True)
        self.svc.login("eng2@example.com", temp)

    def test_admin_reset_ends_sessions_and_forces_a_change(self):
        user, temp = self.svc.create_user("eng3", "eng3@example.com")
        token, _ = self.svc.login("eng3@example.com", temp)
        new_temp = self.svc.admin_reset(user["id"])
        self.assertIsNone(self.svc.session_user(token))
        self.assertEqual(self.code(self.svc.login, "eng3@example.com", temp), "bad_credentials")
        _, signed = self.svc.login("eng3@example.com", new_temp)
        self.assertTrue(signed["must_change"])

    def test_change_password_checks(self):
        token, _ = self.svc.login("admin@example.com", ADMIN_PW)
        uid = self.admin["id"]
        self.assertEqual(self.code(self.svc.change_password, uid, "Bad old password 1", "New password 12"), "bad_credentials")
        self.assertEqual(self.code(self.svc.change_password, uid, ADMIN_PW, "short"), "weak_password")
        self.assertEqual(self.code(self.svc.change_password, uid, ADMIN_PW, ADMIN_PW), "same_password")
        other, _ = self.svc.login("admin@example.com", ADMIN_PW)
        self.svc.change_password(uid, ADMIN_PW, "Brand new password 5", keep_token=token)
        self.assertIsNotNone(self.svc.session_user(token))             # this session stays
        self.assertIsNone(self.svc.session_user(other))                # every other one ends

    def test_cannot_remove_the_last_administrator(self):
        self.assertEqual(self.code(self.svc.set_active, self.admin["id"], False), "last_admin")
        self.assertEqual(self.code(self.svc.set_role, self.admin["id"], "engineer"), "last_admin")
        second, _ = self.svc.create_user("admin2", "admin2@example.com", "admin")
        self.svc.set_role(self.admin["id"], "engineer")                 # fine once there is another
        self.assertEqual(self.code(self.svc.set_active, second["id"], False), "last_admin")

    def test_list_users_never_exposes_hashes(self):
        for u in self.svc.list_users():
            self.assertNotIn("pw_hash", u)
            self.assertEqual(set(u), {"id", "username", "email", "role", "role_id", "level", "is_admin", "level_name", "role_name", "active", "must_change", "locked", "created"})

    def test_update_user_and_the_admin_rule(self):
        user, _ = self.svc.create_user("upd1", "upd1@example.com")
        engineer = next(r for r in self.svc.list_roles() if r["name"] == "Weld Engineer")
        admin_role = next(r for r in self.svc.list_roles() if r["name"] == "Admin")
        user_role = next(r for r in self.svc.list_roles() if r["name"] == "User")
        changed = self.svc.update_user(user["id"], "upd1b", "upd1b@example.com", engineer["id"])
        self.assertEqual((changed["username"], changed["level"], changed["level_name"]), ("upd1b", 5, "Weld Engineer"))
        self.assertEqual(self.code(self.svc.update_user, user["id"], "upd1b", "upd1b@example.com", admin_role["id"]), "bad_level")
        self.assertEqual(self.code(self.svc.update_user, user["id"], "ab", "upd1b@example.com", engineer["id"]), "bad_username")
        self.assertEqual(self.code(self.svc.update_user, user["id"], "upd1b", "admin@example.com", engineer["id"]), "exists")
        self.assertEqual(self.code(self.svc.update_user, user["id"], "upd1b", "upd1b@example.com", 9999), "bad_role")
        self.assertEqual(self.code(self.svc.update_user, self.admin["id"], "admin1", "admin@example.com", user_role["id"]), "last_admin")

    def test_a_role_change_applies_from_the_next_request_without_signing_out(self):
        user, temp = self.svc.create_user("upd2", "upd2@example.com", password=OTHER_PW)
        token, _ = self.svc.login("upd2@example.com", OTHER_PW)
        engineer = next(r for r in self.svc.list_roles() if r["name"] == "Weld Engineer")
        self.svc.update_user(user["id"], "upd2", "upd2@example.com", engineer["id"])
        self.assertEqual(self.svc.session_user(token)["level"], 5)

    def test_delete_user(self):
        user, _ = self.svc.create_user("del1", "del1@example.com")
        self.assertEqual(self.code(self.svc.delete_user, self.admin["id"], self.admin["id"]), "self")
        self.assertEqual(self.code(self.svc.delete_user, self.admin["id"], user["id"]), "last_admin")
        self.svc.delete_user(user["id"], self.admin["id"])
        self.assertEqual(self.code(self.svc.delete_user, user["id"], self.admin["id"]), "not_found")
        self.assertFalse(any(u["username"] == "del1" for u in self.svc.list_users()))


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.svc = AccountService(":memory:", clock=Clock())
        self.admin, _ = self.svc.create_user("admin1", "admin@example.com", "admin", password=ADMIN_PW)

    def code(self, fn, *a, **kw):
        with self.assertRaises(AccountError) as cm:
            fn(*a, **kw)
        return cm.exception.code

    def roles(self):
        return [{"id": r["id"], "name": r["name"], "level": r["level"]} for r in self.svc.list_roles()]

    def test_the_starting_roles(self):
        self.assertEqual([(r["name"], r["level"], r["fixed"]) for r in self.svc.list_roles()], [("User", 1, True), ("Weld Engineer", 5, False), ("Admin", 10, True)])

    def test_add_edit_and_remove_in_one_save_and_levels_may_repeat(self):
        saved = self.svc.save_roles(self.roles() + [{"name": "Trainee", "level": 3}, {"name": "Inspector", "level": 3}])
        self.assertEqual([(r["name"], r["level"]) for r in saved], [("User", 1), ("Inspector", 3), ("Trainee", 3), ("Weld Engineer", 5), ("Admin", 10)])
        trainee = next(r for r in saved if r["name"] == "Trainee")
        renamed = [dict(r, name="Apprentice") if r["id"] == trainee["id"] else {"id": r["id"], "name": r["name"], "level": r["level"]} for r in saved]
        renamed = [r for r in renamed if r["name"] != "Inspector"]                                           # a role left out of the list is removed
        self.assertEqual([r["name"] for r in self.svc.save_roles(renamed)], ["User", "Apprentice", "Weld Engineer", "Admin"])

    def test_user_and_admin_are_fixed_and_admin_is_reserved(self):
        current = self.roles()
        user_role = next(r for r in current if r["name"] == "User")
        self.assertEqual(self.code(self.svc.save_roles, [dict(r, name="Basic") if r["id"] == user_role["id"] else r for r in current]), "bad_role")
        self.assertEqual(self.code(self.svc.save_roles, [r for r in current if r["name"] != "Admin"]), "bad_role")
        self.assertEqual(self.code(self.svc.save_roles, current + [{"name": "admin", "level": 4}]), "bad_role")
        for bad in (0, 10, 11, "4", None):
            self.assertEqual(self.code(self.svc.save_roles, current + [{"name": "Odd", "level": bad}]), "bad_role")

    def test_names_must_be_filled_and_unique_and_nothing_is_saved_on_a_failure(self):
        current = self.roles()
        self.assertEqual(self.code(self.svc.save_roles, current + [{"name": " ", "level": 4}]), "bad_role")
        self.assertEqual(self.code(self.svc.save_roles, current + [{"name": "Trainee", "level": 3}, {"name": "trainee", "level": 4}]), "bad_role")
        self.assertEqual(self.roles(), current)                                                              # all or nothing

    def test_a_role_in_use_cannot_be_removed_and_a_level_change_follows_the_people(self):
        engineer = next(r for r in self.roles() if r["name"] == "Weld Engineer")
        person, _ = self.svc.create_user("eng", "eng@example.com", level=5, password=OTHER_PW)
        without = [r for r in self.roles() if r["name"] != "Weld Engineer"]
        self.assertEqual(self.code(self.svc.save_roles, without), "role_in_use")
        moved = [dict(r, level=6) if r["id"] == engineer["id"] else r for r in self.roles()]
        self.svc.save_roles(moved)
        self.assertEqual(next(u for u in self.svc.list_users() if u["email"] == "eng@example.com")["level"], 6)
        self.assertEqual(person["role_name"], "Weld Engineer")


class MigrationTests(unittest.TestCase):
    OLD = ("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, email TEXT NOT NULL UNIQUE COLLATE NOCASE, pw_hash TEXT NOT NULL,"
           " role TEXT NOT NULL DEFAULT 'engineer', active INTEGER NOT NULL DEFAULT 1, level INTEGER NOT NULL DEFAULT 2, must_change INTEGER NOT NULL DEFAULT 0,"
           " failed INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0, created REAL NOT NULL);"
           "CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, created REAL NOT NULL, last_seen REAL NOT NULL);")

    def test_an_older_database_gets_roles_and_a_shareable_username(self):
        path = Path(tempfile.mkdtemp()) / "old.db"
        db = sqlite3.connect(path)
        db.executescript(self.OLD)
        for row in ((1, "boss", "boss@example.com", "admin", 10, 0), (2, "viewer", "v@example.com", "engineer", 1, 0), (3, "user", "u@example.com", "engineer", 2, 0),
                    (4, "editor", "e@example.com", "engineer", 5, 0), (5, "manager", "m@example.com", "engineer", 8, 1)):
            db.execute("INSERT INTO users (id, username, email, pw_hash, role, level, must_change, created) VALUES (?, ?, ?, 'x', ?, ?, ?, 1)", (row[0], row[1], row[2], row[3], row[4], row[5]))
        db.execute("INSERT INTO sessions VALUES ('t', 1, 1, 1)")
        db.commit()
        db.close()
        svc = AccountService(path)
        self.assertEqual({u["username"]: (u["level"], u["level_name"]) for u in svc.list_users()},
                         {"boss": (10, "Admin"), "viewer": (1, "User"), "user": (1, "User"), "editor": (5, "Weld Engineer"), "manager": (5, "Weld Engineer")})
        self.assertTrue(next(u for u in svc.list_users() if u["username"] == "manager")["must_change"])
        svc.create_user("boss", "boss2@example.com", password=OTHER_PW)                                      # the username may now repeat
        db = sqlite3.connect(path)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)
        self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        db.close()
        self.assertEqual(len(AccountService(path).list_users()), 6)                                          # opening it again changes nothing

    def test_a_database_without_levels_is_still_understood(self):
        path = Path(tempfile.mkdtemp()) / "older.db"
        db = sqlite3.connect(path)
        db.executescript("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, email TEXT NOT NULL UNIQUE COLLATE NOCASE, pw_hash TEXT NOT NULL,"
                         " role TEXT NOT NULL DEFAULT 'engineer', active INTEGER NOT NULL DEFAULT 1, must_change INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,"
                         " locked_until REAL NOT NULL DEFAULT 0, created REAL NOT NULL);"
                         "INSERT INTO users (username, email, pw_hash, role, created) VALUES ('boss', 'b@example.com', 'x', 'admin', 1), ('eng', 'e@example.com', 'x', 'engineer', 1);")
        db.commit()
        db.close()
        svc = AccountService(path)
        self.assertEqual({u["username"]: u["level"] for u in svc.list_users()}, {"boss": 10, "eng": 1})


if __name__ == "__main__":
    unittest.main()
