"""Answers written by hand where the documents have none.

A Weld Engineer or Admin who sees "Not in the documents" can write the answer. From then on the same question gets that answer for everyone, marked as added by hand
("Added manually. Not from the documents. It may not be correct."). The answer is not a question pair for training: a pair needs a passage of a document to stand on.

    ManualAnswers(path).add(question, answer, area, by_name, by_email)   -> the saved answer
    ManualAnswers.claim(ctx)  -> the events of the answer when the question is one that was answered by hand, else None (a provider the pipeline asks, like the calculator)
"""
import re
import sqlite3
import threading
import time

from welding_ai.core import events

AREAS = ("General", "Fault Finder", "Procedure Advisor", "Formulas")
QUESTION_MIN, QUESTION_MAX, ANSWER_MAX = 5, 300, 1500
SIMILAR = 0.85                     # how alike two questions' words must be (shared words over all words) for the same answer to be given
SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_answers (
    id        INTEGER PRIMARY KEY,
    question  TEXT NOT NULL,
    key       TEXT NOT NULL,
    answer    TEXT NOT NULL,
    area      TEXT NOT NULL,
    by_name   TEXT NOT NULL DEFAULT '',
    by_email  TEXT NOT NULL DEFAULT '',
    created   REAL NOT NULL
)"""
_WORDS = re.compile(r"[a-z0-9]+")
_FILLER = frozenset({"the", "a", "an", "is", "are", "of", "for", "to", "in", "on", "and", "do", "does", "what", "how", "can", "i", "my", "me", "please", "tell", "about"})


class ManualError(Exception):
    def __init__(self, message, code="bad"):
        super().__init__(message)
        self.message, self.code = message, code


def words(text):
    return frozenset(w for w in _WORDS.findall(text.lower()) if len(w) > 1 and w not in _FILLER)


def alike(a, b):
    return bool(a) and bool(b) and len(a & b) / len(a | b) >= SIMILAR


class ManualAnswers:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self._db.row_factory = sqlite3.Row
        self._db.execute(SCHEMA)

    def listing(self):
        with self._lock:
            rows = self._db.execute("SELECT id, question, answer, area, by_name, by_email, created FROM manual_answers ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

    def _find(self, question):
        key = words(question)
        with self._lock:
            rows = self._db.execute("SELECT * FROM manual_answers ORDER BY id DESC").fetchall()
        return next((dict(r) for r in rows if alike(key, frozenset(r["key"].split()))), None)

    def add(self, question, answer, area="General", by_name="", by_email=""):
        question, answer = question.strip(), answer.strip()
        if not QUESTION_MIN <= len(question) <= QUESTION_MAX:
            raise ManualError(f"The question must be {QUESTION_MIN} to {QUESTION_MAX} characters.")
        if not answer or len(answer) > ANSWER_MAX:
            raise ManualError(f"Write the answer, up to {ANSWER_MAX} characters.")
        if area not in AREAS:
            raise ManualError("Choose one of: " + ", ".join(AREAS) + ".")
        if not words(question):
            raise ManualError("Write a real question.")
        if self._find(question):
            raise ManualError("There is already an answer for this question. Remove it first to write another.", "exists")
        with self._lock:
            cur = self._db.execute("INSERT INTO manual_answers (question, key, answer, area, by_name, by_email, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                   (question, " ".join(sorted(words(question))), answer, area, by_name, by_email, self.clock()))
            self._db.commit()
        return {"id": cur.lastrowid, "question": question, "answer": answer, "area": area}

    def remove(self, answer_id):
        with self._lock:
            cur = self._db.execute("DELETE FROM manual_answers WHERE id = ?", (int(answer_id),))
            self._db.commit()
            return cur.rowcount > 0

    # ---- the pipeline's provider
    def claim(self, ctx):
        if ctx.nested:
            return None
        found = self._find(ctx.q)
        if found is None:
            return None
        return [events.no_sources("manual", ctx.notes, match="none"), events.token(found["answer"]),
                ctx.done(route="manual", grounded=False, confidence="n/a", match="none", answer_source="manual", manual_area=found["area"])]
