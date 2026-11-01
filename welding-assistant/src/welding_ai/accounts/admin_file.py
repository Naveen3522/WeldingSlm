"""A plain text file holding each administrator's username, email, password and recovery key, kept current whenever one of those changes.

    record(user, password=None, key=None)   writes (or updates) the administrator's block; a value that is not given keeps what the file already has
    forget(user_id)                          removes the block (account deleted or no longer an administrator)

It is for the person who runs the server computer, so a lost administrator password can be read from the file instead of rebuilding the database. Only
administrators are written, and the file is never read back by the application. Keep it on the server computer; it is listed in .gitignore.
"""
import threading
import time
from pathlib import Path

from welding_ai.accounts import levels

HEADER = ("WELD ASSIST - ADMINISTRATOR CREDENTIALS\n"
          "Kept up to date automatically when an administrator changes a password or recovery key.\n"
          "Keep this file private: anyone who can read it can sign in as an administrator.\n")
FIELDS = (("id", "ID"), ("username", "Username"), ("email", "Email"), ("password", "Password"), ("key", "Recovery key"), ("updated", "Updated"))
WIDTH = max(len(label) for _, label in FIELDS)


class AdminFile:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self._lock = threading.Lock()

    def _read(self):
        """-> {id: {field: value}} from the file; a missing or hand-damaged file gives what could be read."""
        blocks = {}
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return blocks
        by_label = {label: name for name, label in FIELDS}
        for chunk in text.split("\n\n"):
            block = {}
            for line in chunk.splitlines():
                label, sep, value = line.partition(":")
                if sep and label.strip() in by_label:
                    block[by_label[label.strip()]] = value.strip()
            if block.get("id", "").isdigit():
                blocks[int(block["id"])] = block
        return blocks

    def _write(self, blocks):
        parts = [HEADER]
        for uid in sorted(blocks):
            parts.append("\n".join(f"{label:<{WIDTH}} : {blocks[uid].get(name, '')}" for name, label in FIELDS))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def record(self, user, password=None, key=None):
        """`user` is the public user dict (id, username, email, level). Ignored for anyone who is not an administrator."""
        with self._lock:
            blocks = self._read()
            if user["level"] < levels.ADMIN_LEVEL:
                if blocks.pop(user["id"], None) is not None:
                    self._write(blocks)
                return
            block = blocks.get(user["id"], {})
            block.update(id=str(user["id"]), username=user["username"], email=user["email"], updated=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.clock())))
            if password is not None:
                block["password"] = password
            if key is not None:
                block["key"] = key
            blocks[user["id"]] = block
            self._write(blocks)

    def forget(self, user_id):
        with self._lock:
            blocks = self._read()
            if blocks.pop(user_id, None) is not None:
                self._write(blocks)
