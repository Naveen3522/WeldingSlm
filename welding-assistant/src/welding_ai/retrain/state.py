"""The retrain job's state, kept in a small JSON file so it survives the browser closing, the server restarting and the job outliving both.

The runner (its own process) writes it; the server and the page only read it, except to ask for a cancel. A write replaces the whole file at once.
"""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

IDLE = {"status": "idle"}
ACTIVE = ("running",)
FINISHED = ("finished", "failed", "cancelled")


class JobState:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock

    def read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return dict(IDLE)

    def write(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        state = {**state, "updated": self.clock()}
        fd, temp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
        for attempt in range(20):                            # Windows refuses to replace a file another process is reading at that instant
            try:
                os.replace(temp, self.path)
                return state
            except PermissionError:
                time.sleep(0.05)
        os.replace(temp, self.path)
        return state

    def update(self, **changes):
        return self.write({**self.read(), **changes})

    def request_cancel(self):
        return self.update(cancel_requested=True) if self.read().get("status") in ACTIVE else self.read()

    def cancel_requested(self):
        return bool(self.read().get("cancel_requested"))


def process_alive(pid):
    """True while a process with this id exists (Windows tasklist; no extra packages)."""
    if not pid:
        return False
    out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"], capture_output=True, text=True).stdout
    return str(int(pid)) in out.split()


def kill_tree(pid):
    """Ends a process and everything it started (the runner starts the trainer)."""
    subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True)


def reconcile(state_file, alive=None):
    """If the file says a job is running but its process is gone, mark it failed. -> the (possibly updated) state."""
    alive = alive or process_alive
    s = state_file.read()
    if s.get("status") in ACTIVE and not alive(s.get("pid")):
        return state_file.update(status="failed", stage="", message="The retraining stopped unexpectedly. The model that was in use before is still in use.")
    return s
