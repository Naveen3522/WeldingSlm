"""Adding a model from a package: unpack its adapter, then turn it into a model Ollama can serve, the same way a retrain does (merge, convert, register).

    Installer.start(package, user)  -> (True, state) or (False, why); the work runs in a background thread
    Installer.status()              -> {"state": idle | running | done | failed, "step", "message", "model_id", "name"}

The model is added next to the others and is not made active: an administrator chooses when to use it. Only one install runs at a time. The adapter is kept under
runs/<dataset>/adapter like a retrained model's, so an added model can be exported again.
"""
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

STEPS = {"merging": "Merging the adapter into the base model", "converting": "Converting it for Ollama", "registering": "Adding it to Ollama", "testing": "Testing it"}
TAGGED = re.compile(r"(STAGE|DONE|FAILED) (\{.*\})\s*$")


class Installer:
    def __init__(self, runs_dir, pipeline_dir, registry, python=sys.executable, popen=subprocess.Popen, clock=time.time, remove=lambda name: True):
        self.runs_dir, self.pipeline, self.registry = Path(runs_dir), Path(pipeline_dir), registry
        self.python, self.popen, self.clock, self.remove = python, popen, clock, remove
        self._lock = threading.Lock()
        self._state = {"state": "idle"}
        self.thread = None

    def status(self):
        with self._lock:
            return dict(self._state)

    @property
    def running(self):
        return self.status()["state"] == "running"

    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    def start(self, package, user="admin"):
        with self._lock:
            if self._state["state"] == "running":
                return False, "A model is already being added."
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
            self._state = {"state": "running", "step": "Unpacking the file", "message": "", "model_id": f"welding-slm-import-{stamp}", "name": package.manifest["name"], "started": self.clock()}
        self.thread = threading.Thread(target=self._run, args=(package, stamp, user), daemon=True)
        self.thread.start()
        return True, self.status()

    def _fail(self, message, run_dir=None):
        import shutil
        if run_dir:
            shutil.rmtree(run_dir, ignore_errors=True)
        self.remove(self.status().get("model_id", ""))
        self._set(state="failed", step="", message=message)

    def _run(self, package, stamp, user):
        model_id, dataset = f"welding-slm-import-{stamp}", f"import-{stamp}"
        run_dir = self.runs_dir / dataset
        try:
            adapter = package.extract(run_dir / "adapter")
            work = run_dir / "export"
            cmd = [self.python, str(self.pipeline / "export.py"), "--adapter", str(adapter), "--name", model_id, "--work", str(work)]
            proc = self.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", cwd=str(self.pipeline))
            done, error = False, ""
            for line in proc.stdout:
                m = TAGGED.search(line.strip())
                if not m:
                    continue
                try:
                    payload = json.loads(m.group(2))
                except ValueError:
                    continue
                if m.group(1) == "STAGE":
                    name = payload.get("name", "")
                    self._set(step=next((text for key, text in STEPS.items() if name.startswith(key)), name))
                elif m.group(1) == "DONE":
                    done = True
                else:
                    error = payload.get("error", "")
            code = proc.wait()
            if code or not done:
                return self._fail("Adding the model failed: " + (error or f"the export stopped with code {code}") + ". Nothing was added.", run_dir)
            self._finish(package, model_id, dataset, user)
            self._slim(work)
        except Exception as e:                                                      # never leave the job looking as if it is still running
            self._fail(f"Adding the model failed ({type(e).__name__}: {str(e)[:200]}). Nothing was added.", run_dir)

    @staticmethod
    def _slim(work):
        import shutil
        shutil.rmtree(Path(work) / "merged_16bit", ignore_errors=True)
        for gguf in Path(work).glob("*.gguf"):
            gguf.unlink(missing_ok=True)

    def _finish(self, package, model_id, dataset, user):
        m = package.manifest
        gate = dict(m.get("gate") or {})
        gate.update(imported=True, pairs=gate.get("pairs", m.get("pairs", 0)), documents=gate.get("documents", 0))
        self.registry.record_training(model_id, dataset, gate, user)
        taken = self.registry.labels()
        base, label, n = str(m["name"]).strip()[:55], None, 1
        for n in range(1, 100):
            label = base if n == 1 else f"{base} ({n})"
            if label.lower() not in {t.lower() for t in taken}:
                break
        self.registry.rename(model_id, label, ((m.get("notes") or "") + " Added from a model file.").strip()[:500], user)
        self._set(state="done", step="", message=f'"{label}" was added. It is not in use yet: choose it in the table when you want it used.', label=label)
