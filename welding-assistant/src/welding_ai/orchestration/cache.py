"""Small persistent cache of verified model answers."""
import json
import re
import threading
from pathlib import Path


class AnswerCache:
    # Load the on-disk cache (or start empty).
    def __init__(self, path, max_items):
        self.path, self.max, self.lock = Path(path), max_items, threading.Lock()
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}

    # Normalize a question to a cache key (lowercase alphanumeric words only).
    @staticmethod
    def key(q):
        return " ".join(re.findall(r"[a-z0-9]+", q.lower()))

    # Look up a cached entry for a question.
    def get(self, q):
        return self.data.get(self.key(q))

    # Store an entry, evicting the oldest if over the size limit, and persist to disk.
    def put(self, q, entry):
        with self.lock:
            k = self.key(q)
            self.data.pop(k, None)
            self.data[k] = entry
            while len(self.data) > self.max:
                self.data.pop(next(iter(self.data)))
            try:
                self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

    # Empty the cache in memory and on disk.
    def clear(self):
        with self.lock:
            self.data = {}
            try:
                self.path.unlink()
            except Exception:
                pass
