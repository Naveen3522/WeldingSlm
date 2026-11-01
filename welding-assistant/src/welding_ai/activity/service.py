"""Who asked what, what they were told, and whether it helped: the per-person history, the feedback and the audit log (SQLite).

A person is identified by their account email (usernames may be shared by several people); the username is kept beside it as the display name, and `area` says
what answered (General, Fault Finder, Procedure Advisor, Formulas, WPS Builder). Rows written before emails existed carry only the username.

    record · history · clear_history · add_feedback · audit_page · export_rows

The audit log is never edited. A person's "Clear history" only hides their own questions from their History menu. Only an Admin (right 11.2) can delete entries, with `clear`,
and only the entries a choice names: older than some days, or everything the filters match, or everything on purpose.
"""
import sqlite3
import threading
import time
from pathlib import Path

HISTORY_SHOWN = 40
MAX_QUESTION, MAX_ANSWER, MAX_COMMENT = 1000, 8000, 600
PAGE = 50

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_log (
    id        INTEGER PRIMARY KEY,
    username  TEXT NOT NULL,
    email     TEXT NOT NULL DEFAULT '',
    area      TEXT NOT NULL DEFAULT '',
    asked     REAL NOT NULL,
    question  TEXT NOT NULL,
    answer    TEXT NOT NULL,
    route     TEXT NOT NULL,
    label     TEXT NOT NULL,
    mode      TEXT NOT NULL,
    model     TEXT NOT NULL,
    match     TEXT NOT NULL,
    documents TEXT NOT NULL,
    ms        INTEGER NOT NULL,
    hidden    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS chat_log_user ON chat_log (username, id);
CREATE TABLE IF NOT EXISTS feedback (
    id       INTEGER PRIMARY KEY,
    log_id   INTEGER NOT NULL REFERENCES chat_log (id),
    username TEXT NOT NULL,
    score    INTEGER NOT NULL,
    comment  TEXT NOT NULL,
    at       REAL NOT NULL,
    UNIQUE (log_id, username)
);
"""


class ActivityError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class ActivityLog:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self._lock = threading.RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """A log made before emails and areas gets the two columns; where the accounts table is in the same file, each old row learns its person's email."""
        columns = {r["name"] for r in self._db.execute("PRAGMA table_info(chat_log)")}
        for name in ("email", "area"):
            if name not in columns:
                self._db.execute(f"ALTER TABLE chat_log ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        self._db.execute("CREATE INDEX IF NOT EXISTS chat_log_email ON chat_log (email, id)")
        has_users = self._db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()
        if has_users:
            self._db.execute("UPDATE chat_log SET email = (SELECT u.email FROM users u WHERE u.username = chat_log.username COLLATE NOCASE LIMIT 1) "
                             "WHERE email = '' AND (SELECT COUNT(*) FROM users u WHERE u.username = chat_log.username COLLATE NOCASE) = 1")
        self._db.commit()

    # ------------------------------------------------------------------ writing
    def record(self, username, question, answer, route="", label="", mode="", model="", match="", documents=(), ms=0, email="", area=""):
        """-> the id of the new log row (the page sends it back with the feedback)."""
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO chat_log (username, email, area, asked, question, answer, route, label, mode, model, match, documents, ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (username, str(email or "").strip(), str(area or ""), self.clock(), str(question)[:MAX_QUESTION], str(answer)[:MAX_ANSWER], str(route or ""), str(label or ""), str(mode or ""),
                 str(model or ""), str(match or ""), " | ".join(dict.fromkeys(documents))[:600], int(ms)))
            self._db.commit()
            return cur.lastrowid

    def add_feedback(self, username, log_id, score, comment="", email=""):
        """One verdict per person per answer (a second one replaces the first). Only your own answers."""
        if score not in (1, -1):
            raise ActivityError("bad_score", "The score must be 1 (helpful) or -1 (not helpful).")
        with self._lock:
            row = self._db.execute("SELECT username, email FROM chat_log WHERE id = ?", (log_id,)).fetchone()
            if row is None or not self._is_theirs(row, username, email):
                raise ActivityError("not_found", "No such answer.")
            self._db.execute("INSERT INTO feedback (log_id, username, score, comment, at) VALUES (?, ?, ?, ?, ?) "
                             "ON CONFLICT (log_id, username) DO UPDATE SET score = excluded.score, comment = excluded.comment, at = excluded.at",
                             (log_id, username, score, str(comment or "").strip()[:MAX_COMMENT], self.clock()))
            self._db.commit()

    @staticmethod
    def _is_theirs(row, username, email):
        """A row belongs to the person with this email; a row written before emails existed belongs to the person with that username."""
        if email and row["email"]:
            return row["email"].lower() == email.lower()
        return row["username"] == username

    def clear_history(self, username, email=""):
        """Hides this person's questions from their History menu. The audit log keeps them."""
        with self._lock:
            self._db.execute("UPDATE chat_log SET hidden = 1 WHERE " + self._mine, self._mine_args(username, email))
            self._db.commit()

    # ------------------------------------------------------------------ reading
    _mine = "((email != '' AND email = ? COLLATE NOCASE) OR (email = '' AND username = ?))"

    @staticmethod
    def _mine_args(username, email):
        return (str(email or "").strip() or "\0", username)

    def history_items(self, username, email="", area=""):
        """The person's recent distinct questions, newest first, each with the time it was last asked (only those answered by `area` when one is named)."""
        with self._lock:
            rows = self._db.execute("SELECT question, MAX(id) AS last, MAX(asked) AS asked FROM chat_log WHERE " + self._mine + " AND hidden = 0" + (" AND area = ?" if area else "") +
                                    " GROUP BY question ORDER BY last DESC LIMIT ?",
                                    (*self._mine_args(username, email), *((area,) if area else ()), HISTORY_SHOWN)).fetchall()
        return [{"question": r["question"], "asked": r["asked"]} for r in rows]

    def history(self, username, email="", area=""):
        """The same questions as plain text."""
        return [item["question"] for item in self.history_items(username, email, area)]

    @staticmethod
    def _where(user, text, verdict, area=""):
        where, args = ["1 = 1"], []
        if user:
            where.append("(c.email = ? COLLATE NOCASE OR c.username = ? COLLATE NOCASE)")
            args += [user, user]
        if area:
            where.append("c.area = ?")
            args.append(area)
        if text:
            where.append("(c.question LIKE ? ESCAPE '\\' OR c.answer LIKE ? ESCAPE '\\')")
            like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            args += [like, like]
        if verdict == "bad":
            where.append("f.score = -1")
        elif verdict == "good":
            where.append("f.score = 1")
        elif verdict == "none":
            where.append("f.score IS NULL")
        return " AND ".join(where), args

    _SELECT = ("SELECT c.id, c.username, c.email, c.area, c.asked, c.question, c.answer, c.route, c.label, c.mode, c.model, c.match, c.documents, c.ms, "
               "f.score AS score, f.comment AS comment FROM chat_log c LEFT JOIN feedback f ON f.log_id = c.id AND f.username = c.username")

    def audit_page(self, offset=0, limit=PAGE, user="", text="", verdict="", area=""):
        """-> {"rows": [...newest first], "total": n}"""
        where, args = self._where(user, text, verdict, area)
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM chat_log c LEFT JOIN feedback f ON f.log_id = c.id AND f.username = c.username WHERE {where}", args).fetchone()[0]
            rows = self._db.execute(f"{self._SELECT} WHERE {where} ORDER BY c.id DESC LIMIT ? OFFSET ?", args + [max(1, min(int(limit), 200)), max(0, int(offset))]).fetchall()
        return {"rows": [dict(r) for r in rows], "total": total}

    def export_rows(self, user="", text="", verdict="", area=""):
        where, args = self._where(user, text, verdict, area)
        with self._lock:
            rows = self._db.execute(f"{self._SELECT} WHERE {where} ORDER BY c.id", args).fetchall()
        return [dict(r) for r in rows]

    def clear(self, user="", text="", verdict="", area="", older_than_days=None, everything=False, dry_run=False):
        """Deletes audit entries (and the feedback on them): those older than `older_than_days`, and/or those the filters match. With no age and no filter nothing is deleted
        unless `everything` is True. -> how many (or how many would be, with `dry_run`)."""
        filtered = any((user, text, verdict, area))
        if older_than_days is None and not filtered and not everything:
            return 0
        where, args = self._where(user, text, verdict, area)
        if older_than_days is not None:
            where += " AND c.asked < ?"
            args.append(self.clock() - max(0, float(older_than_days)) * 86400)
        with self._lock:
            ids = [r[0] for r in self._db.execute(f"SELECT c.id FROM chat_log c LEFT JOIN feedback f ON f.log_id = c.id AND f.username = c.username WHERE {where}", args)]
            if dry_run:
                return len(ids)
            for start in range(0, len(ids), 500):
                part = ids[start:start + 500]
                marks = ",".join("?" * len(part))
                self._db.execute(f"DELETE FROM feedback WHERE log_id IN ({marks})", part)
                self._db.execute(f"DELETE FROM chat_log WHERE id IN ({marks})", part)
            self._db.commit()
        return len(ids)

    def usage(self):
        """-> {"questions", "people", "helpful", "not_helpful"} for the admin's summary line."""
        with self._lock:
            q = self._db.execute("SELECT COUNT(*), COUNT(DISTINCT CASE WHEN email != '' THEN lower(email) ELSE username END) FROM chat_log").fetchone()
            f = self._db.execute("SELECT COALESCE(SUM(score = 1), 0), COALESCE(SUM(score = -1), 0) FROM feedback").fetchone()
        return {"questions": q[0], "people": q[1], "helpful": f[0], "not_helpful": f[1]}


CSV_COLUMNS = [("id", "ID"), ("asked", "Time"), ("username", "User"), ("email", "Email"), ("area", "Answered by"), ("question", "Question"), ("answer", "Answer"), ("label", "Answer type"),
               ("route", "Route"), ("mode", "Mode"), ("model", "Model"), ("match", "Match"), ("documents", "Documents"), ("ms", "Milliseconds"),
               ("score", "Feedback"), ("comment", "Comment")]


# A spreadsheet runs a cell that starts with = + - @ as a formula: neutralise it so an exported question cannot do that.
def safe_cell(value):
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_xlsx(rows):
    """The audit rows as an Excel (.xlsx) file, one sheet, the titles in bold on the first row."""
    import datetime
    from welding_ai.core import xlsx
    lines = [[title for _, title in CSV_COLUMNS]]
    for r in rows:
        r = dict(r, asked=datetime.datetime.fromtimestamp(r["asked"]).strftime("%Y-%m-%d %H:%M:%S"), score={1: "helpful", -1: "not helpful"}.get(r["score"], ""))
        lines.append([safe_cell(r.get(key)) for key, _ in CSV_COLUMNS])
    return xlsx.write(lines, [8, 20, 16, 24, 16, 50, 70, 16, 16, 10, 16, 10, 30, 12, 12, 30])


def to_csv(rows):
    import csv
    import io
    import datetime
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([title for _, title in CSV_COLUMNS])
    for r in rows:
        r = dict(r, asked=datetime.datetime.fromtimestamp(r["asked"]).strftime("%Y-%m-%d %H:%M:%S"), score={1: "helpful", -1: "not helpful"}.get(r["score"], ""))
        writer.writerow([safe_cell(r.get(key)) for key, _ in CSV_COLUMNS])
    return "﻿" + buf.getvalue()           # the byte-order mark lets Excel read accents correctly
