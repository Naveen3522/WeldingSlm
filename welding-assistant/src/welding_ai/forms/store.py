"""Templates and drafts (SQLite).

A template is a table: column titles and rows. The first column holds the field names; a cell that is empty is what a person fills in, a cell with text is fixed.
A draft is a person's copy of a template with their values in it. It is created the first time it is saved, never before. Everyone can see every draft.
Each draft keeps the template's rows as they were when it was made (`base`), so a later change to the template does not disturb it and so the fixed cells can be
checked on every save.
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

MAX_ROWS, MAX_COLS, MAX_CELL, MAX_NAME = 300, 12, 500, 120
PAGE = 25


class FormError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


SCHEMA = """
CREATE TABLE IF NOT EXISTS templates (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    heads    TEXT NOT NULL,
    rows     TEXT NOT NULL,
    by_name  TEXT NOT NULL DEFAULT '',
    by_email TEXT NOT NULL DEFAULT '',
    created  REAL NOT NULL,
    updated  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    template_id   INTEGER,
    template_name TEXT NOT NULL DEFAULT '',
    heads         TEXT NOT NULL,
    rows          TEXT NOT NULL,
    base          TEXT NOT NULL,
    by_name       TEXT NOT NULL DEFAULT '',
    by_email      TEXT NOT NULL DEFAULT '',
    created       REAL NOT NULL,
    updated       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS drafts_updated ON drafts (updated);
"""


def check_grid(heads, rows):
    """-> (heads, rows) cleaned, or raises FormError saying what is wrong. Every row needs a field name; field names are unique (case ignored)."""
    if not isinstance(heads, list) or not isinstance(rows, list):
        raise FormError("bad_grid", "Send the column titles and the rows.")
    heads = [str(h or "").strip() for h in heads]
    if not 2 <= len(heads) <= MAX_COLS:
        raise FormError("bad_grid", f"A table needs 2 to {MAX_COLS} columns.")
    if any(not h for h in heads):
        raise FormError("bad_grid", "Every column needs a title.")
    if len({h.lower() for h in heads}) != len(heads):
        raise FormError("bad_grid", "Two columns have the same title.")
    if not 1 <= len(rows) <= MAX_ROWS:
        raise FormError("bad_grid", f"A table needs 1 to {MAX_ROWS} rows.")
    clean, seen = [], set()
    for line in rows:
        if not isinstance(line, list) or len(line) != len(heads):
            raise FormError("bad_grid", "Every row must have a cell for every column.")
        cells = [str(c or "").strip() for c in line]
        if any(len(c) > MAX_CELL for c in cells):
            raise FormError("bad_grid", f"A cell can hold at most {MAX_CELL} characters.")
        if not cells[0]:
            raise FormError("bad_grid", "Every row needs a field name in the first column.")
        key = cells[0].lower()
        if key in seen:
            raise FormError("bad_grid", f'The field name "{cells[0]}" is used twice.')
        seen.add(key)
        clean.append(cells)
    return heads, clean


def _name(value):
    name = str(value or "").strip()
    if not name:
        raise FormError("bad_name", "Give it a name.")
    if len(name) > MAX_NAME:
        raise FormError("bad_name", f"A name can have at most {MAX_NAME} characters.")
    return name


def progress(rows, base):
    """-> (filled, total): the cells that were empty in the template, and how many of them now hold something. Fixed cells do not count."""
    total = filled = 0
    for line, original in zip(rows, base):
        for cell, was in zip(line[1:], original[1:]):
            if not was:
                total += 1
                filled += 1 if cell else 0
    return filled, total


class FormStore:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self._lock = threading.RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    # ------------------------------------------------------------------ templates
    @staticmethod
    def _template(row, full=True):
        out = {"id": row["id"], "name": row["name"], "by": row["by_name"], "email": row["by_email"], "updated": row["updated"], "created": row["created"]}
        heads, rows = json.loads(row["heads"]), json.loads(row["rows"])
        out.update(columns=len(heads), fields=len(rows))
        if full:
            out.update(heads=heads, rows=rows)
        return out

    def list_templates(self):
        with self._lock:
            return [self._template(r, full=False) for r in self._db.execute("SELECT * FROM templates ORDER BY name COLLATE NOCASE")]

    def get_template(self, template_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
        if row is None:
            raise FormError("not_found", "No such template.")
        return self._template(row)

    def save_template(self, template_id, name, heads, rows, by_name="", by_email=""):
        """Creates a template (template_id None) or replaces one. Drafts already made from it keep their own copy of the rows. -> the template."""
        name = _name(name)
        heads, rows = check_grid(heads, rows)
        now = self.clock()
        with self._lock:
            try:
                if template_id is None:
                    cur = self._db.execute("INSERT INTO templates (name, heads, rows, by_name, by_email, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                           (name, json.dumps(heads), json.dumps(rows), by_name, by_email, now, now))
                    template_id = cur.lastrowid
                else:
                    self.get_template(template_id)
                    self._db.execute("UPDATE templates SET name = ?, heads = ?, rows = ?, updated = ? WHERE id = ?", (name, json.dumps(heads), json.dumps(rows), now, template_id))
                    self._db.execute("UPDATE drafts SET template_name = ? WHERE template_id = ?", (name, template_id))
                self._db.commit()
            except sqlite3.IntegrityError:
                self._db.rollback()
                raise FormError("exists", "A template with this name already exists.")
        return self.get_template(template_id)

    def delete_template(self, template_id):
        with self._lock:
            self.get_template(template_id)
            self._db.execute("DELETE FROM templates WHERE id = ?", (template_id,))
            self._db.execute("UPDATE drafts SET template_id = NULL WHERE template_id = ?", (template_id,))   # drafts stay, with their own copy
            self._db.commit()

    # ------------------------------------------------------------------ drafts
    @staticmethod
    def _draft(row, full=True):
        rows, base = json.loads(row["rows"]), json.loads(row["base"])
        filled, total = progress(rows, base)
        out = {"id": row["id"], "name": row["name"], "template_id": row["template_id"], "template": row["template_name"], "by": row["by_name"], "email": row["by_email"],
               "updated": row["updated"], "created": row["created"], "filled": filled, "total": total, "complete": total > 0 and filled == total}
        if full:
            out.update(heads=json.loads(row["heads"]), rows=rows, base=base)
        return out

    def list_drafts(self, offset=0, limit=PAGE, text=""):
        """-> {"drafts": [...newest first], "total": n}. Everyone sees every draft."""
        where, args = "1 = 1", []
        if text:
            like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where, args = "(name LIKE ? ESCAPE '\\' OR template_name LIKE ? ESCAPE '\\' OR by_name LIKE ? ESCAPE '\\' OR by_email LIKE ? ESCAPE '\\')", [like] * 4
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM drafts WHERE {where}", args).fetchone()[0]
            rows = self._db.execute(f"SELECT * FROM drafts WHERE {where} ORDER BY updated DESC, id DESC LIMIT ? OFFSET ?", args + [max(1, min(int(limit), 100)), max(0, int(offset))]).fetchall()
        return {"drafts": [self._draft(r, full=False) for r in rows], "total": total}

    def get_draft(self, draft_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise FormError("not_found", "No such draft.")
        return self._draft(row)

    def create_draft(self, name, template_id, heads, rows, by_name="", by_email="", can_restructure=False):
        """A draft exists from its first save. The grid must be the template's (same columns and rows, only empty cells changed) unless the person may restructure."""
        name = _name(name)
        template = self.get_template(template_id)
        heads, rows = check_grid(heads, rows)
        self._check_against(template["heads"], template["rows"], heads, rows, can_restructure)
        now = self.clock()
        with self._lock:
            cur = self._db.execute("INSERT INTO drafts (name, template_id, template_name, heads, rows, base, by_name, by_email, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                   (name, template["id"], template["name"], json.dumps(heads), json.dumps(rows), json.dumps(template["rows"] if not can_restructure else self._rebase(template["rows"], template["heads"], rows, heads)),
                                    by_name, by_email, now, now))
            self._db.commit()
        return self.get_draft(cur.lastrowid)

    def save_draft(self, draft_id, name, heads, rows, can_restructure=False):
        draft = self.get_draft(draft_id)
        name = _name(name)
        heads, rows = check_grid(heads, rows)
        base_rows = draft["base"]
        self._check_against(draft["heads"], base_rows, heads, rows, can_restructure)
        with self._lock:
            base = base_rows if not can_restructure else self._rebase(base_rows, draft["heads"], rows, heads)
            self._db.execute("UPDATE drafts SET name = ?, heads = ?, rows = ?, base = ?, updated = ? WHERE id = ?", (name, json.dumps(heads), json.dumps(rows), json.dumps(base), self.clock(), draft_id))
            self._db.commit()
        return self.get_draft(draft_id)

    @staticmethod
    def _rebase(base, base_heads, rows, heads):
        """The fixed cells after a person who may restructure changed the shape: a cell keeps the fixed text it had (found by its row's field name and its column
        title); new rows and new columns start empty, so what the person typed in them counts as filled in."""
        by_field = {line[0].lower(): line for line in base}
        column = {h.lower(): i for i, h in enumerate(base_heads)}
        out = []
        for line in rows:
            old = by_field.get(line[0].lower())
            cells = [(old[column[h.lower()]] if old and h.lower() in column and column[h.lower()] < len(old) else "") for h in heads]
            cells[0] = line[0]                                         # a field name is always fixed text, a new row's too
            out.append(cells)
        return out

    @staticmethod
    def _check_against(heads, base, new_heads, new_rows, can_restructure):
        """Without the right to restructure, the columns and the rows must be the same and a fixed cell (one with text in the template) must not change."""
        if can_restructure:
            if len(new_heads) == len(heads) and new_heads != heads:           # the column titles come from the template: adding or removing a column is allowed, renaming one is not
                raise FormError("no_rename", "The column titles of a draft come from its template and cannot be renamed. Change the template to rename a column.")
            return
        if new_heads != heads or [r[0] for r in new_rows] != [r[0] for r in base]:
            raise FormError("no_restructure", "You cannot change the rows or columns of a draft.")
        for line, original in zip(new_rows, base):
            for cell, was in zip(line, original):
                if was and cell != was:
                    raise FormError("fixed_cell", f'"{original[0]}": a cell that the template fills in cannot be changed.')

    def delete_draft(self, draft_id):
        with self._lock:
            self.get_draft(draft_id)
            self._db.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
            self._db.commit()
