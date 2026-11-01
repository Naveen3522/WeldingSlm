"""Which documents the index was built from, so a new, changed or removed file can be noticed (SQLite, a fingerprint per file).

    scan(folder) · Manifest.detect() · Manifest.mark_current() · Manifest.seed_if_empty()
"""
import hashlib
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_manifest (
    name        TEXT PRIMARY KEY,
    sha256      TEXT NOT NULL,
    size        INTEGER NOT NULL,
    ingested_at REAL NOT NULL
);
"""
SUFFIX = ".docx"


def _digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def scan(folder):
    """-> {file name: (sha256, size)} for every document in the folder (temporary Word files such as ~$x.docx are ignored)."""
    out = {}
    for p in sorted(Path(folder).glob("*" + SUFFIX)):
        if p.is_file() and not p.name.startswith("~$"):
            out[p.name] = (_digest(p), p.stat().st_size)
    return out


class Manifest:
    def __init__(self, path, folder, clock=time.time):
        self.folder, self.clock = Path(folder), clock
        self._lock = threading.RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def _known(self):
        with self._lock:
            return {r["name"]: (r["sha256"], r["size"], r["ingested_at"]) for r in self._db.execute("SELECT * FROM doc_manifest")}

    def seed_if_empty(self):
        """On first use the index is assumed to match the folder, so the first check does not call every document new."""
        with self._lock:
            if not self._db.execute("SELECT 1 FROM doc_manifest LIMIT 1").fetchone():
                self.mark_current()
                return True
        return False

    def mark_current(self, current=None):
        """Records the folder as it is now as the state the index was built from."""
        current = scan(self.folder) if current is None else current
        now = self.clock()
        with self._lock:
            self._db.execute("DELETE FROM doc_manifest")
            self._db.executemany("INSERT INTO doc_manifest (name, sha256, size, ingested_at) VALUES (?, ?, ?, ?)", [(n, s, z, now) for n, (s, z) in current.items()])
            self._db.commit()

    def detect(self):
        """-> {"new": [...], "changed": [...], "removed": [...], "documents": [{name, size, status}], "pending": bool, "last": time or None}"""
        current, known = scan(self.folder), self._known()
        new = [n for n in current if n not in known]
        changed = [n for n in current if n in known and known[n][0] != current[n][0]]
        removed = [n for n in known if n not in current]
        docs = [{"name": n, "size": current[n][1], "status": "new" if n in new else "changed" if n in changed else "current"} for n in current]
        last = max((v[2] for v in known.values()), default=None)
        return {"new": new, "changed": changed, "removed": removed, "documents": docs, "pending": bool(new or changed or removed), "last": last}
