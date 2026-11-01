"""What the server does with the retrain job: show what it would cost, start it as its own process, report it, and cancel it.

    plan(mode) -> rows, steps, seconds     start(mode)     status()     cancel()
The job runs in its own process (welding_ai.retrain.run), so it carries on when the page is closed or the server restarts; its state file is the truth.
"""

import subprocess
import sys
from pathlib import Path

from welding_ai import paths
from welding_ai.retrain import eta
from welding_ai.retrain.state import ACTIVE, FINISHED, JobState, kill_tree, process_alive, reconcile
from welding_ai.training import baseset, datasets

MODE_LABELS = {"full": "Full retrain: every existing and new example", "short": "Short run: the new examples plus a share of the existing ones", "test": "Quick test: proves the whole chain works (about 30 minutes)"}
DETACHED = 0x00000008 | 0x00000200            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: the job is not tied to the server's console


class Retrain:
    def __init__(self, state, history, queue, base_rows, registry=None, popen=subprocess.Popen, python=sys.executable, log_path=None):
        self.state, self.history, self.queue, self.base_rows = state, history, queue, base_rows
        self.registry, self.popen, self.python = registry, popen, python
        self.log_path = Path(log_path or paths.RETRAIN_DIR / "last_run.log")

    # ------------------------------------------------------------------ what it would cost
    def plan(self, mode, ids=None):
        """-> {"mode", "rows", "steps", "seconds", "human", "label", "new_pairs"} for a run started now. `ids`: train on only these approved pairs (a short run)."""
        if mode not in eta.GATE_ROWS:
            raise ValueError("The mode must be full, short or test.")
        approved = len(self.queue.trainable(only_unused=(mode == "short" and ids is None), ids=ids))
        rows = datasets.TEST_ROWS if mode == "test" else baseset.TARGET        # every run builds a fresh set of the same size from the documents (training/baseset.py)
        e = eta.estimate(rows, mode, self.history)
        return {"mode": mode, "rows": rows, "steps": e["steps"], "seconds": e["seconds"], "human": eta.human(e["seconds"]), "label": MODE_LABELS[mode],
                "new_pairs": approved, "measured": e["measured"], "ready": mode == "full" or approved > 0}

    def plans(self):
        return {m: self.plan(m) for m in ("short", "full", "test")}

    # ------------------------------------------------------------------ the job
    def status(self):
        s = reconcile(self.state)
        return {**s, "running": s.get("status") in ACTIVE, "human_left": eta.human(s["eta_end"] - self.state.clock()) if s.get("eta_end") and s.get("status") in ACTIVE else ""}

    @property
    def running(self):
        return self.status()["running"]

    def start(self, mode, ids=None):
        """-> (True, state) or (False, why). The job is a separate, detached process. `ids`: train on only these approved pairs (the ticked rows), with the base set replayed."""
        if self.running:
            return False, "A retrain is already running."
        if ids is not None:
            mode = "short"
        plan = self.plan(mode, ids)
        if not plan["ready"]:
            return False, "None of the chosen rows is in the training set." if ids is not None else "There are no new question-and-answer pairs to train on. Add documents and re-index first, or choose a full retrain."
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.state.write({"status": "running", "mode": mode, "stage": "prepare", "label": "Starting", "percent": 0, "cancel_requested": False, "cancellable": True,
                          "eta_end": self.state.clock() + plan["seconds"], "message": "", "pid": None, "plan": plan})
        log = open(self.log_path, "wb")
        proc = self.popen([self.python, "-m", "welding_ai.retrain.run", "--mode", mode, *(["--ids", ",".join(str(int(i)) for i in ids)] if ids is not None else [])], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                          cwd=str(paths.ROOT), env=self._env(), creationflags=DETACHED, close_fds=True)
        log.close()
        self.state.update(pid=proc.pid)
        return True, self.status()

    def _env(self):
        import os
        return dict(os.environ, PYTHONPATH=str(paths.ROOT / "src"), PYTHONIOENCODING="utf-8")

    def cancel(self):
        """-> (True, state) or (False, why). Ends the job and everything it started, then removes what it had made."""
        s = self.state.read()
        if s.get("status") not in ACTIVE:
            return False, "No retrain is running."
        if s.get("cancellable") is False:
            return False, "It is switching to the new model right now, which takes a few seconds. It can no longer be cancelled."
        self.state.update(cancel_requested=True)
        for pid in (s.get("child_pid"), s.get("pid")):
            if pid:
                kill_tree(pid)
        self._remove_leftovers(s)
        self.state.update(status="cancelled", stage="", label="", eta_end=None, child_pid=None, finished=self.state.clock(),
                          message="Cancelled. The model in use was kept; the partly trained model was removed.")
        return True, self.status()

    @staticmethod
    def _remove_leftovers(s):
        import shutil
        from welding_ai.retrain import ollama_admin
        if s.get("model_id"):
            ollama_admin.remove(s["model_id"])
        for key in ("run_dir", "dataset_dir"):
            if s.get(key) and Path(s[key]).exists():
                shutil.rmtree(s[key], ignore_errors=True)
