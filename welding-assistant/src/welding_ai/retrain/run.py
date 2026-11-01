"""The retrain runner: its own process, started by the server, that outlives the browser and even a server restart.

    python -m welding_ai.retrain.run --mode full|short|test

    prepare   build the training data from the approved pairs, free the GPU
    train     fine-tune (welding_slm/pipeline/train.py)
    export    merge, convert to GGUF, register with Ollama (welding_slm/pipeline/export.py)
    gate      the new model and the one in use answer the same held-out rows; the rules are in gate.py
    switch    make the new model the active one if it passed; otherwise it is kept, hidden from everyone but administrators

It writes its state to a JSON file as it goes (stage, step, percent, finish time). A cancel is honoured between and inside every stage up to the switch;
the model in use is never touched until the switch, so a cancel or a failure always leaves things exactly as they were.
"""
import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from welding_ai import paths
from welding_ai.retrain import eta, gate, ollama_admin
from welding_ai.retrain.state import JobState, kill_tree

STAGES = [("prepare", "Preparing the training data"), ("train", "Training the model"), ("export", "Preparing the new model for use"),
          ("gate", "Checking the new model against the one in use"), ("switch", "Switching")]
PIPELINE = paths.MODEL_DIR / "pipeline"
TAGGED = re.compile(r"(STAGE|PROGRESS|DONE|FAILED) (\{.*\})\s*$")


class Cancelled(Exception):
    pass


class Failed(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def friendly(error):
    text = (error or "").lower()
    if "out of memory" in text or "outofmemory" in text:
        return "The graphics card ran out of memory. Close any other program that uses the GPU and try again."
    return error or "The step failed without saying why."


class Runner:
    def __init__(self, state, history, queue_, registry, load_index, base_train, base_dev, runs_dir, active_default, ollama=ollama_admin, chat=gate.ollama_chat,
                 clock=time.time, python=sys.executable, make_dataset=None):
        self.state, self.history, self.queue, self.registry = state, history, queue_, registry
        self.load_index, self.base_train, self.base_dev, self.runs_dir = load_index, base_train, base_dev, Path(runs_dir)
        self.active_default, self.ollama, self.chat, self.clock, self.python = active_default, ollama, chat, clock, python
        self.make_dataset = make_dataset
        self.vectors = None                         # the embedding vectors that find near-copies among the questions (set by build(); None: not checked)
        self.run_dir = self.model_id = None
        self.select_ids = None                      # train on only these approved pairs (the ticked rows), or None for every one that applies
        self.est, self.done_s, self.stage_started = {}, 0.0, 0.0
        self.measured = {}

    # ------------------------------------------------------------------ state helpers
    def _check_cancel(self):
        if self.state.cancel_requested():
            raise Cancelled()

    def _progress(self, stage, label, fraction=0.0, **extra):
        """Records where the job is: the stage, how far through the whole job, and when it should finish."""
        now = self.clock()
        names = [s for s, _ in STAGES]
        total = sum(self.est.get(s, 0) for s in names) or 1.0
        before = sum(self.est.get(s, 0) for s in names[:names.index(stage)])
        this = self.est.get(stage, 0)
        remaining = this * (1 - fraction) + sum(self.est.get(s, 0) for s in names[names.index(stage) + 1:])
        self.state.update(stage=stage, label=label, percent=round(min(99.0, 100 * (before + this * fraction) / total), 1), eta_end=now + remaining, **extra)

    def _begin(self, stage):
        self.stage_started = self.clock()
        self._check_cancel()
        self._progress(stage, dict(STAGES)[stage])

    def _stream(self, cmd, on_line):
        """Runs a command and hands each output line to on_line, watching for a cancel. -> the exit code."""
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", TQDM_DISABLE="1", PYTHONPATH=str(Path(paths.ROOT) / "src"))
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(PIPELINE)) as proc:
            lines = queue.Queue()
            threading.Thread(target=lambda: ([lines.put(line.rstrip("\n")) for line in proc.stdout], lines.put(None)), daemon=True).start()
            self.state.update(child_pid=proc.pid)
            while True:
                if self.state.cancel_requested():
                    kill_tree(proc.pid)
                    raise Cancelled()
                try:
                    line = lines.get(timeout=1.0)
                except queue.Empty:
                    continue
                if line is None:
                    break
                on_line(line)
            code = proc.wait()
        self.state.update(child_pid=None)
        return code

    @staticmethod
    def _tagged(line):
        """The tag and payload of a line the pipeline scripts print. The trainer's progress bar shares the same stream and does not end its line, so a tag can follow its text."""
        m = TAGGED.search(line)
        if m:
            try:
                return m.group(1), json.loads(m.group(2))
            except ValueError:
                pass
        return None, None

    # ------------------------------------------------------------------ the stages
    def _refresh_pairs(self, index):
        """The pairs of every document as it is now, so a document added, changed or deleted since the last reindex is in the pool: new ones join the queue (approved unless an
        administrator turned automatic approval off), and ones an administrator rejected stay rejected."""
        try:
            from welding_ai.retrieval import tables as table_index
            from welding_ai.training import qa
            tables = table_index.load(index)
            if tables is None:
                return 0
            docs = sorted({c["source"] for c in index.chunks if c["source"] != "Added by hand" and not c.get("manual")})
            return self.queue.add_many(qa.generate(tables.t, index.chunks, docs, index.focus))["added"]
        except Exception:                                                    # the pool is only richer with it: never fail the training over it
            return 0

    def prepare(self, mode):
        self._begin("prepare")
        index = self.load_index()
        if mode != "test" and self.make_dataset is None:
            self._refresh_pairs(index)
        approved = self.queue.trainable(only_unused=(mode == "short" and self.select_ids is None), ids=self.select_ids)
        if mode == "test":
            approved = approved[:30]
        make = self.make_dataset or __import__("welding_ai.training.datasets", fromlist=["make"]).make
        try:
            meta = make(approved, index, mode, self.base_train, self.base_dev, **({"vectors": self.vectors} if self.vectors is not None else {}))
        except ValueError as e:
            raise Failed(str(e))
        self.model_id = f"welding-slm-{meta['version']}"
        self.run_dir = self.runs_dir / meta["version"]
        self.state.update(dataset=meta["version"], dataset_dir=meta["dir"], model_id=self.model_id, run_dir=str(self.run_dir), rows=meta["rows"],
                          new_pairs=meta["pairs"], ids=[p["id"] for p in approved])
        self.est = {s: v for s, v in eta.estimate(meta["rows"], mode, self.history)["parts"].items()}
        self.est["switch"] = 5.0
        self.ollama.unload_all()
        self.measured["prepare_s"] = self.clock() - self.stage_started
        return meta, approved

    def train(self, meta, mode):
        self._begin("train")
        cmd = [self.python, str(PIPELINE / "train.py"), "--train", str(Path(meta["dir"]) / "train.jsonl"), "--out", str(self.run_dir / "adapter"),
               "--checkpoints", str(self.run_dir / "checkpoints")] + (["--max-steps", "2"] if mode == "test" else [])
        seen = {"error": "", "done": False, "last": None}

        def on_line(line):
            tag, payload = self._tagged(line)
            if tag == "PROGRESS":
                seen["last"] = payload
                step, total = payload["step"], max(payload["max_steps"], 1)
                live = payload["elapsed"] / step if step else None
                if live:
                    self.est["train"] = total * live
                self._progress("train", dict(STAGES)["train"], fraction=step / total, step=step, max_steps=total, loss=payload.get("loss"))
            elif tag == "STAGE":
                self.state.update(detail=payload.get("name", ""))
            elif tag == "FAILED":
                seen["error"] = payload.get("error", "")
            elif tag == "DONE":
                seen["done"] = True

        code = self._stream(cmd, on_line)
        if code or not seen["done"]:
            raise Failed("Training stopped: " + friendly(seen["error"] or f"it exited with code {code}"))
        last = seen["last"] or {}
        if last.get("step"):
            self.measured["sec_per_step"] = last["elapsed"] / last["step"]

    def export(self):
        self._begin("export")
        cmd = [self.python, str(PIPELINE / "export.py"), "--adapter", str(self.run_dir / "adapter"), "--name", self.model_id, "--work", str(self.run_dir / "export")]
        seen = {"error": "", "done": False}

        def on_line(line):
            tag, payload = self._tagged(line)
            if tag == "STAGE":
                names = ["merging", "converting", "registering", "testing"]
                step = next((i for i, n in enumerate(names) if payload.get("name", "").startswith(n)), 0)
                self._progress("export", dict(STAGES)["export"], fraction=step / 4, detail=payload.get("name", ""))
            elif tag == "FAILED":
                seen["error"] = payload.get("error", "")
            elif tag == "DONE":
                seen["done"] = True

        code = self._stream(cmd, on_line)
        if code or not seen["done"]:
            raise Failed("Preparing the new model failed: " + friendly(seen["error"] or f"it exited with code {code}"))
        self.measured["export_s"] = self.clock() - self.stage_started
        self._slim()

    def _slim(self):
        """Ollama keeps its own copy of the converted model, so the 2.5 GB merged weights and the 1.3 GB GGUF are not needed any more. The adapter and the dataset stay."""
        work = self.run_dir / "export"
        shutil.rmtree(work / "merged_16bit", ignore_errors=True)
        for gguf in work.glob("*.gguf"):
            gguf.unlink(missing_ok=True)

    def check(self, meta, mode):
        self._begin("gate")
        rows = [json.loads(line) for line in (Path(meta["dir"]) / "dev.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        picked = gate.sample(rows, eta.GATE_ROWS[mode])
        active = self.registry.active() or self.active_default
        total = 2 * len(picked)

        def progress(offset):
            def tick(done, _total):
                self._check_cancel()
                self._progress("gate", dict(STAGES)["gate"], fraction=(offset + done) / total)
            return tick

        baseline = gate.score(active, picked, chat=self.chat, progress=progress(0))
        candidate = gate.score(self.model_id, picked, chat=self.chat, progress=progress(len(picked)))
        verdict = gate.compare(baseline, candidate, allow_small=(mode == "test"))
        verdict["active_model"], verdict["rows"] = active, len(picked)
        self.measured["gate_s_per_row"] = (self.clock() - self.stage_started) / max(total / 2, 1)
        return verdict

    def _new_name(self):
        """weld-2026-10-21, or weld-2026-10-21-2 when that name is already taken."""
        base, taken = f"weld-{time.strftime('%Y-%m-%d', time.localtime(self.clock()))}", self.registry.labels()
        return next(name for name in (base, *(f"{base}-{n}" for n in range(2, 100))) if name.lower() not in {t.lower() for t in taken})

    def switch(self, meta, mode, approved, verdict):
        self._begin("switch")
        self.state.update(cancellable=False)
        summary = {k: verdict["candidate"][k] for k in ("quality", "invented", "false_refusal", "refusal_recall", "source_line")}
        summary.update(passed=verdict["passed"], compared_with=verdict["active_model"], rows=verdict["rows"], pairs=meta["pairs"], documents=len(meta["documents"]), mode=mode)
        promoted = verdict["passed"] and mode != "test"
        self.registry.record_training(self.model_id, meta["version"], summary)
        self.registry.rename(self.model_id, self._new_name(), f"{meta['pairs']} new question-and-answer pairs from {len(meta['documents'])} document(s); dataset {meta['version']}.", "retrain")
        if promoted:
            self.registry.set_active(self.model_id)
            self.queue.mark_used([p["id"] for p in approved], meta["version"])
        elif mode != "test":
            self.registry.set_hidden(self.model_id, True)
        if mode == "test":                                                    # a test run proves the chain; it leaves nothing behind
            self.ollama.remove(self.model_id)
        else:
            self._retire_others()
        return promoted

    def _retire_others(self):
        """The page lists two models, the built-in one and the trained one, so a retrain replaces the earlier trained model: every other model a retrain made is removed from
        Ollama (the model in use and the new one stay). Their adapters stay on disk, so an old model can be exported or made again."""
        keep = {self.model_id, self.registry.active() or self.active_default}
        for old in self.registry.trained_ids():
            if old not in keep and self.ollama.remove(old):
                self.registry.forget(old)

    # ------------------------------------------------------------------ the job
    def cleanup(self, remove_model=True):
        if remove_model and self.model_id:
            self.ollama.remove(self.model_id)
        if self.run_dir and Path(self.run_dir).exists():
            shutil.rmtree(self.run_dir, ignore_errors=True)
        dataset_dir = self.state.read().get("dataset_dir")
        if dataset_dir and Path(dataset_dir).exists():
            shutil.rmtree(dataset_dir, ignore_errors=True)

    def run(self, mode):
        start = self.clock()
        self.state.write({"status": "running", "id": time.strftime("%Y%m%d-%H%M%S", time.localtime(start)), "mode": mode, "pid": os.getpid(), "started": start,
                          "stage": "prepare", "label": dict(STAGES)["prepare"], "percent": 0, "step": 0, "max_steps": 0, "cancel_requested": False, "cancellable": True,
                          "message": "", "eta_end": None})
        plan_rows = (self.state.read().get("plan") or {}).get("rows") or eta.GATE_ROWS["test"]
        self.est = dict(eta.estimate(plan_rows, mode, self.history)["parts"], switch=5.0)         # a first estimate, replaced once the real dataset is built
        try:
            meta, approved = self.prepare(mode)
            self.train(meta, mode)
            self.export()
            verdict = self.check(meta, mode)
            promoted = self.switch(meta, mode, approved, verdict)
            self.history.record(**self.measured)
            if mode == "test":
                self.cleanup(remove_model=False)                                # the model was removed in the switch; the files go too
            if mode == "test":
                message = "Test run complete: the whole chain worked (train, export, check). The test model was removed again; nothing in use changed."
            elif promoted:
                message = "The new model passed the checks and is now the active model. " + ("It improved: " + ", ".join(verdict["improved"]) + ".") if verdict["improved"] else "The new model is now active."
            else:
                message = "The new model did not pass the checks, so the model in use was kept. " + "; ".join(verdict["reasons"]) + "."
            self.state.update(status="finished", stage="", label="", percent=100, eta_end=None, promoted=promoted, verdict=verdict, message=message, finished=self.clock(), cancellable=False)
        except Cancelled:
            self.cleanup()
            self.state.update(status="cancelled", stage="", label="", eta_end=None, message="Cancelled. The model in use was kept; the partly trained model was removed.", finished=self.clock())
        except Failed as e:
            self.cleanup()
            self.state.update(status="failed", stage="", label="", eta_end=None, message=e.message + " The model in use was kept.", finished=self.clock())
        except Exception as e:                                                  # never leave the job looking as if it is still running
            self.cleanup()
            self.state.update(status="failed", stage="", label="", eta_end=None, message=f"The retraining failed ({type(e).__name__}: {str(e)[:200]}). The model in use was kept.", finished=self.clock())
        finally:
            self.ollama.warm(self.registry.active() or self.active_default)


def build(mode, ids=None):
    """The real dependencies, for running from the command line."""
    from welding_ai.core.config import Config
    from welding_ai.llm.registry import ModelRegistry
    from welding_ai.retrieval import retrieval
    from welding_ai.training.queue import QaQueue
    root = paths.RETRAIN_DIR
    from welding_ai.knowledge.embed import TextVectors
    runner = Runner(JobState(root / "job.json"), eta.History(root / "history.json"), QaQueue(paths.DB_PATH), ModelRegistry(paths.DB_PATH), retrieval.load,
                    paths.MODEL_DIR / "v2" / "train.jsonl", paths.MODEL_DIR / "v2" / "dev.jsonl", paths.MODEL_DIR / "runs", Config.default_model)
    runner.vectors = TextVectors(paths.INDEX_DIR / "questions")
    return runner


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("full", "short", "test"), required=True)
    ap.add_argument("--ids", default="", help="train on only these approved pairs (comma separated ids)")
    args = ap.parse_args()
    runner = build(args.mode)
    runner.select_ids = [int(i) for i in args.ids.split(",") if i] if args.ids else None
    runner.run(args.mode)
