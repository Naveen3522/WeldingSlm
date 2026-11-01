"""The question-and-answer pairs waiting for, or past, an administrator's review (SQLite).

Pairs made from a table are quoted word for word, so by default they are approved when created; an administrator can still reject any of them,
and a rejected pair is never trained on. Only approved pairs go into a dataset.
"""
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_candidates (
    id         INTEGER PRIMARY KEY,
    uid        TEXT NOT NULL UNIQUE,
    doc        TEXT NOT NULL,
    section    TEXT NOT NULL,
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    evidence   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    status     TEXT NOT NULL,
    created    REAL NOT NULL,
    decided_by TEXT NOT NULL DEFAULT '',
    decided_at REAL,
    used_in    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS qa_status ON qa_candidates (status, doc);
"""
STATUSES = ("pending", "approved", "rejected", "changes")      # changes: an administrator asked the person who sent it to correct it and send it again
NEW_COLUMNS = (("added_by", "TEXT NOT NULL DEFAULT ''"), ("note", "TEXT NOT NULL DEFAULT ''"), ("refs", "TEXT NOT NULL DEFAULT ''"), ("area", "TEXT NOT NULL DEFAULT ''"),
               ("backed", "INTEGER NOT NULL DEFAULT -1"),       # is the answer in the documents: 1 yes, 0 no, -1 not checked yet
               ("backed_how", "TEXT NOT NULL DEFAULT ''"),      # exact | meaning | none
               ("off_topic", "INTEGER NOT NULL DEFAULT 0"),     # the question has no welding topic
               ("tested", "INTEGER NOT NULL DEFAULT 0"),        # Test selected pairs ran on it: 0 not yet, 1 passed, 2 failed
               ("placement", "TEXT NOT NULL DEFAULT ''"),
               ("draft", "TEXT NOT NULL DEFAULT ''"))          # JSON: the draft cell a pair was made from (id, name, template, field, column), for the administrator to see       # "learned": an administrator keeps it out of training
SETTING = "qa_auto_approve"
PERSON = ("manual", "suggested")                                   # pairs added by people; every other kind is made from a document table

# The question pairs people add (and an administrator approves) are the learned pairs: they answer at question time through the lookup and are never trained on.
# The SLM is trained on the training set: pairs generated from the documents, and rows an administrator adds to it directly (kind "training", always quoted from a document).
TRAINABLE = "status = 'approved' AND kind NOT IN ('manual', 'suggested')"
LEARNED_ONLY = "status = 'approved' AND kind IN ('manual', 'suggested')"


class QaError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class QaQueue:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self._lock = threading.RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        have = {r["name"] for r in self._db.execute("PRAGMA table_info(qa_candidates)")}
        for name, kind in NEW_COLUMNS:                                    # a database made by an older version gets the new columns
            if name not in have:
                self._db.execute(f"ALTER TABLE qa_candidates ADD COLUMN {name} {kind}")
        self.listeners = []                                              # called with no arguments after any change (the learned-pairs file is rewritten from here)
        self._db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def _changed(self):
        for listener in self.listeners:
            try:
                listener()
            except Exception:
                pass                                                      # a failing listener never breaks a change

    # ------------------------------------------------------------------ the setting
    def auto_approve(self):
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key = ?", (SETTING,)).fetchone()
        return row is None or row["value"] == "1"                       # on unless an administrator turned it off

    def set_auto_approve(self, on):
        with self._lock:
            self._db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (SETTING, "1" if on else "0"))
            self._db.commit()

    def setting(self, key, default="1"):
        """Another on/off setting kept beside the approval one (for example "auto_retrain")."""
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else row["value"]

    def set_setting(self, key, value):
        with self._lock:
            self._db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, str(value)))
            self._db.commit()

    # ------------------------------------------------------------------ adding
    def add_many(self, pairs, user="system"):
        """Adds generated pairs, skipping any already in the queue (whatever its status). -> {"added", "skipped", "status"}."""
        status = "approved" if self.auto_approve() else "pending"
        now, added = self.clock(), 0
        with self._lock:
            for p in pairs:
                uid = hashlib.sha256("\x1f".join((p["doc"], p["question"], p["answer"])).encode()).hexdigest()[:32]
                cur = self._db.execute(
                    "INSERT OR IGNORE INTO qa_candidates (uid, doc, section, question, answer, evidence, kind, status, created, decided_by, decided_at, added_by, refs, area) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (uid, p["doc"], p["section"], p["question"], p["answer"], p["evidence"], p.get("kind", "table"), status, now,
                     (user if user != "system" else "automatic") if status == "approved" else "", now if status == "approved" else None,
                     "" if user == "system" else user, json.dumps(p["refs"], ensure_ascii=False) if p.get("refs") else "", p.get("area", "")))
                added += cur.rowcount
            self._db.commit()
        if added:
            self._changed()
        return {"added": added, "skipped": len(pairs) - added, "status": status}

    def suggest(self, pair, user):
        """A pair sent by a person who may not approve it: it waits. -> the id, or None when the same question and answer are already there."""
        uid = hashlib.sha256("\x1f".join((pair["doc"], pair["question"], pair["answer"])).encode()).hexdigest()[:32]
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO qa_candidates (uid, doc, section, question, answer, evidence, kind, status, created, added_by, refs, area, draft) "
                "VALUES (?, ?, ?, ?, ?, ?, 'suggested', 'pending', ?, ?, ?, ?, ?)",
                (uid, pair["doc"], pair.get("section", ""), pair["question"], pair["answer"], pair.get("evidence", ""), self.clock(), user,
                 json.dumps(pair["refs"], ensure_ascii=False) if pair.get("refs") else "", pair.get("area", ""),
                 json.dumps(pair["draft"], ensure_ascii=False) if pair.get("draft") else ""))
            self._db.commit()
            row = self._db.execute("SELECT id FROM qa_candidates WHERE uid = ?", (uid,)).fetchone()
        return row["id"] if cur.rowcount else None

    def mine(self, user):
        """Every pair this person sent, newest first."""
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM qa_candidates WHERE added_by = ? ORDER BY id DESC", (user,)).fetchall()]

    def set_kind(self, pair_id, kind):
        """A suggested pair that was approved becomes an ordinary person's pair ("manual"). Does not call the listeners: it is called from one."""
        with self._lock:
            self._db.execute("UPDATE qa_candidates SET kind = ? WHERE id = ?", (kind, int(pair_id)))
            self._db.commit()

    def get(self, pair_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM qa_candidates WHERE id = ?", (int(pair_id),)).fetchone()
        return dict(row) if row else None

    def resend(self, pair_id, user, question, answer, evidence="", refs=None):
        """The person corrects a pair an administrator asked them to change; it waits again. -> True when it was theirs and in 'changes'."""
        with self._lock:
            cur = self._db.execute("UPDATE qa_candidates SET question = ?, answer = ?, evidence = CASE WHEN ? = '' THEN evidence ELSE ? END, refs = CASE WHEN ? = '' THEN refs ELSE ? END, "
                                   "status = 'pending', note = '', tested = 0, backed = -1 WHERE id = ? AND added_by = ? AND status = 'changes'",
                                   (question, answer, evidence, evidence, json.dumps(refs, ensure_ascii=False) if refs else "", json.dumps(refs, ensure_ascii=False) if refs else "", int(pair_id), user))
            self._db.commit()
        if cur.rowcount:
            self._changed()
        return cur.rowcount > 0

    def edit_own(self, pair_id, user, question, answer):
        """The person changes their own pair while it is not approved (waiting, sent back, or rejected); it waits again. -> True when it was theirs and could be changed."""
        with self._lock:
            cur = self._db.execute("UPDATE qa_candidates SET question = ?, answer = ?, status = 'pending', note = '', tested = 0, backed = -1 "
                                   "WHERE id = ? AND added_by = ? AND status IN ('pending', 'changes', 'rejected')", (question, answer, int(pair_id), user))
            self._db.commit()
        if cur.rowcount:
            self._changed()
        return cur.rowcount > 0

    # ------------------------------------------------------------------ reading
    def counts(self):
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) AS n FROM qa_candidates GROUP BY status").fetchall()
            unused = self._db.execute("SELECT COUNT(*) FROM qa_candidates WHERE status = 'approved' AND used_in = ''").fetchone()[0]
        out = {s: 0 for s in STATUSES}
        out.update({r["status"]: r["n"] for r in rows})
        return {**out, "total": sum(out.values()), "unused": unused, "auto_approve": self.auto_approve()}

    def documents(self):
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT doc, COUNT(*) AS n FROM qa_candidates GROUP BY doc ORDER BY doc")]

    def page(self, status="", doc="", text="", offset=0, limit=50, origin="", goes=""):
        where, args = ["1 = 1"], []
        if status in STATUSES:
            where.append("status = ?")
            args.append(status)
        if doc:
            where.append("doc = ?")
            args.append(doc)
        if origin == "hand":
            where.append("kind IN ('manual', 'suggested')")
        elif origin == "generated":
            where.append("kind NOT IN ('manual', 'suggested')")
        if goes == "training":
            where.append(f"({TRAINABLE})")
        elif goes == "learned":
            where.append(f"({LEARNED_ONLY})")
        if text:
            like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(question LIKE ? ESCAPE '\\' OR answer LIKE ? ESCAPE '\\')")
            args += [like, like]
        clause = " AND ".join(where)
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM qa_candidates WHERE {clause}", args).fetchone()[0]
            rows = self._db.execute(f"SELECT * FROM qa_candidates WHERE {clause} ORDER BY id DESC LIMIT ? OFFSET ?", args + [max(1, min(int(limit), 200)), max(0, int(offset))]).fetchall()
        return {"rows": [dict(r) for r in rows], "total": total}

    def trainable(self, only_unused=False, ids=None):
        """The pairs a retrain may take: the training pairs. A fact that is not in the documents is never among them."""
        with self._lock:
            rows = self._db.execute(f"SELECT * FROM qa_candidates WHERE {TRAINABLE}" + (" AND used_in = ''" if only_unused else "") + " ORDER BY id").fetchall()
        wanted = None if ids is None else {int(i) for i in ids}
        return [dict(r) for r in rows if wanted is None or r["id"] in wanted]

    def learned_only(self):
        """The approved pairs people added that are not training pairs: they answer at question time and are never trained on."""
        with self._lock:
            return [dict(r) for r in self._db.execute(f"SELECT * FROM qa_candidates WHERE {LEARNED_ONLY} ORDER BY id").fetchall()]

    def set_backing(self, pair_id, backed, how, off_topic=False):
        with self._lock:
            self._db.execute("UPDATE qa_candidates SET backed = ?, backed_how = ?, off_topic = ? WHERE id = ?", (1 if backed else 0, how, 1 if off_topic else 0, int(pair_id)))
            self._db.commit()

    def set_tested(self, pair_id, passed):
        """Test selected pairs ran on this pair. A pair that failed counts as tested too: failing is why it may need training."""
        with self._lock:
            self._db.execute("UPDATE qa_candidates SET tested = ? WHERE id = ?", (1 if passed else 2, int(pair_id)))
            self._db.commit()
        self._changed()

    def set_placement(self, ids, placement):
        """"learned": keep these pairs out of training (an administrator's choice); "": let the rules decide. -> how many."""
        if placement not in ("", "learned"):
            raise QaError("The placement must be learned or empty.")
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        with self._lock:
            cur = self._db.execute(f"UPDATE qa_candidates SET placement = ? WHERE id IN ({','.join('?' * len(ids))})", [placement, *ids])
            self._db.commit()
        if cur.rowcount:
            self._changed()
        return cur.rowcount

    @staticmethod
    def goes_to(row):
        """Where an approved pair goes: "learned" (a person's pair: answers at question time, never trained) or "training" (generated, or added to the SLM directly)."""
        if row["status"] != "approved":
            return ""
        return "learned" if row["kind"] in PERSON else "training"

    def approved(self, only_unused=False, ids=None):
        """The approved pairs (the learned pairs). `only_unused`: those no training has taken yet. `ids`: only these (the ticked rows)."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM qa_candidates WHERE status = 'approved'" + (" AND used_in = ''" if only_unused else "") + " ORDER BY id").fetchall()
        wanted = None if ids is None else {int(i) for i in ids}
        return [dict(r) for r in rows if wanted is None or r["id"] in wanted]

    # ------------------------------------------------------------------ deciding
    def decide(self, ids, status, user, doc="", only_pending=False, note=""):
        """Approve, reject or ask for changes to the pairs with these ids (or, with `doc`, every pair of that document). `note` says why. -> how many changed."""
        if status not in ("approved", "rejected", "pending", "changes"):
            raise QaError("The status must be approved, rejected, pending or changes.")
        if status == "changes" and not str(note).strip():
            raise QaError("Say what the person should change.")
        note = str(note).strip()[:400]
        with self._lock:
            if doc:
                cur = self._db.execute("UPDATE qa_candidates SET status = ?, decided_by = ?, decided_at = ?, note = ? WHERE doc = ?" + (" AND status = 'pending'" if only_pending else ""),
                                       (status, user, self.clock(), note, doc))
            else:
                ids = [int(i) for i in ids]
                if not ids:
                    return 0
                marks = ",".join("?" * len(ids))
                cur = self._db.execute(f"UPDATE qa_candidates SET status = ?, decided_by = ?, decided_at = ?, note = ? WHERE id IN ({marks})", [status, user, self.clock(), note, *ids])
            self._db.commit()
        if cur.rowcount:
            self._changed()
        return cur.rowcount

    def edit(self, pair_id, question, answer, evidence):
        """Changes the wording of one pair (the page has already checked that the answer is in its document). -> True when the pair exists."""
        with self._lock:
            cur = self._db.execute("UPDATE qa_candidates SET question = ?, answer = ?, evidence = ?, tested = 0, backed = -1 WHERE id = ?", (question, answer, evidence, int(pair_id)))
            self._db.commit()
        if cur.rowcount:
            self._changed()
        return cur.rowcount > 0

    def retire(self, documents, reason="document changed"):
        """A document was changed or removed, so the pairs made from it may no longer be true: they are rejected and never trained on. -> how many."""
        documents = list(documents)
        if not documents:
            return 0
        marks = ",".join("?" * len(documents))
        with self._lock:
            cur = self._db.execute(f"UPDATE qa_candidates SET status = 'rejected', decided_by = ?, decided_at = ?, note = ? WHERE doc IN ({marks}) AND status != 'rejected'",
                                   [reason, self.clock(), reason, *documents])
            self._db.commit()
        if cur.rowcount:
            self._changed()
        return cur.rowcount

    def by_kind(self, kind):
        """The pairs of one kind ("manual" = written by hand), oldest first."""
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM qa_candidates WHERE kind = ? ORDER BY id", (kind,)).fetchall()]

    def delete(self, ids):
        """Removes pairs for good. -> how many."""
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        with self._lock:
            cur = self._db.execute(f"DELETE FROM qa_candidates WHERE id IN ({','.join('?' * len(ids))})", ids)
            self._db.commit()
        if cur.rowcount:
            self._changed()
        return cur.rowcount

    def delete_kind(self, kind):
        """Removes every pair of one kind (a retrain clears the ones written by hand). -> how many."""
        with self._lock:
            cur = self._db.execute("DELETE FROM qa_candidates WHERE kind = ?", (kind,))
            self._db.commit()
            return cur.rowcount

    def mark_used(self, ids, version):
        with self._lock:
            for i in ids:
                self._db.execute("UPDATE qa_candidates SET used_in = CASE WHEN used_in = '' THEN ? ELSE used_in || ',' || ? END WHERE id = ?", (version, version, i))
            self._db.commit()
