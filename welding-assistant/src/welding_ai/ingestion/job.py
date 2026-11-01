"""One background re-index at a time, with a status the page can poll (so it carries on when the browser is closed and reopened)."""
import threading
import time

from welding_ai.ingestion.rebuild import Cancelled, RebuildError


class IndexJob:
    def __init__(self, run, clock=time.time):
        """run(progress, cancel) -> a report dict; it calls progress(stage) as it goes and stops if cancel is set."""
        self._run, self.clock = run, clock
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._state = {"state": "idle", "stage": "", "started": None, "finished": None, "message": "", "report": None}
        self._thread = None

    def status(self):
        with self._lock:
            return dict(self._state)

    @property
    def running(self):
        with self._lock:
            return self._state["state"] == "running"

    def start(self):
        """-> False if a re-index is already running."""
        with self._lock:
            if self._state["state"] == "running":
                return False
            self._cancel.clear()
            self._state = {"state": "running", "stage": "Starting", "started": self.clock(), "finished": None, "message": "", "report": None}
        self._thread = threading.Thread(target=self._work, daemon=True)
        self._thread.start()
        return True

    def cancel(self):
        self._cancel.set()

    def wait(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _work(self):
        try:
            report = self._run(lambda stage: self._set(stage=stage), self._cancel)
            self._set(state="done", stage="", finished=self.clock(), report=report, message="The documents are indexed.")
        except Cancelled:
            self._set(state="cancelled", stage="", finished=self.clock(), message="Cancelled. The search index was not changed.")
        except RebuildError as e:
            self._set(state="failed", stage="", finished=self.clock(), message=e.message)
        except Exception as e:                                                  # never leave the page waiting on a job that died
            self._set(state="failed", stage="", finished=self.clock(), message=f"The re-index failed ({type(e).__name__}: {str(e)[:200]}).")
