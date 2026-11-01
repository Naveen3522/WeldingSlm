"""Passages written by hand: text the documents do not have, kept in their own file so a document rebuild never removes them.

    ManualPassages(path).add(title, text, by)   -> the saved passage
    ManualPassages.remove(passage_id)           ManualPassages.listing()
    merge(chunks, passages)                     -> the index's passages followed by these, as the retrieval index expects them

Each passage becomes one more passage of the library under the document name "Added by hand", so a question finds it and quotes it like any other. Nothing here
reads the documents again: adding a passage only means the search index is built again from what is already on disk, which takes seconds.
"""
import json
import threading
import time
from pathlib import Path

SOURCE = "Added by hand"
TITLE_MIN, TITLE_MAX, TEXT_MIN, TEXT_MAX = 3, 120, 20, 3000


class PassageError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class ManualPassages:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self._lock = threading.RLock()

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _write(self, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        temp.replace(self.path)

    def listing(self):
        with self._lock:
            return self._read()

    def add(self, title, text, by=""):
        title, text = " ".join((title or "").split()), (text or "").strip()
        if not TITLE_MIN <= len(title) <= TITLE_MAX:
            raise PassageError(f"The passage title must be {TITLE_MIN} to {TITLE_MAX} characters.")
        if not TEXT_MIN <= len(text) <= TEXT_MAX:
            raise PassageError(f"The passage text must be {TEXT_MIN} to {TEXT_MAX} characters.")
        with self._lock:
            rows = self._read()
            same = next((r for r in rows if r["title"].lower() == title.lower()), None)
            if same:                                                         # the same title again updates that passage, so a correction does not leave two
                same.update(text=text, by=by, updated=self.clock())
                self._write(rows)
                return same
            row = {"id": max([r["id"] for r in rows] + [0]) + 1, "title": title, "text": text, "by": by, "created": self.clock()}
            rows.append(row)
            self._write(rows)
        return row

    def remove(self, passage_id):
        with self._lock:
            rows = self._read()
            kept = [r for r in rows if r["id"] != int(passage_id)]
            if len(kept) != len(rows):
                self._write(kept)
            return len(kept) != len(rows)


def merge(chunks, passages):
    """The document passages followed by the hand-written ones. The new passages carry chunk numbers after the last document passage."""
    if not passages:
        return chunks
    first = max((c.get("chunk_id", i) for i, c in enumerate(chunks)), default=-1) + 1
    extra = [{"source": SOURCE, "heading": p["title"], "text": p["text"], "chunk_id": first + i, "manual": True} for i, p in enumerate(passages)]
    return list(chunks) + extra
