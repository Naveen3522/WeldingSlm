"""Names and notes for the installed models, and which one is active (SQLite).

Renaming changes only the label people see; the Ollama model id and its weights are never touched, so a rename cannot break anything.
Each model can also carry the dataset it was trained on and its promotion-gate scores (written by the retrain job, shown to the admin).

    ModelRegistry.rename · set_active · active · describe · labels · forget
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

NAME_MIN, NAME_MAX, NOTES_MAX = 3, 60, 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_versions (
    ollama_id    TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    dataset      TEXT NOT NULL DEFAULT '',
    gate         TEXT NOT NULL DEFAULT '',
    updated_by   TEXT NOT NULL DEFAULT '',
    updated      REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class ModelError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class ModelRegistry:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self._lock = threading.RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        if "hidden" not in {r["name"] for r in self._db.execute("PRAGMA table_info(model_versions)")}:      # a column added after the first release
            self._db.execute("ALTER TABLE model_versions ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
            self._db.commit()

    # ------------------------------------------------------------------ reading
    def entry(self, ollama_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM model_versions WHERE ollama_id = ?", (ollama_id,)).fetchone()
        return dict(row) if row else {"ollama_id": ollama_id, "display_name": "", "notes": "", "dataset": "", "gate": "", "updated_by": "", "updated": 0, "hidden": 0}

    def active(self):
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key = 'active_model'").fetchone()
        return row["value"] if row else None

    def describe(self, installed, default):
        """installed: [{id, label, note}] from Ollama. -> the same models with the admin's name and notes applied and `active` marked.
        `default` is the model used when none has been chosen."""
        active = self.active() or default
        out = []
        for m in installed:
            e = self.entry(m["id"])
            out.append({**m, "label": e["display_name"] or m["label"], "builtin_label": m["label"], "notes": e["notes"], "dataset": e["dataset"],
                        "gate": json.loads(e["gate"]) if e["gate"] else None, "active": m["id"] == active, "renamed": bool(e["display_name"]), "hidden": bool(e["hidden"]), "updated": e["updated"]})
        return out

    # ------------------------------------------------------------------ writing
    def rename(self, ollama_id, display_name, notes, user, taken=()):
        """Sets the label and notes. An empty name goes back to the built-in label. `taken`: labels already used by the other models."""
        name, notes = (display_name or "").strip(), (notes or "").strip()
        if name and not NAME_MIN <= len(name) <= NAME_MAX:
            raise ModelError("bad_name", f"The name must be {NAME_MIN} to {NAME_MAX} characters, or empty to use the built-in name.")
        if len(notes) > NOTES_MAX:
            raise ModelError("bad_notes", f"Notes can be at most {NOTES_MAX} characters.")
        if name and name.lower() in {t.lower() for t in taken}:
            raise ModelError("name_taken", "Another model already has that name.")
        with self._lock:
            self._db.execute("INSERT INTO model_versions (ollama_id, display_name, notes, updated_by, updated) VALUES (?, ?, ?, ?, ?) "
                             "ON CONFLICT (ollama_id) DO UPDATE SET display_name = excluded.display_name, notes = excluded.notes, "
                             "updated_by = excluded.updated_by, updated = excluded.updated", (ollama_id, name, notes, user, self.clock()))
            self._db.commit()

    def record_training(self, ollama_id, dataset, gate, user="retrain"):
        """Remembers what a model was trained on and how it scored on the promotion gate (a dict)."""
        with self._lock:
            self._db.execute("INSERT INTO model_versions (ollama_id, dataset, gate, updated_by, updated) VALUES (?, ?, ?, ?, ?) "
                             "ON CONFLICT (ollama_id) DO UPDATE SET dataset = excluded.dataset, gate = excluded.gate, updated_by = excluded.updated_by, updated = excluded.updated",
                             (ollama_id, str(dataset), json.dumps(gate), user, self.clock()))
            self._db.commit()

    def set_hidden(self, ollama_id, hidden):
        """A model that failed the promotion gate is hidden from everyone's model picker; an administrator still sees it and can activate it."""
        with self._lock:
            self._db.execute("INSERT INTO model_versions (ollama_id, hidden) VALUES (?, ?) ON CONFLICT (ollama_id) DO UPDATE SET hidden = excluded.hidden",
                             (ollama_id, 1 if hidden else 0))
            self._db.commit()

    def set_active(self, ollama_id):
        with self._lock:
            self._db.execute("INSERT INTO settings (key, value) VALUES ('active_model', ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (ollama_id,))
            self._db.execute("UPDATE model_versions SET hidden = 0 WHERE ollama_id = ?", (ollama_id,))             # the model in use is never hidden
            self._db.commit()

    def trained_ids(self):
        """The models a retrain (or an added model file) made, oldest first: the ones that carry the dataset they were trained on."""
        with self._lock:
            return [r["ollama_id"] for r in self._db.execute("SELECT ollama_id FROM model_versions WHERE dataset != '' ORDER BY updated, ollama_id").fetchall()]

    def labels(self):
        """The names people gave (or the retrain gave) to models, so a new name can be made unique."""
        with self._lock:
            return {r["display_name"] for r in self._db.execute("SELECT display_name FROM model_versions WHERE display_name != ''").fetchall()}

    def forget(self, ollama_id):
        """Drops what is remembered about a model that has been deleted from Ollama."""
        with self._lock:
            self._db.execute("DELETE FROM model_versions WHERE ollama_id = ?", (ollama_id,))
            self._db.commit()
