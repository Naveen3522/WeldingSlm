"""Formula storage (SQLite, the same file as the accounts) and the rules about who may change what.

    status  documents  transcribed from the source documents: read-only here (change the document, then refresh)
            admin      added by an administrator: can be edited or removed
            suggested  proposed by an engineer: hidden from everyone but administrators until approved
Removing is a soft delete (active = 0): nothing is ever lost, and every change is kept in the history.
"""
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from welding_ai.formulas import simple, validate

SLUG = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
RESERVED = {"ask", "suggest", "new", "locate"}                      # words the web routes use after /api/formulas/

SCHEMA = """
CREATE TABLE IF NOT EXISTS formulas (
    id         TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    version    INTEGER NOT NULL DEFAULT 1,
    body       TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS formula_history (
    id         INTEGER PRIMARY KEY,
    formula_id TEXT NOT NULL,
    version    INTEGER NOT NULL,
    action     TEXT NOT NULL,
    status     TEXT NOT NULL,
    active     INTEGER NOT NULL,
    body       TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    changed    REAL NOT NULL
);
"""


class FormulaError(Exception):
    def __init__(self, code, message, problems=None):
        super().__init__(message)
        self.code, self.message, self.problems = code, message, problems or []


class FormulaStore:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self._lock = threading.RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    # ------------------------------------------------------------------ reading
    @staticmethod
    def _full(row):
        return {"id": row["id"], "status": row["status"], "active": bool(row["active"]), "version": row["version"],
                "updated": row["updated"], "updated_by": row["updated_by"], **json.loads(row["body"])}

    def get(self, formula_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM formulas WHERE id = ?", (formula_id,)).fetchone()
        if row is None:
            raise FormulaError("not_found", "No such formula.")
        return self._full(row)

    # Formulas people may use and see: from the documents or approved by an administrator, and not removed.
    def usable(self):
        with self._lock:
            rows = self._db.execute("SELECT * FROM formulas WHERE active = 1 AND status IN ('documents', 'admin') ORDER BY id").fetchall()
        return [self._full(r) for r in rows]

    # Everything an administrator can manage, including suggestions and removed ones.
    def everything(self):
        with self._lock:
            rows = self._db.execute("SELECT * FROM formulas ORDER BY id").fetchall()
        return [self._full(r) for r in rows]

    def history(self, formula_id):
        with self._lock:
            rows = self._db.execute("SELECT version, action, status, active, changed_by, changed FROM formula_history WHERE formula_id = ? ORDER BY id DESC",
                                    (formula_id,)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def summary(f):
        return {"id": f["id"], "name": f["name"], "group": f["group"], "status": f["status"], "active": f["active"], "version": f["version"],
                "summary": f["summary"], "display": f["display"], "outputs": [{"symbol": o["symbol"], "name": o["name"], "unit": o["unit"]} for o in f["outputs"]],
                "source": f["source"], "updated": f["updated"], "updated_by": f["updated_by"]}

    # ------------------------------------------------------------------ writing
    def _record(self, formula_id, version, action, status, active, body, user):
        self._db.execute("INSERT INTO formula_history (formula_id, version, action, status, active, body, changed_by, changed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (formula_id, version, action, status, 1 if active else 0, json.dumps(body, ensure_ascii=False), user, self.clock()))

    def _checked(self, body):
        needs_example = not simple.is_simple(body)             # the simple form has no worked example: its symbols and expression are the check
        body = validate.clean(simple.expand(body))
        found = validate.problems(body, require_example=needs_example)
        if found:
            raise FormulaError("invalid", "This formula cannot be saved yet.", found)
        return body

    def _insert(self, formula_id, body, status, user, action):
        needs_example = not simple.is_simple(body)
        if simple.is_simple(body) and not formula_id:
            with self._lock:
                formula_id = simple.make_id(body, {r["id"] for r in self._db.execute("SELECT id FROM formulas")})
        body = validate.clean(simple.expand(body))
        found = validate.problems(body, require_example=needs_example)
        if not SLUG.match(formula_id or "") or formula_id in RESERVED:
            message = "The id must be 3 to 41 characters: lowercase letters, digits and underscores, starting with a letter."
            if not found:
                raise FormulaError("bad_id", message)
            found = [message] + found                          # tell the person everything that is wrong in one go
        if found:
            raise FormulaError("invalid", "This formula cannot be saved yet.", found)
        with self._lock:
            if self._db.execute("SELECT 1 FROM formulas WHERE id = ?", (formula_id,)).fetchone():
                raise FormulaError("exists", "A formula with this id already exists.")
            self._db.execute("INSERT INTO formulas (id, status, active, version, body, updated_by, updated) VALUES (?, ?, 1, 1, ?, ?, ?)",
                             (formula_id, status, json.dumps(body, ensure_ascii=False), user, self.clock()))
            self._record(formula_id, 1, action, status, True, body, user)
            self._db.commit()
        return self.get(formula_id)

    def create(self, formula_id, body, user):
        return self._insert(formula_id, body, "admin", user, "created")

    def suggest(self, formula_id, body, user):
        return self._insert(formula_id, body, "suggested", user, "suggested")

    def _editable(self, formula_id):
        f = self.get(formula_id)
        if f["status"] == "documents":
            raise FormulaError("from_documents", "This formula comes from the documents, so it cannot be edited here. Change the document and refresh.")
        return f

    def update(self, formula_id, body, user):
        current = self._editable(formula_id)
        body = self._checked(body)
        with self._lock:
            version = current["version"] + 1
            self._db.execute("UPDATE formulas SET body = ?, version = ?, updated_by = ?, updated = ? WHERE id = ?",
                             (json.dumps(body, ensure_ascii=False), version, user, self.clock(), formula_id))
            self._record(formula_id, version, "edited", current["status"], current["active"], body, user)
            self._db.commit()
        return self.get(formula_id)

    def _set(self, formula_id, user, action, status=None, active=None, any_source=False):
        current = self.get(formula_id) if any_source else self._editable(formula_id)
        status = current["status"] if status is None else status
        active = current["active"] if active is None else active
        with self._lock:
            version = current["version"] + 1
            self._db.execute("UPDATE formulas SET status = ?, active = ?, version = ?, updated_by = ?, updated = ? WHERE id = ?",
                             (status, 1 if active else 0, version, user, self.clock(), formula_id))
            self._record(formula_id, version, action, status, active, validate.clean(current), user)
            self._db.commit()
        return self.get(formula_id)

    def delete(self, formula_id, user):
        """Takes the formula off the list. One read from the documents stays off after a reindex too (only restoring it brings it back); the documents are never touched."""
        return self._set(formula_id, user, "removed", active=False, any_source=True)

    def restore(self, formula_id, user):
        return self._set(formula_id, user, "restored", active=True, any_source=True)

    def approve(self, formula_id, user):
        if self.get(formula_id)["status"] != "suggested":
            raise FormulaError("not_suggested", "Only a suggested formula can be approved.")
        return self._set(formula_id, user, "approved", status="admin")

    # ------------------------------------------------------------------ the documents
    def sync_documents(self, entries, user="documents"):
        """Make the 'documents' formulas match the transcribed file. -> {"added", "updated", "retired", "invalid": {id: [problems]}}."""
        report = {"added": [], "updated": [], "retired": [], "invalid": {}}
        seen = set()
        for entry in entries:
            formula_id = entry.get("id", "")
            plain = simple.is_simple(entry)                    # a formula read from the documents' own lines has no worked example: its symbols and expression are the check
            body = validate.clean(simple.expand(entry))
            found = validate.problems(body, require_example=not plain)
            if found or not SLUG.match(formula_id):
                report["invalid"][formula_id or "?"] = found or ["The id is not valid."]
                continue
            seen.add(formula_id)
            with self._lock:
                row = self._db.execute("SELECT * FROM formulas WHERE id = ?", (formula_id,)).fetchone()
                if row is None:
                    self._db.execute("INSERT INTO formulas (id, status, active, version, body, updated_by, updated) VALUES (?, 'documents', 1, 1, ?, ?, ?)",
                                     (formula_id, json.dumps(body, ensure_ascii=False), user, self.clock()))
                    self._record(formula_id, 1, "from documents", "documents", True, body, user)
                    report["added"].append(formula_id)
                elif row["status"] != "documents":
                    report["invalid"][formula_id] = ["An administrator already added a formula with this id."]
                elif not row["active"] and row["updated_by"] != user:               # an administrator removed it: it stays removed, and only keeps its latest wording
                    if json.loads(row["body"]) != body:
                        self._db.execute("UPDATE formulas SET body = ? WHERE id = ?", (json.dumps(body, ensure_ascii=False), formula_id))
                elif json.loads(row["body"]) != body or not row["active"]:
                    version = row["version"] + 1
                    self._db.execute("UPDATE formulas SET body = ?, active = 1, version = ?, updated_by = ?, updated = ? WHERE id = ?",
                                     (json.dumps(body, ensure_ascii=False), version, user, self.clock(), formula_id))
                    self._record(formula_id, version, "updated from documents", "documents", True, body, user)
                    report["updated"].append(formula_id)
                self._db.commit()
        with self._lock:
            for row in self._db.execute("SELECT * FROM formulas WHERE status = 'documents' AND active = 1").fetchall():
                if row["id"] not in seen and row["id"] not in report["invalid"]:
                    self._db.execute("UPDATE formulas SET active = 0, version = ?, updated = ? WHERE id = ?", (row["version"] + 1, self.clock(), row["id"]))
                    self._record(row["id"], row["version"] + 1, "no longer in documents", "documents", False, json.loads(row["body"]), user)
                    report["retired"].append(row["id"])
            self._db.commit()
        return report
