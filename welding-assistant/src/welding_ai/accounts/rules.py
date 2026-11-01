"""The rules for passwords, email addresses and usernames, kept in a plain file an administrator can edit: data/settings/account-rules.json.

Each rule is a regular expression and the one-line message shown when something does not match it. The file is read again whenever it changes, so an edit
applies without a restart. A file that is not valid JSON, or holds a pattern that does not compile, is ignored (the built-in rules stay in force) and the reason
is kept in `Rules.problem` for the log. The server checks every sign-up, password change and reset with these rules, whatever the page says.

"Not equal to the username" cannot be written as a pattern (a pattern cannot see the username), so that one stays a check in the code.
"""
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("welding.accounts")

DEFAULTS = {
    "password": {
        # 8 to 64 characters, one uppercase letter, one number or symbol, and at least four different characters
        "pattern": r"^(?=.*[A-Z])(?=.*[0-9\W_])(?=.*?(.).*?(?!\1)(.).*?(?!\1|\2)(.).*?(?!\1|\2|\3)(.)).{8,64}$",
        "message": "Password needs 8 or more characters, one uppercase letter, and a number or symbol.",
    },
    "email": {
        "pattern": r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        "required": "Enter your email.",
        "message": "Email doesn't match the format name@example.com.",
    },
    "username": {
        "pattern": r"^[A-Za-z0-9._-]{3,32}$",
        "required": "Enter a username.",
        "message": "Username: 3 to 32 letters, digits, dot, dash or underscore.",
    },
}
MAX_EMAIL = 254


class Rules:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.problem = ""
        self._stamp = None
        self._rules = self._compile(DEFAULTS)
        self.reload()

    @staticmethod
    def _compile(source):
        return {name: {**spec, "regex": re.compile(spec["pattern"])} for name, spec in source.items()}

    def ensure_file(self):
        """Writes the built-in rules to the file when there is none, so there is something to edit."""
        if self.path and not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(DEFAULTS, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.reload()

    def reload(self):
        """Reads the file when it has changed; a broken file leaves the rules in force as they were."""
        if not self.path or not self.path.is_file():
            return
        stamp = self.path.stat().st_mtime_ns
        if stamp == self._stamp:
            return
        self._stamp = stamp
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            merged = {name: {**DEFAULTS[name], **(loaded.get(name) or {})} for name in DEFAULTS}
            self._rules = self._compile(merged)
            self.problem = ""
        except (OSError, ValueError, re.error, TypeError, AttributeError) as e:
            self.problem = f"{self.path.name} was not used: {e}"
            log.warning(self.problem)

    def _spec(self, name):
        self.reload()
        return self._rules[name]

    # ------------------------------------------------------------------ the checks: each returns the message to show, or None
    def password_problem(self, password, username=""):
        spec = self._spec("password")
        if not spec["regex"].match(password or ""):
            return spec["message"]
        if username and password.lower() == username.lower():
            return "The password must not be your username."
        return None

    def email_problem(self, email):
        spec = self._spec("email")
        email = (email or "").strip()
        if not email:
            return spec["required"]
        if len(email) > MAX_EMAIL or not spec["regex"].match(email):
            return spec["message"]
        return None

    def username_problem(self, username):
        spec = self._spec("username")
        username = (username or "").strip()
        if not username:
            return spec["required"]
        if not spec["regex"].match(username):
            return spec["message"]
        return None

    def describe(self):
        """What the pages show beside the fields (the same text the server answers with)."""
        return {name: {k: v for k, v in spec.items() if k != "regex"} for name, spec in self._spec_all().items()}

    def _spec_all(self):
        self.reload()
        return self._rules


_current = None


def current():
    """The rules in force for this server: the file under paths.SETTINGS_DIR, or the built-in ones when there is no file."""
    global _current
    if _current is None:
        from welding_ai import paths
        _current = Rules(paths.SETTINGS_DIR / "account-rules.json")
    return _current
