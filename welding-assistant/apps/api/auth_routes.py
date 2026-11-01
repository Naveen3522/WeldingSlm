"""Sign-in, sessions and user administration for the server. `handle` runs first on every request and either answers it
(sign-in routes, a refusal) or returns False so the normal routes continue.

    POST /api/auth/login            {email, password} -> sets the session cookie (the email is the sign-in; the username is only a display name)
    POST /api/auth/signup           {username, email, password} -> a new User, signed in, with a recovery key (shown once)
    GET  /api/auth/rules            the password, email and username rules (pattern and message) the pages show beside the fields
    POST /api/auth/logout           GET /api/auth/me     the signed-in user (401 when not signed in)
    POST /api/auth/change-password  {old, new}
    POST /api/auth/forgot           {identifier}            a code is emailed (the same answer whether or not the account exists)
    POST /api/auth/verify-code      {identifier, code}      -> {token}   the right code gives a one-time token
    POST /api/auth/recovery-key     {password}   a new recovery key for the signed-in person (the old one stops working)
    POST /api/auth/reset-key        {email, key, password} -> {recovery_key}   the recovery key sets a new password and is replaced by a fresh key
    POST /api/auth/check-key        {email, key} -> {token}   step one of "forgot password": the right key gives a ten-minute token
    POST /api/auth/reset-token      {token, password} -> {recovery_key}   step two: the token sets the new password; a fresh key is shown once
    POST /api/auth/reset            {token, password}       sets the new password and ends the person's sessions
    GET  /api/admin/mail            admin: whether recovery emails are sent or only written to the outbox
    GET  /api/users                 admin: every user and the roles   POST /api/users  {username, email, level} -> one-time password (valid 24 hours)
    POST /api/users/<id>/reset | disable | enable | unlock | delete      POST /api/users/<id>/update {username, email, role_id}
    GET  /api/roles                 admin: the roles and how many people hold each    POST /api/roles/save {roles: [{id, name, level}, ...]} all at once

The page and its css/js are public; every /api/ and /docs/ route needs a session, and a user who still has a one-time
password may only change it.
"""
import json
import os
import re
from http.cookies import SimpleCookie
from urllib.parse import urlparse

from welding_ai.accounts import levels, permissions, rules
from welding_ai.accounts.recovery import RecoveryError
from welding_ai.accounts.service import SESSION_SECONDS, AccountError
from welding_ai.core.log import get_logger

log = get_logger("auth")
COOKIE = "weld_session"
WHILE_CHANGING = ("/api/auth/me", "/api/auth/logout", "/api/auth/change-password")
USER_ACTION = re.compile(r"^/api/users/(\d+)/(reset|disable|enable|unlock|update|delete)$")
STATUS = {"bad_credentials": 401, "locked": 429, "disabled": 403, "not_found": 404, "exists": 409, "last_admin": 409, "self": 409, "expired": 403, "role_in_use": 409}


def _cookie(handler):
    jar = SimpleCookie()
    try:
        jar.load(handler.headers.get("Cookie", ""))
    except Exception:
        return None
    return jar[COOKIE].value if COOKIE in jar else None


def _set_cookie(handler, token):
    parts = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={SESSION_SECONDS if token else 0}"]
    if os.environ.get("WELD_HTTPS") == "1" or handler.headers.get("X-Forwarded-Proto") == "https":
        parts.append("Secure")
    return {"Set-Cookie": "; ".join(parts)}


# A browser form on another site cannot send a request that names a different Origin.
def _same_origin(handler):
    origin = handler.headers.get("Origin")
    return not origin or urlparse(origin).netloc == handler.headers.get("Host")


def _fail(handler, e):
    handler.send_json({"error": e.message, "code": e.code, **({"wait": e.wait} if getattr(e, "wait", 0) else {})}, STATUS.get(e.code, 400))


def handle(handler, accounts, method, path, body, recovery=None):
    token = _cookie(handler)
    user = accounts.session_user(token)
    handler.user = user

    if method == "POST" and not _same_origin(handler):
        handler.send_json({"error": "Request refused (wrong origin)."}, 403)
        return True

    if path.startswith("/api/auth/"):
        _auth(handler, accounts, method, path, body or {}, token, user, recovery)
        return True
    if path == "/api/admin/mail" and recovery is not None:
        if user is None or user["must_change"] or not user["is_admin"]:
            handler.send_json({"error": "Admins only."}, 403 if user else 401)
        else:
            handler.send_json(recovery.mailer.describe() | {"code_lifetime_seconds": recovery.ttl})
        return True
    if path == "/api/users" or path.startswith("/api/users/") or path in ("/api/roles", "/api/roles/save", "/api/permissions", "/api/permissions/save"):
        _users(handler, accounts, method, path, body or {}, user, recovery)
        return True

    if path.startswith("/api/") or path.startswith("/docs/"):
        if user is None:
            handler.send_json({"error": "Sign in to continue."}, 401)
            return True
        if user["must_change"]:
            handler.send_json({"error": "Choose a new password first.", "must_change": True}, 403)
            return True
    return False


def _auth(handler, accounts, method, path, body, token, user, recovery=None):
    if path == "/api/auth/me" and method == "GET":
        if user is None:
            return handler.send_json({"error": "Not signed in.", "setup": not accounts.has_users()}, 401)
        return handler.send_json({"user": user, "permissions": permissions.current().resolve(user["level"])})

    if path == "/api/auth/login" and method == "POST":
        try:
            new_token, who = accounts.login(str(body.get("email", body.get("username", ""))), str(body.get("password", "")))
        except AccountError as e:
            log.info("login failed email=%s (%s)", str(body.get("email", body.get("username", "")))[:40], e.code)
            return _fail(handler, e)
        log.info("login ok user=%s", who["username"])
        return handler.send_bytes(200, json.dumps({"user": who}).encode(), "application/json; charset=utf-8", _set_cookie(handler, new_token))

    if path == "/api/auth/logout" and method == "POST":
        accounts.logout(token)
        return handler.send_bytes(200, b'{"ok": true}', "application/json; charset=utf-8", _set_cookie(handler, ""))

    if path == "/api/auth/rules" and method == "GET":
        return handler.send_json({"rules": rules.current().describe()})

    if path == "/api/auth/signup" and method == "POST":
        if recovery is None:
            return handler.send_json({"error": "Sign-up is not available."}, 400)
        try:
            created = accounts.register(str(body.get("username", "")), str(body.get("email", "")), str(body.get("password", "")))
        except AccountError as e:
            return _fail(handler, e)
        log.info("account created by sign-up email=%s", created["email"])
        new_token = accounts.start_session(created["id"])
        key = recovery.issue_key(created["id"])                                  # shown once, on the screen after sign-up
        return handler.send_bytes(201, json.dumps({"user": created, "recovery_key": key}).encode(), "application/json; charset=utf-8", _set_cookie(handler, new_token))

    if path == "/api/auth/change-password" and method == "POST":
        if user is None:
            return handler.send_json({"error": "Sign in to continue."}, 401)
        try:
            accounts.change_password(user["id"], str(body.get("old", "")), str(body.get("new", "")), keep_token=token)
        except AccountError as e:
            return _fail(handler, e)
        log.info("password changed user=%s", user["username"])
        reply = {"user": accounts.session_user(token)}
        if recovery is not None:
            reply["recovery_key"] = recovery.issue_key(user["id"])              # shown once, on the screen after the change
        return handler.send_json(reply)

    if path == "/api/auth/recovery-key" and method == "POST":
        if user is None:
            return handler.send_json({"error": "Sign in to continue."}, 401)
        if recovery is None or not accounts.check_password(user["id"], str(body.get("password", ""))):
            return handler.send_json({"error": "The password is wrong."}, 400)
        log.info("recovery key made user=%s", user["username"])
        return handler.send_json({"recovery_key": recovery.issue_key(user["id"])})

    if recovery is not None and method == "POST" and path in ("/api/auth/forgot", "/api/auth/verify-code", "/api/auth/reset", "/api/auth/reset-key", "/api/auth/check-key", "/api/auth/reset-token"):
        return _recover(handler, recovery, path, body)

    handler.send_bytes(404, b"not found", "text/plain")


# Forgotten password: public routes (the person cannot sign in), so each answers the same whatever the account, and says nothing that helps a guess.
def _recover(handler, recovery, path, body):
    try:
        if path == "/api/auth/reset-key":
            username, key = recovery.reset_with_key(str(body.get("email", body.get("username", ""))), str(body.get("key", "")), str(body.get("password", "")))
            log.info("password reset by recovery key user=%s", username)
            return handler.send_json({"ok": True, "recovery_key": key})
        if path == "/api/auth/check-key":
            return handler.send_json({"token": recovery.verify_key(str(body.get("email", "")), str(body.get("key", "")))})
        if path == "/api/auth/reset-token":
            username, key = recovery.reset_with_token(str(body.get("token", "")), str(body.get("password", "")))
            log.info("password reset by recovery key (two steps) user=%s", username)
            return handler.send_json({"ok": True, "recovery_key": key})
        if path == "/api/auth/forgot":
            return handler.send_json({"ok": True, **recovery.request(str(body.get("identifier", "")))})
        if path == "/api/auth/verify-code":
            return handler.send_json({"token": recovery.verify(str(body.get("identifier", "")), str(body.get("code", "")))})
        username = recovery.reset(str(body.get("token", "")), str(body.get("password", "")))
        log.info("password reset by email code user=%s", username)
        return handler.send_json({"ok": True})
    except RecoveryError as e:
        return handler.send_json({"error": e.message, "code": e.code}, 400)


def _users(handler, accounts, method, path, body, user, recovery=None):
    if user is None:
        return handler.send_json({"error": "Sign in to continue."}, 401)
    if user["must_change"]:
        return handler.send_json({"error": "Choose a new password first.", "must_change": True}, 403)
    def need():
        """True for an Admin; otherwise the refusal is already sent. Everything under /api/users, /api/roles and /api/permissions is for Admins alone."""
        if user["is_admin"]:
            return True
        handler.send_json({"error": "Admins only."}, 403)
        return False

    try:
        if path == "/api/permissions" and method == "GET":
            if not need():
                return
            return handler.send_json({"screens": permissions.current().levels_tree(), "problem": permissions.current().problem})
        if path == "/api/permissions/save" and method == "POST":
            if not need():
                return
            try:
                saved = permissions.current().save(body.get("screens") or [])
            except permissions.PermissionsError as e:
                return handler.send_json({"error": str(e), "code": "bad_permissions"}, 400)
            log.info("permissions saved by %s", user["username"])
            return handler.send_json({"screens": saved})
        if not need():
            return
        if path == "/api/users" and method == "GET":
            return handler.send_json({"users": accounts.list_users(), "roles": accounts.list_roles(), "default_level": levels.DEFAULT_LEVEL})
        if path == "/api/roles" and method == "GET":
            return handler.send_json({"roles": accounts.list_roles()})
        if path == "/api/roles/save" and method == "POST":
            if not need():
                return
            saved = accounts.save_roles(body.get("roles"))
            log.info("roles saved by %s: %s", user["username"], ", ".join(f'{r["name"]} {r["level"]}' for r in saved))
            return handler.send_json({"roles": saved})
        if path == "/api/users" and method == "POST":
            created, temp = accounts.create_user(str(body.get("username", "")), str(body.get("email", "")), level=body.get("level", levels.DEFAULT_LEVEL))
            log.info("user created %s by %s", created["username"], user["username"])
            return handler.send_json({"user": created, "temp_password": temp}, 201)

        m = USER_ACTION.match(path)
        if m and method == "POST":
            uid, action = int(m.group(1)), m.group(2)
            result = {"ok": True}
            if not need():
                return
            if action == "reset":
                result["temp_password"] = accounts.admin_reset(uid)
                if recovery is not None:
                    recovery.revoke_key(uid)                          # the old recovery key stops working; a new one is shown to the person after their forced change
            elif action in ("disable", "enable"):
                accounts.set_active(uid, action == "enable")
            elif action == "unlock":
                accounts.unlock(uid)
            elif action == "update":
                result["user"] = accounts.update_user(uid, str(body.get("username", "")), str(body.get("email", "")), body.get("role_id"))
            else:
                accounts.delete_user(uid, user["id"])
            log.info("user %s: %s by %s", uid, action, user["username"])
            return handler.send_json(result)
    except AccountError as e:
        return _fail(handler, e)
    handler.send_bytes(404, b"not found", "text/plain")
