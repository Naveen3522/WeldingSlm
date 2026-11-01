"""Accounts: users, roles, sessions and administration, stored in SQLite.

    register · create_user · login · session_user · logout · change_password
    admin_reset · set_active · update_user · delete_user · unlock · list_users · list_roles · save_roles

A person signs in with their email address. The username is only a display name and may be shared by several people; the email is the account's identity and
is unique (case ignored). Every person has a role from the `roles` table (User 1, Weld Engineer 5, Admin 10 to begin with); the role's level is the number the
permissions compare against. Every failure is an AccountError with a short code and a message that is safe to show to the user.
"""
import hashlib
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from welding_ai.accounts import levels, rules
from welding_ai.accounts.passwords import hash_password, password_problem, temp_password, verify_password
from welding_ai.core.log import get_logger

log = get_logger("accounts")

ROLES = ("engineer", "admin")                        # the older two-value `role` column, kept in step with the role (admin or not)
MAX_FAILED, LOCK_SECONDS, LOCK_MAX = 5, 15, 5 * 60     # after five wrong passwords the account locks for 15 seconds; each further wrong one doubles it, up to 5 minutes
IDLE_SECONDS, SESSION_SECONDS = 8 * 3600, 24 * 3600  # signed out after 8 h idle, or 24 h in all
TEMP_SECONDS = 24 * 3600                             # a one-time password works for 24 hours
TOUCH_SECONDS = 60                                   # last-seen is written at most once a minute
BAD_LOGIN = "Wrong email or password."

SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    level   INTEGER NOT NULL,
    fixed   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY,
    username     TEXT NOT NULL,
    email        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash      TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'engineer',
    role_id      INTEGER REFERENCES roles(id),
    active       INTEGER NOT NULL DEFAULT 1,
    level        INTEGER NOT NULL DEFAULT 1,
    must_change  INTEGER NOT NULL DEFAULT 0,
    temp_expires REAL NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0,
    created      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created    REAL NOT NULL,
    last_seen  REAL NOT NULL
);
"""
USER_COLUMNS = "id, username, email, pw_hash, role, role_id, active, level, must_change, temp_expires, failed, locked_until, created"


class AccountError(Exception):
    def __init__(self, code, message, wait=0):
        super().__init__(message)
        self.code, self.message, self.wait = code, message, wait          # wait: seconds until another try is allowed (0 when not locked)


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class AccountService:
    def __init__(self, path, clock=time.time, vault=None):
        self.clock = clock
        self.vault = vault                                  # an AdminFile: told about every administrator password change (never lets a file problem stop an account action)
        self._lock = threading.RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._seed_roles()
        self._migrate()
        self._dummy_hash = None

    # ------------------------------------------------------------------ roles and the move from older databases
    def _seed_roles(self):
        if self._db.execute("SELECT 1 FROM roles LIMIT 1").fetchone() is None:
            self._db.executemany("INSERT INTO roles (name, level, fixed) VALUES (?, ?, ?)", [("User", 1, 1), ("Weld Engineer", 5, 0), ("Admin", levels.ADMIN_LEVEL, 1)])
            self._db.commit()

    def _role_id_for_level(self, level):
        """The role an old level becomes: 10 is Admin, 5 to 9 is Weld Engineer, anything lower is User (the older five roles are folded into three)."""
        name = "Admin" if level >= levels.ADMIN_LEVEL else "Weld Engineer" if level >= 5 else "User"
        row = self._db.execute("SELECT id FROM roles WHERE name = ?", (name,)).fetchone()
        return row["id"] if row else self._db.execute("SELECT id FROM roles ORDER BY level LIMIT 1").fetchone()["id"]

    def _migrate(self):
        """Databases made before email sign-in and roles get the new shape: the username stops being unique, every person gets a role, and the older
        levels (1, 2, 5, 8, 10) become User 1, Weld Engineer 5 and Admin 10."""
        sql = self._db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()["sql"]
        columns = {r["name"] for r in self._db.execute("PRAGMA table_info(users)")}
        old_unique = "username     TEXT NOT NULL UNIQUE" in sql or "username TEXT NOT NULL UNIQUE" in sql
        if "level" not in columns:
            self._db.execute("ALTER TABLE users ADD COLUMN level INTEGER NOT NULL DEFAULT 1")
            self._db.execute("UPDATE users SET level = ? WHERE role = 'admin'", (levels.ADMIN_LEVEL,))
            columns.add("level")
        if not old_unique and {"role_id", "temp_expires"} <= columns:
            self._db.commit()
            return
        now = self.clock()
        self._db.commit()
        self._db.execute("PRAGMA foreign_keys = OFF")                   # the order SQLite documents for changing a table that others point at
        try:
            self._db.execute("BEGIN")
            self._db.execute("""CREATE TABLE users_new (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE, pw_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'engineer', role_id INTEGER REFERENCES roles(id), active INTEGER NOT NULL DEFAULT 1,
                level INTEGER NOT NULL DEFAULT 1, must_change INTEGER NOT NULL DEFAULT 0, temp_expires REAL NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0, created REAL NOT NULL)""")
            for r in self._db.execute("SELECT * FROM users").fetchall():
                old_level = r["level"] if "level" in r.keys() else (levels.ADMIN_LEVEL if r["role"] == "admin" else 2)
                rid = self._role_id_for_level(old_level)
                level = self._db.execute("SELECT level FROM roles WHERE id = ?", (rid,)).fetchone()["level"]
                self._db.execute(
                    "INSERT INTO users_new (id, username, email, pw_hash, role, role_id, active, level, must_change, temp_expires, failed, locked_until, created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["id"], r["username"], r["email"], r["pw_hash"], "admin" if level == levels.ADMIN_LEVEL else "engineer", rid, r["active"], level,
                     r["must_change"], now + TEMP_SECONDS if r["must_change"] else 0, r["failed"], r["locked_until"], r["created"]))
            self._db.execute("DROP TABLE users")
            self._db.execute("ALTER TABLE users_new RENAME TO users")
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            raise
        finally:
            self._db.execute("PRAGMA foreign_keys = ON")

    def list_roles(self):
        with self._lock:
            rows = self._db.execute(
                "SELECT r.id, r.name, r.level, r.fixed, (SELECT COUNT(*) FROM users u WHERE u.role_id = r.id) AS people FROM roles r ORDER BY r.level, r.name COLLATE NOCASE").fetchall()
        return [{"id": r["id"], "name": r["name"], "level": r["level"], "fixed": bool(r["fixed"]), "people": r["people"]} for r in rows]

    def save_roles(self, roles):
        """Applies a whole list of roles at once (all or nothing): [{"id": 4, "name": "Trainee", "level": 3}, {"name": "New", "level": 4}, ...].
        User (1) and Admin (10) cannot change or go; the others may be renamed, moved to another level 1 to 9 (several roles may share a level), added or
        removed. A role people still hold cannot be removed. A role that was left out of the list is removed."""
        if not isinstance(roles, list):
            raise AccountError("bad_roles", "Send the list of roles.")
        with self._lock:
            current = {r["id"]: r for r in self._db.execute("SELECT * FROM roles").fetchall()}
            seen_names, keep = set(), set()
            for item in roles:
                name = str((item or {}).get("name", "")).strip()
                if not name:
                    raise AccountError("bad_role", "Every role needs a name.")
                rid = item.get("id")
                level = item.get("level")
                if name.lower() == "admin" and not (rid in current and current[rid]["fixed"]):
                    raise AccountError("bad_role", "Admin is a reserved name.")
                if name.lower() in seen_names:
                    raise AccountError("bad_role", f'"{name}" is used twice.')
                seen_names.add(name.lower())
                if rid in current and current[rid]["fixed"]:
                    if name != current[rid]["name"] or int(level) != current[rid]["level"]:
                        raise AccountError("bad_role", f'"{current[rid]["name"]}" cannot be changed.')
                else:
                    if name.lower() == "admin":
                        raise AccountError("bad_role", "Admin is a reserved name.")
                    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= levels.MAX_ASSIGNABLE:
                        raise AccountError("bad_role", f"Level for \"{name}\" must be a whole number from 1 to {levels.MAX_ASSIGNABLE}.")
                if rid in current:
                    keep.add(rid)
            for rid, r in current.items():
                if rid not in keep:
                    if r["fixed"]:
                        raise AccountError("bad_role", f'"{r["name"]}" cannot be removed.')
                    n = self._db.execute("SELECT COUNT(*) FROM users WHERE role_id = ?", (rid,)).fetchone()[0]
                    if n:
                        raise AccountError("role_in_use", f'{r["name"]} is used by {n} {"person" if n == 1 else "people"}. Move them to another role first.')
            try:
                self._db.execute("BEGIN")
                for rid, r in current.items():
                    if rid not in keep:
                        self._db.execute("DELETE FROM roles WHERE id = ?", (rid,))
                # rename in two passes so two roles may swap names
                for item in roles:
                    rid = item.get("id")
                    if rid in current and not current[rid]["fixed"]:
                        self._db.execute("UPDATE roles SET name = ? WHERE id = ?", (f"~{secrets.token_hex(4)}", rid))
                for item in roles:
                    name, level, rid = str(item["name"]).strip(), item.get("level"), item.get("id")
                    if rid in current:
                        if not current[rid]["fixed"]:
                            self._db.execute("UPDATE roles SET name = ?, level = ? WHERE id = ?", (name, level, rid))
                            self._db.execute("UPDATE users SET level = ? WHERE role_id = ?", (level, rid))
                    else:
                        self._db.execute("INSERT INTO roles (name, level, fixed) VALUES (?, ?, 0)", (name, level))
                self._db.execute("COMMIT")
            except sqlite3.IntegrityError:
                self._db.execute("ROLLBACK")
                raise AccountError("bad_role", "Two roles have the same name.")
        return self.list_roles()

    # ------------------------------------------------------------------ reading
    def has_users(self):
        with self._lock:
            return self._db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def list_users(self):
        with self._lock:
            rows = self._db.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE, email COLLATE NOCASE").fetchall()
            return [self._public(r) for r in rows]

    def _role_name(self, role_id, level):
        row = self._db.execute("SELECT name FROM roles WHERE id = ?", (role_id,)).fetchone() if role_id else None
        return row["name"] if row else levels.name(level)

    def _public(self, row):
        name = self._role_name(row["role_id"], row["level"])
        return {"id": row["id"], "username": row["username"], "email": row["email"], "role": row["role"], "role_id": row["role_id"],
                "level": row["level"], "is_admin": levels.is_admin(row["level"]), "level_name": name, "role_name": name, "active": bool(row["active"]), "must_change": bool(row["must_change"]),
                "locked": row["locked_until"] > self.clock(), "created": row["created"]}

    def _row(self, user_id):
        row = self._db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise AccountError("not_found", "No such user.")
        return row

    def _vault(self, user_id, password=None, forget=False):
        if self.vault is None:
            return
        try:
            if forget:
                self.vault.forget(user_id)
            else:
                self.vault.record(self._public(self._row(user_id)), password=password)
        except Exception as e:
            log.warning("could not update the administrator file: %s", e)

    # ------------------------------------------------------------------ creating
    def _check_identity(self, username, email):
        """-> (username, email) cleaned, or an AccountError with the message from the rules file."""
        username, email = (username or "").strip(), (email or "").strip()
        if (problem := rules.current().username_problem(username)):
            raise AccountError("bad_username", problem)
        if (problem := rules.current().email_problem(email)):
            raise AccountError("bad_email", problem)
        return username, email

    def _insert(self, username, email, password_hash, role_id, must_change, temp_expires):
        role = self._db.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
        try:
            cur = self._db.execute(
                "INSERT INTO users (username, email, pw_hash, role, role_id, level, must_change, temp_expires, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (username, email, password_hash, "admin" if role["level"] == levels.ADMIN_LEVEL else "engineer", role_id, role["level"], must_change, temp_expires, self.clock()))
            self._db.commit()
        except sqlite3.IntegrityError:
            self._db.rollback()
            raise AccountError("exists", "That email is already in use.")
        return self._public(self._row(cur.lastrowid))

    def register(self, username, email, password):
        """Self sign-up: always a User (the lowest role), never a chosen role. -> the new user."""
        username, email = self._check_identity(username, email)
        if (problem := password_problem(password or "", username)):
            raise AccountError("weak_password", problem)
        with self._lock:
            user_role = self._db.execute("SELECT id FROM roles WHERE name = 'User'").fetchone()["id"]
            return self._insert(username, email, hash_password(password), user_role, 0, 0)

    def create_user(self, username, email, role="engineer", password=None, level=None):
        """Returns (user, temporary_password). With no password given, a one-time one is made: it works for 24 hours and must be changed at first sign-in.
        `level` picks the first role with that level; 10 (or role "admin") is Admin and is only made by scripts/create_admin.py."""
        username, email = self._check_identity(username, email)
        if role not in ROLES:
            raise AccountError("bad_role", "Role must be engineer or admin.")
        with self._lock:
            if level is None:
                level = levels.ADMIN_LEVEL if role == "admin" else 1
            elif role == "admin" or isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= levels.MAX_ASSIGNABLE:
                raise AccountError("bad_level", f"Level must be a whole number from 1 to {levels.MAX_ASSIGNABLE} (10 is the administrator).")
            row = self._db.execute("SELECT id FROM roles WHERE level = ? ORDER BY id LIMIT 1", (level,)).fetchone()
            if row is None:
                raise AccountError("bad_level", f"No role has level {level}.")
            temp = None
            if password is None:
                password = temp = temp_password()
            elif (problem := password_problem(password, username)):
                raise AccountError("weak_password", problem)
            user = self._insert(username, email, hash_password(password), row["id"], 1 if temp else 0, self.clock() + TEMP_SECONDS if temp else 0)
            self._vault(user["id"], password)
            return user, temp

    # ------------------------------------------------------------------ signing in and out
    def login(self, email, password):
        """Returns (session_token, user) for an email address. Wrong email and wrong password give the same message."""
        now = self.clock()
        with self._lock:
            who = (email or "").strip()
            row = self._db.execute("SELECT * FROM users WHERE email = ?", (who,)).fetchone()
            if row is None:
                verify_password(password or "", self._dummy())          # same time as a real check: no way to probe for accounts
                raise AccountError("bad_credentials", BAD_LOGIN)
            if row["locked_until"] > now:
                seconds = int(row["locked_until"] - now) + 1
                raise AccountError("locked", f"Too many wrong attempts. Try again in {seconds} second{'s' if seconds != 1 else ''}, or ask an Admin.", seconds)
            if not verify_password(password or "", row["pw_hash"]):
                failed = row["failed"] + 1
                pause = min(LOCK_MAX, LOCK_SECONDS * 2 ** (failed - MAX_FAILED)) if failed >= MAX_FAILED else 0
                self._db.execute("UPDATE users SET failed = ?, locked_until = ? WHERE id = ?", (failed, now + pause if pause else 0, row["id"]))
                self._db.commit()
                raise AccountError("bad_credentials", BAD_LOGIN, pause)
            if row["must_change"] and row["temp_expires"] and now > row["temp_expires"]:
                raise AccountError("expired", "This one-time password has expired. Ask an Admin for a new one.")
            if not row["active"]:                                       # only said to someone who knows the password
                raise AccountError("disabled", "This account is disabled. Ask an Admin.")
            self._db.execute("UPDATE users SET failed = 0, locked_until = 0 WHERE id = ?", (row["id"],))
            self._db.execute("DELETE FROM sessions WHERE ? - last_seen > ? OR ? - created > ?", (now, IDLE_SECONDS, now, SESSION_SECONDS))
            token = secrets.token_urlsafe(32)
            self._db.execute("INSERT INTO sessions (token_hash, user_id, created, last_seen) VALUES (?, ?, ?, ?)", (_digest(token), row["id"], now, now))
            self._db.commit()
            return token, self._public(self._row(row["id"]))

    def start_session(self, user_id):
        """A session for a person who has just proved who they are in another way (sign-up). -> the session token."""
        now = self.clock()
        with self._lock:
            self._row(user_id)
            token = secrets.token_urlsafe(32)
            self._db.execute("INSERT INTO sessions (token_hash, user_id, created, last_seen) VALUES (?, ?, ?, ?)", (_digest(token), user_id, now, now))
            self._db.commit()
            return token

    def session_user(self, token):
        """The signed-in user for a session cookie, or None (unknown, expired, or the account was disabled). The level is read on every call, so a change of
        role applies from the person's next request."""
        if not token:
            return None
        now = self.clock()
        with self._lock:
            s = self._db.execute("SELECT * FROM sessions WHERE token_hash = ?", (_digest(token),)).fetchone()
            if s is None:
                return None
            row = self._db.execute("SELECT * FROM users WHERE id = ?", (s["user_id"],)).fetchone()
            if row is None or not row["active"] or now - s["last_seen"] > IDLE_SECONDS or now - s["created"] > SESSION_SECONDS:
                self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (s["token_hash"],))
                self._db.commit()
                return None
            if now - s["last_seen"] > TOUCH_SECONDS:
                self._db.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, s["token_hash"]))
                self._db.commit()
            return self._public(row)

    def logout(self, token):
        if token:
            with self._lock:
                self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(token),))
                self._db.commit()

    def _dummy(self):
        if self._dummy_hash is None:
            self._dummy_hash = hash_password(secrets.token_urlsafe(8))
        return self._dummy_hash

    # ------------------------------------------------------------------ passwords
    def change_password(self, user_id, old, new, keep_token=None):
        with self._lock:
            row = self._row(user_id)
            if not verify_password(old or "", row["pw_hash"]):
                raise AccountError("bad_credentials", "The current password is wrong.")
            if (problem := password_problem(new or "", row["username"])):
                raise AccountError("weak_password", problem)
            if new == old:
                raise AccountError("same_password", "Choose a password you have not used just now.")
            self._db.execute("UPDATE users SET pw_hash = ?, must_change = 0, temp_expires = 0 WHERE id = ?", (hash_password(new), user_id))
            self._db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, _digest(keep_token or "")))
            self._db.commit()
            self._vault(user_id, new)

    def check_password(self, user_id, password):
        """True when this is the person's current password (for actions that need it again, such as making a new recovery key)."""
        with self._lock:
            return verify_password(password or "", self._row(user_id)["pw_hash"])

    def admin_reset(self, user_id):
        """A new one-time password, valid for 24 hours, to be changed at the next sign-in. Ends the user's sessions and clears any lock."""
        temp = temp_password()
        with self._lock:
            self._row(user_id)
            self._db.execute("UPDATE users SET pw_hash = ?, must_change = 1, temp_expires = ?, failed = 0, locked_until = 0 WHERE id = ?",
                             (hash_password(temp), self.clock() + TEMP_SECONDS, user_id))
            self._db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self._db.commit()
            self._vault(user_id, temp)
        return temp

    def find_for_recovery(self, identifier):
        """The active account with this email address (case ignored), as {"id", "username", "email"}; None for no such account or a disabled one."""
        identifier = (identifier or "").strip()
        if not identifier:
            return None
        with self._lock:
            row = self._db.execute("SELECT id, username, email, active FROM users WHERE email = ?", (identifier,)).fetchone()
        return {"id": row["id"], "username": row["username"], "email": row["email"]} if row and row["active"] else None

    def reset_password(self, user_id, new_password):
        """Sets a password the person chose (after proving they own the account): no forced change afterwards, lock cleared, every session ended. -> the username."""
        with self._lock:
            row = self._row(user_id)
            if (problem := password_problem(new_password or "", row["username"])):
                raise AccountError("weak_password", problem)
            self._db.execute("UPDATE users SET pw_hash = ?, must_change = 0, temp_expires = 0, failed = 0, locked_until = 0 WHERE id = ?", (hash_password(new_password), user_id))
            self._db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self._db.commit()
            self._vault(user_id, new_password)
        return row["username"]

    # ------------------------------------------------------------------ administration
    def set_active(self, user_id, active):
        with self._lock:
            row = self._row(user_id)
            if not active:
                self._keep_one_admin(row)
            self._db.execute("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id))
            if not active:
                self._db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self._db.commit()

    def set_role(self, user_id, role):
        """The older call: "admin" or "engineer". `engineer` means the role at level 5 (Weld Engineer) if there is one, else User."""
        if role not in ROLES:
            raise AccountError("bad_role", "Role must be engineer or admin.")
        with self._lock:
            row = self._row(user_id)
            if role != "admin":
                self._keep_one_admin(row)
            level = levels.ADMIN_LEVEL if role == "admin" else min(row["level"], levels.MAX_ASSIGNABLE)
            rid = self._db.execute("SELECT id FROM roles WHERE level = ? ORDER BY id LIMIT 1", (level,)).fetchone()
            rid = rid["id"] if rid else self._role_id_for_level(level)
            self._db.execute("UPDATE users SET role = ?, role_id = ?, level = (SELECT level FROM roles WHERE id = ?) WHERE id = ?", (role, rid, rid, user_id))
            self._db.commit()

    def update_user(self, user_id, username, email, role_id):
        """Edit a person: username (a display name), email and role. Nobody can be given Admin here (administrators are made with scripts/create_admin.py),
        but an Admin can be moved to another role while another Admin remains. The new role applies from the person's next request."""
        username, email = self._check_identity(username, email)
        with self._lock:
            row = self._row(user_id)
            role = self._db.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
            if role is None:
                raise AccountError("bad_role", "Choose a role from the list.")
            is_admin_row = row["level"] == levels.ADMIN_LEVEL
            if role["level"] == levels.ADMIN_LEVEL and not is_admin_row:
                raise AccountError("bad_level", "Admin cannot be given here. Create an Admin with scripts/create_admin.py.")
            if role["level"] != levels.ADMIN_LEVEL and is_admin_row:
                self._keep_one_admin(row)
            try:
                self._db.execute("UPDATE users SET username = ?, email = ?, role_id = ?, level = ?, role = ? WHERE id = ?",
                                 (username, email, role["id"], role["level"], "admin" if role["level"] == levels.ADMIN_LEVEL else "engineer", user_id))
                self._db.commit()
            except sqlite3.IntegrityError:
                self._db.rollback()
                raise AccountError("exists", "That email is already in use.")
            self._vault(user_id)
            return self._public(self._row(user_id))

    def delete_user(self, user_id, by_user_id):
        """Removes the account and its sessions for good. Not your own account, and never the only administrator."""
        with self._lock:
            row = self._row(user_id)
            if user_id == by_user_id:
                raise AccountError("self", "You cannot delete your own account.")
            self._keep_one_admin(row)
            self._db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self._db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self._db.commit()
            self._vault(user_id, forget=True)

    def unlock(self, user_id):
        with self._lock:
            self._row(user_id)
            self._db.execute("UPDATE users SET failed = 0, locked_until = 0 WHERE id = ?", (user_id,))
            self._db.commit()

    # Refuses a change that would leave nobody able to administer the system.
    def _keep_one_admin(self, row):
        if row["role"] == "admin" and row["active"]:
            others = self._db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id != ?", (row["id"],)).fetchone()[0]
            if others == 0:
                raise AccountError("last_admin", "This is the only active Admin, so it cannot be disabled or demoted.")
