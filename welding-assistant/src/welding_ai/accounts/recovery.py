"""Forgotten passwords: a six-digit code sent by email, then a short-lived token to set a new password.

    request(identifier)        sends a code (the answer is the same whether or not the account exists, so nobody can probe for accounts)
    verify(identifier, code)   the right code gives a one-time token
    reset(token, password)     the token sets the new password and ends every session of that person

Besides the email code there is a recovery key: sixteen letters and digits made once per account, shown once, kept only as a keyed hash. Email + key + a
new password resets the password and issues a fresh key; five wrong keys in an hour block further tries for that account. No mail server is needed.

The code lives for `ttl` seconds (WELD_OTP_TTL, 120 by default), works once, and is guessable only five times; a new request cancels the old code; a person can
ask for one a minute and five an hour. Only a keyed hash of the code and the token is stored, never the code itself.
"""
import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from welding_ai.accounts.service import AccountError

DEFAULT_TTL = 120
TOKEN_TTL = 10 * 60
MAX_GUESSES = 5
RESEND_AFTER = 60
PER_HOUR = 5
KEY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"       # no 0 O 1 I L: easy to copy from paper
KEY_TRIES = 5
BAD_KEY = "The email or recovery key is wrong."
WRONG = "That code is wrong or has expired. Ask for a new one."
SENT = "If that account exists, a code has been sent to its email address."

SCHEMA = """
CREATE TABLE IF NOT EXISTS password_resets (
    id        INTEGER PRIMARY KEY,
    user_id   INTEGER NOT NULL,
    kind      TEXT NOT NULL,
    digest    TEXT NOT NULL,
    expires   REAL NOT NULL,
    attempts  INTEGER NOT NULL DEFAULT 0,
    used      INTEGER NOT NULL DEFAULT 0,
    created   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS password_resets_user ON password_resets (user_id, kind, id);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
log = logging.getLogger("welding.recovery")


class RecoveryError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def ttl_from_env(env=None):
    try:
        return max(30, int((os.environ if env is None else env).get("WELD_OTP_TTL", DEFAULT_TTL)))
    except ValueError:
        return DEFAULT_TTL


class Recovery:
    def __init__(self, path, accounts, mailer, ttl=DEFAULT_TTL, clock=time.time, vault=None):
        self.accounts, self.mailer, self.ttl, self.clock = accounts, mailer, ttl, clock
        self.vault = vault                                  # an AdminFile: given each new recovery key of an administrator
        self._lock = threading.RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._key = self._secret()

    def _secret(self):
        """The key the codes are hashed with: made once, kept in the database, so a copy of the table alone does not reveal a code."""
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key = 'recovery_secret'").fetchone()
            if row:
                return bytes.fromhex(row["value"])
            key = secrets.token_bytes(32)
            self._db.execute("INSERT INTO settings (key, value) VALUES ('recovery_secret', ?)", (key.hex(),))
            self._db.commit()
            return key

    def _hash(self, user_id, value):
        return hmac.new(self._key, f"{user_id}:{value}".encode(), hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------ asking for a code
    def request(self, identifier):
        """-> {"message", "ttl", "resend_after"}: always the same, so it says nothing about whether the account exists."""
        reply = {"message": SENT, "ttl": self.ttl, "resend_after": RESEND_AFTER}
        user = self.accounts.find_for_recovery(identifier)
        if user is None:
            return reply
        now = self.clock()
        with self._lock:
            recent = self._db.execute("SELECT created FROM password_resets WHERE user_id = ? AND kind = 'code' AND created > ? ORDER BY id DESC", (user["id"], now - 3600)).fetchall()
            if recent and (now - recent[0]["created"] < RESEND_AFTER or len(recent) >= PER_HOUR):
                return reply                                                    # too soon or too many: nothing is sent, and nothing says so
            code = f"{secrets.randbelow(10 ** 6):06d}"
            self._db.execute("UPDATE password_resets SET used = 1 WHERE user_id = ? AND kind = 'code' AND used = 0", (user["id"],))   # the new code cancels any earlier code
            self._db.execute("INSERT INTO password_resets (user_id, kind, digest, expires, created) VALUES (?, 'code', ?, ?, ?)", (user["id"], self._hash(user["id"], code), now + self.ttl, now))
            self._db.commit()
        minutes = self.ttl / 60
        body = (f"Your Weld Assist code is {code}\n\nIt works for {minutes:g} minute{'s' if minutes != 1 else ''} and only once. "
                "If you did not ask to reset your password, ignore this email: nothing changes.")
        try:
            self.mailer.send(user["email"], "Your Weld Assist code", body)
        except Exception as e:                                                  # the person is told the same either way; the administrator sees why in the log
            log.warning("could not send a recovery email: %s", e)
        return reply

    # ------------------------------------------------------------------ the code
    def verify(self, identifier, code):
        """-> a one-time token. Raises RecoveryError("bad_code") for a wrong, used or expired code, whether or not the account exists."""
        user = self.accounts.find_for_recovery(identifier)
        code = (code or "").strip().replace(" ", "")
        if user is None or not (code.isdigit() and len(code) == 6):
            raise RecoveryError("bad_code", WRONG)
        now = self.clock()
        with self._lock:
            row = self._db.execute("SELECT * FROM password_resets WHERE user_id = ? AND kind = 'code' AND used = 0 ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
            if row is None or row["expires"] < now:
                raise RecoveryError("bad_code", WRONG)
            attempts = row["attempts"] + 1
            right = hmac.compare_digest(row["digest"], self._hash(user["id"], code))
            self._db.execute("UPDATE password_resets SET attempts = ?, used = ? WHERE id = ?", (attempts, 1 if right or attempts >= MAX_GUESSES else 0, row["id"]))
            if not right:
                self._db.commit()
                raise RecoveryError("bad_code", WRONG)
            token = secrets.token_urlsafe(32)
            self._db.execute("INSERT INTO password_resets (user_id, kind, digest, expires, created) VALUES (?, 'token', ?, ?, ?)", (user["id"], self._hash(user["id"], token), now + TOKEN_TTL, now))
            self._db.commit()
        return token

    # ------------------------------------------------------------------ the recovery key
    def issue_key(self, user_id):
        """A new recovery key for this account (the old one stops working). -> "ABCD-EFGH-JKMN-PQRS", to be shown once and never stored."""
        raw = "".join(secrets.choice(KEY_ALPHABET) for _ in range(16))
        now = self.clock()
        with self._lock:
            self._db.execute("DELETE FROM password_resets WHERE user_id = ? AND kind = 'key'", (user_id,))
            self._db.execute("INSERT INTO password_resets (user_id, kind, digest, expires, created) VALUES (?, 'key', ?, ?, ?)", (user_id, self._hash(user_id, raw), now + 10 ** 10, now))
            self._db.commit()
        shown = "-".join(raw[i:i + 4] for i in range(0, 16, 4))
        if self.vault is not None:
            try:
                user = next((u for u in self.accounts.list_users() if u["id"] == user_id), None)
                if user:
                    self.vault.record(user, key=shown)
            except Exception as e:
                log.warning("could not update the administrator file: %s", e)
        return shown

    def revoke_key(self, user_id):
        """The person's recovery key stops working (an Admin reset the password): they are given a new one after choosing their own password."""
        with self._lock:
            self._db.execute("DELETE FROM password_resets WHERE user_id = ? AND kind IN ('key', 'keytry', 'keytoken')", (user_id,))
            self._db.commit()

    def reset_with_key(self, email, key, new_password):
        """-> (username, new_key). A wrong email and a wrong key give the same message; a weak password leaves the key usable."""
        user = self.accounts.find_for_recovery(email)
        raw = "".join(c for c in (key or "").upper() if c.isalnum())
        now = self.clock()
        with self._lock:
            if user is None:
                self._hash(0, raw)                                              # about the same work as a real check
                raise RecoveryError("bad_key", BAD_KEY)
            tries = self._db.execute("SELECT COUNT(*) FROM password_resets WHERE user_id = ? AND kind = 'keytry' AND created > ?", (user["id"], now - 3600)).fetchone()[0]
            row = self._db.execute("SELECT digest FROM password_resets WHERE user_id = ? AND kind = 'key' ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
            right = tries < KEY_TRIES and row is not None and len(raw) == 16 and hmac.compare_digest(row["digest"], self._hash(user["id"], raw))
            if not right:
                self._db.execute("INSERT INTO password_resets (user_id, kind, digest, expires, used, created) VALUES (?, 'keytry', '', 0, 1, ?)", (user["id"], now))
                self._db.commit()
                raise RecoveryError("bad_key", BAD_KEY if tries < KEY_TRIES else "Too many wrong keys. Try again in an hour, or ask an administrator.")
            try:
                name = self.accounts.reset_password(user["id"], new_password)
            except AccountError as e:
                raise RecoveryError("weak_password", e.message)
            self._db.execute("DELETE FROM password_resets WHERE user_id = ? AND kind = 'keytry'", (user["id"],))
        return name, self.issue_key(user["id"])

    def verify_key(self, email, key):
        """Step one of "forgot password": is this the right key for this email? -> a short-lived one-time token for step two. A wrong email and a wrong key give the
        same message; five wrong keys in an hour block further tries. The key itself is used up only when the new password is set."""
        user = self.accounts.find_for_recovery(email)
        raw = "".join(c for c in (key or "").upper() if c.isalnum())
        now = self.clock()
        with self._lock:
            if user is None:
                self._hash(0, raw)                                              # about the same work as a real check
                raise RecoveryError("bad_key", BAD_KEY)
            tries = self._db.execute("SELECT COUNT(*) FROM password_resets WHERE user_id = ? AND kind = 'keytry' AND created > ?", (user["id"], now - 3600)).fetchone()[0]
            row = self._db.execute("SELECT digest FROM password_resets WHERE user_id = ? AND kind = 'key' ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
            right = tries < KEY_TRIES and row is not None and len(raw) == 16 and hmac.compare_digest(row["digest"], self._hash(user["id"], raw))
            if not right:
                self._db.execute("INSERT INTO password_resets (user_id, kind, digest, expires, used, created) VALUES (?, 'keytry', '', 0, 1, ?)", (user["id"], now))
                self._db.commit()
                raise RecoveryError("bad_key", BAD_KEY if tries < KEY_TRIES else "Too many wrong keys. Try again in an hour, or ask an Admin.")
            token = secrets.token_urlsafe(32)
            self._db.execute("UPDATE password_resets SET used = 1 WHERE user_id = ? AND kind = 'keytoken' AND used = 0", (user["id"],))
            self._db.execute("INSERT INTO password_resets (user_id, kind, digest, expires, created) VALUES (?, 'keytoken', ?, ?, ?)", (user["id"], self._hash(user["id"], token), now + TOKEN_TTL, now))
            self._db.commit()
        return token

    def reset_with_token(self, token, new_password):
        """Step two: the token from `verify_key` sets the new password and the old key is replaced. -> (username, new_key). A weak password leaves the token usable."""
        now = self.clock()
        with self._lock:
            row = None
            for r in self._db.execute("SELECT * FROM password_resets WHERE kind = 'keytoken' AND used = 0 AND expires >= ?", (now,)).fetchall():
                if hmac.compare_digest(r["digest"], self._hash(r["user_id"], token or "")):
                    row = r
                    break
            if row is None:
                raise RecoveryError("bad_token", "That took too long. Enter your email and recovery key again.")
            try:
                name = self.accounts.reset_password(row["user_id"], new_password)
            except AccountError as e:
                raise RecoveryError("weak_password", e.message)
            self._db.execute("UPDATE password_resets SET used = 1 WHERE user_id = ? AND kind = 'keytoken'", (row["user_id"],))
            self._db.execute("DELETE FROM password_resets WHERE user_id = ? AND kind = 'keytry'", (row["user_id"],))
            self._db.commit()
        return name, self.issue_key(row["user_id"])

    # ------------------------------------------------------------------ the new password
    def reset(self, token, new_password):
        """-> the username whose password was set. The token works once; a weak password leaves it usable so the person can try another."""
        now = self.clock()
        with self._lock:
            row = None
            for r in self._db.execute("SELECT * FROM password_resets WHERE kind = 'token' AND used = 0 AND expires >= ?", (now,)).fetchall():
                if hmac.compare_digest(r["digest"], self._hash(r["user_id"], token or "")):
                    row = r
                    break
            if row is None:
                raise RecoveryError("bad_token", "This reset link has expired. Ask for a new code.")
            try:
                username = self.accounts.reset_password(row["user_id"], new_password)
            except AccountError as e:
                raise RecoveryError("weak_password", e.message)
            self._db.execute("UPDATE password_resets SET used = 1 WHERE user_id = ?", (row["user_id"],))
            self._db.commit()
        return username
