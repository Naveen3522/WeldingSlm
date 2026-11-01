"""The retrain runner and its control, with the trainer, the exporter and Ollama replaced by fakes, so every path can be exercised in seconds."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from welding_ai.answering.answering import NOT_FOUND
from welding_ai.llm.registry import ModelRegistry
from welding_ai.orchestration.prompts import SYSTEM_PROMPT
from welding_ai.retrain import control, eta, gate, run
from welding_ai.retrain.state import JobState
from welding_ai.training.queue import QaQueue


class FakeIndex:
    def __init__(self):
        self.chunks = [{"source": f"{n:02d}_doc.docx", "heading": f"Part > S{n}", "text": f"Passage {n} about topic {n}. " * 8} for n in range(8)]

    def search(self, question, k):
        return [{"source": c["source"], "heading": c["heading"], "text": c["text"]} for c in self.chunks[:k]]

    def focus(self, text, query, max_chars):
        return text[:max_chars]


class FakeOllama:
    def __init__(self):
        self.calls = []

    def unload_all(self):
        self.calls.append(("unload_all",))
        return []

    def warm(self, name):
        self.calls.append(("warm", name))

    def remove(self, name):
        self.calls.append(("remove", name))
        return True


def pair(n, doc="05_new.docx"):
    return {"doc": doc, "section": "S", "question": f"What is the corrective action for Fault {n}?", "answer": f"Replace part {n} and test it.", "evidence": f"Fault {n} | Replace part {n} and test it.",
            "kind": "table"}


class ScriptedRunner(run.Runner):
    """A runner whose trainer and exporter are scripts: it feeds the lines a real one prints and never starts a process."""

    def __init__(self, *a, train="ok", export="ok", cancel_at=None, **kw):
        super().__init__(*a, **kw)
        self.train_mode, self.export_mode, self.cancel_at = train, export, cancel_at
        self.seen_stages = []

    def _progress(self, stage, label, fraction=0.0, **extra):
        super()._progress(stage, label, fraction, **extra)
        if not self.seen_stages or self.seen_stages[-1] != stage:
            self.seen_stages.append(stage)

    def _stream(self, cmd, on_line):
        script = Path(cmd[1]).name
        if script == "train.py":
            on_line('STAGE {"name": "loading the base model"}')
            on_line('PROGRESS {"step": 0, "max_steps": 4, "loss": null, "elapsed": 0}')
            for step in range(1, 5):
                if self.cancel_at == step:
                    self.state.update(cancel_requested=True)
                self._check_cancel()
                if self.train_mode == "fail" and step == 2:
                    on_line('FAILED {"error": "CUDA out of memory. Tried to allocate 2.00 GiB"}')
                    return 1
                on_line(json.dumps({"step": step, "max_steps": 4, "loss": 2.0 - step * 0.1, "elapsed": step * 30.0}).join(["PROGRESS ", ""]))
            on_line('DONE {"adapter": "x", "steps": 4}')
            return 0
        work = Path(cmd[cmd.index("--work") + 1])
        (work / "merged_16bit").mkdir(parents=True, exist_ok=True)
        (work / "m.gguf").write_bytes(b"gguf")
        (work / "Modelfile").write_text("FROM m.gguf", encoding="utf-8")
        for name in ("merging the adapter into the model", "converting to GGUF", "registering with Ollama", "testing the new model"):
            on_line('STAGE ' + json.dumps({"name": name}))
        if self.export_mode == "fail":
            on_line('FAILED {"error": "the GGUF conversion failed"}')
            return 1
        on_line('DONE {"model": "x"}')
        return 0


def good_chat(model, messages):
    """The candidate answers well; the model in use invents a number now and then."""
    user = messages[1]["content"]
    answer = user.split("QUESTION:")[0]
    if "(no passages were found)" in user or "document: " not in user:
        return NOT_FOUND
    return "ok" if model == "weak" else "ok"


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        old = [{"kind": k, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"old {i}"}, {"role": "assistant", "content": "a"}]}
               for i, k in enumerate(["gold"] * 60 + ["wrong_passage"] * 10)]
        (self.root / "train.jsonl").write_text("\n".join(json.dumps(r) for r in old), encoding="utf-8")
        dev = []
        for i in range(40):
            kind = "gold" if i < 24 else ("wrong_passage" if i < 34 else "empty")
            answer = NOT_FOUND if kind != "gold" else f"The value is {i + 10} mm [1]"
            dev.append({"kind": kind, "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": f"EXCERPTS:\n[1] The value is {i + 10} mm\n\nQUESTION: q{i}"},
                                                   {"role": "assistant", "content": answer}]})
        (self.root / "dev.jsonl").write_text("\n".join(json.dumps(r) for r in dev), encoding="utf-8")
        self.state = JobState(self.root / "retrain" / "job.json")
        self.history = eta.History(self.root / "retrain" / "history.json")
        self.queue = QaQueue(":memory:")
        self.queue.add_many([pair(n) for n in range(1, 31)])
        self.registry = ModelRegistry(":memory:")
        self.ollama = FakeOllama()

    def runner(self, chat=None, **kw):
        return ScriptedRunner(self.state, self.history, self.queue, self.registry, FakeIndex, self.root / "train.jsonl", self.root / "dev.jsonl", self.root / "runs", "welding-slm-q8",
                              ollama=self.ollama, chat=chat or self.chat(), make_dataset=self.make_dataset, **kw)

    def make_dataset(self, approved, index, mode, base_train, base_dev):
        from welding_ai.training import datasets
        return datasets.make(approved, index, mode, base_train, base_dev, out_root=self.root / "datasets", clock=lambda: 1_700_000_000)

    @staticmethod
    def chat(candidate_invents=False, candidate_refuses_less=False):
        def chat(model, messages):
            user = messages[1]["content"]
            if "QUESTION: q" not in user:
                return "x"
            i = int(user.rsplit("QUESTION: q", 1)[1])
            if i >= 24:
                return "The value is 1 mm [1]" if (model != "welding-slm-q8" and candidate_refuses_less) else NOT_FOUND
            if model == "welding-slm-q8":
                return "The value is 999 mm [1]" if i % 3 == 0 else f"The value is {i + 10} mm [1]"            # the model in use invents a number now and then
            return "The value is 999 mm [1]" if candidate_invents and i % 2 == 0 else f"The value is {i + 10} mm [1]"
        return chat


class Success(Base):
    def test_a_short_run_that_passes_makes_the_new_model_active_and_uses_up_the_pairs(self):
        r = self.runner()
        r.run("short")
        s = self.state.read()
        self.assertEqual((s["status"], s["promoted"]), ("finished", True))
        self.assertEqual(self.registry.active(), r.model_id)
        self.assertEqual(self.queue.counts()["unused"], 0)
        self.assertEqual(r.seen_stages, ["prepare", "train", "export", "gate", "switch"])
        self.assertIn("now the active model", s["message"])
        self.assertNotIn(("remove", r.model_id), self.ollama.calls)                                    # a promoted model is kept
        self.assertEqual(self.ollama.calls[0], ("unload_all",))                                        # the GPU is freed before training
        self.assertEqual(self.ollama.calls[-1], ("warm", r.model_id))                                  # and the model now in use is loaded again at the end
        m = next(x for x in self.registry.describe([{"id": r.model_id, "label": "x", "note": ""}], "welding-slm-q8"))
        self.assertRegex(m["label"], r"^weld-\d{4}-\d{2}-\d{2}(-\d+)?$")
        self.assertEqual(m["dataset"], r.state.read()["dataset"])
        self.assertTrue(m["gate"]["passed"])
        self.assertFalse(m["hidden"])

    def test_a_promoted_model_replaces_the_earlier_trained_models_but_never_the_one_in_use_or_the_builtin(self):
        self.registry.record_training("welding-slm-old1", "v1", {"passed": True})
        self.registry.record_training("welding-slm-old2", "v2", {"passed": True})
        r = self.runner()
        r.run("short")
        removed = [c[1] for c in self.ollama.calls if c[0] == "remove"]
        self.assertEqual(sorted(removed), ["welding-slm-old1", "welding-slm-old2"])
        self.assertEqual(self.registry.trained_ids(), [r.model_id])
        self.assertNotIn("welding-slm-q8", removed)

    def test_a_tag_after_the_trainers_progress_bar_text_is_still_read(self):
        glued = ' 33%|###3      | 1/3 [01:39<03:18, 99.33s/it]PROGRESS {"step": 2, "max_steps": 3, "loss": 1.19, "elapsed": 222.8}'
        self.assertEqual(run.Runner._tagged(glued), ("PROGRESS", {"step": 2, "max_steps": 3, "loss": 1.19, "elapsed": 222.8}))
        self.assertEqual(run.Runner._tagged("some text"), (None, None))

    def test_progress_only_goes_forward_and_the_finish_time_is_set(self):
        seen = []
        original = self.state.update
        self.state.update = lambda **kw: (seen.append(kw), original(**kw))[1]
        self.runner().run("short")
        percents = [kw["percent"] for kw in seen if "percent" in kw and isinstance(kw["percent"], (int, float))]
        self.assertEqual(percents, sorted(percents))
        self.assertTrue(any(kw.get("eta_end") for kw in seen))
        self.assertEqual(self.state.read()["percent"], 100)
        self.assertIsNone(self.state.read()["eta_end"])

    def test_the_live_speed_replaces_the_estimate_and_is_remembered_for_next_time(self):
        self.runner().run("short")
        self.assertAlmostEqual(self.history.measured()["sec_per_step"], 30.0)                        # the fake trainer reported 30 s per step

    def test_a_kept_run_drops_the_large_files_ollama_no_longer_needs_but_keeps_the_adapter_and_dataset(self):
        r = self.runner()
        (r.runs_dir / "x").mkdir(parents=True, exist_ok=True)
        r.run("short")
        work = r.run_dir / "export"
        self.assertFalse((work / "merged_16bit").exists())
        self.assertEqual(list(work.glob("*.gguf")), [])
        self.assertTrue((work / "Modelfile").exists())
        self.assertTrue(Path(self.state.read()["dataset_dir"]).exists())

    def test_a_test_run_proves_the_chain_and_leaves_nothing_behind(self):
        r = self.runner()
        r.run("test")
        self.assertFalse(r.run_dir.exists())
        self.assertFalse(Path(self.state.read()["dataset_dir"]).exists())
        s = self.state.read()
        self.assertEqual((s["status"], s["promoted"]), ("finished", False))
        self.assertIn("Test run complete", s["message"])
        self.assertIn(("remove", r.model_id), self.ollama.calls)
        self.assertIsNone(self.registry.active())
        self.assertEqual(self.queue.counts()["unused"], 30)                                           # nothing was used up


class GateOutcome(Base):
    def test_a_model_that_fails_the_gate_is_kept_but_hidden_and_the_active_model_does_not_change(self):
        r = self.runner(chat=self.chat(candidate_refuses_less=True))
        r.run("short")
        s = self.state.read()
        self.assertEqual((s["status"], s["promoted"]), ("finished", False))
        self.assertIsNone(self.registry.active())
        m = next(x for x in self.registry.describe([{"id": r.model_id, "label": "x", "note": ""}], "welding-slm-q8"))
        self.assertTrue(m["hidden"])
        self.assertFalse(m["gate"]["passed"])
        self.assertIn("did not pass the checks", s["message"])
        self.assertIn("model in use was kept", s["message"])
        self.assertEqual(self.queue.counts()["unused"], 30)                                           # the pairs stay available for the next run
        self.assertNotIn(("remove", r.model_id), self.ollama.calls)                                   # the model is kept so an administrator can still look at it

    def test_a_failed_candidate_replaces_an_older_failed_one_and_the_model_in_use_stays(self):
        self.registry.record_training("welding-slm-inuse", "v1", {"passed": True})
        self.registry.set_active("welding-slm-inuse")
        self.registry.record_training("welding-slm-failed-before", "v2", {"passed": False})
        self.registry.set_hidden("welding-slm-failed-before", True)
        r = self.runner(chat=self.chat(candidate_refuses_less=True))
        r.run("short")
        removed = [c[1] for c in self.ollama.calls if c[0] == "remove"]
        self.assertEqual(removed, ["welding-slm-failed-before"])
        self.assertEqual(sorted(self.registry.trained_ids()), sorted([r.model_id, "welding-slm-inuse"]))

    def test_the_verdict_is_recorded_for_the_page(self):
        self.runner().run("short")
        v = self.state.read()["verdict"]
        self.assertIn("candidate", v)
        self.assertIn("active", v)
        self.assertEqual(v["active_model"], "welding-slm-q8")


class Stops(Base):
    def test_cancelling_during_training_removes_everything_and_changes_nothing(self):
        r = self.runner(cancel_at=2)
        r.run("short")
        s = self.state.read()
        self.assertEqual(s["status"], "cancelled")
        self.assertIn(("remove", r.model_id), self.ollama.calls)
        self.assertIsNone(self.registry.active())
        self.assertEqual(self.queue.counts()["unused"], 30)
        self.assertFalse(r.run_dir.exists())
        self.assertFalse((self.root / "datasets" / s["dataset"]).exists())
        self.assertEqual(self.history.measured()["sec_per_step"], eta.DEFAULT_SEC_PER_STEP)           # a cancelled run teaches the estimate nothing

    def test_a_failed_training_says_why_in_plain_words_and_keeps_the_old_model(self):
        r = self.runner(train="fail")
        r.run("short")
        s = self.state.read()
        self.assertEqual(s["status"], "failed")
        self.assertIn("graphics card ran out of memory", s["message"])
        self.assertIn("model in use was kept", s["message"])
        self.assertIsNone(self.registry.active())

    def test_a_failed_export_is_reported_and_cleaned_up(self):
        r = self.runner(export="fail")
        r.run("short")
        s = self.state.read()
        self.assertEqual(s["status"], "failed")
        self.assertIn("Preparing the new model failed", s["message"])
        self.assertFalse(r.run_dir.exists())

    def test_nothing_to_train_on_fails_before_anything_is_touched(self):
        for row in self.queue.approved():
            self.queue.decide([row["id"]], "rejected", "naveen")
        r = self.runner()
        r.run("short")
        s = self.state.read()
        self.assertEqual(s["status"], "failed")
        self.assertIn("no approved question-and-answer pairs", s["message"])
        self.assertNotIn(("unload_all",), self.ollama.calls)

    def test_a_crash_inside_the_runner_never_leaves_the_job_looking_busy(self):
        r = self.runner()
        r.train = mock.Mock(side_effect=RuntimeError("boom"))
        r.run("short")
        s = self.state.read()
        self.assertEqual(s["status"], "failed")
        self.assertIn("RuntimeError", s["message"])


class Control(Base):
    def make(self, popen=None):
        self.popen = popen or mock.Mock(return_value=mock.Mock(pid=4321))
        return control.Retrain(self.state, self.history, self.queue, base_rows=3000, registry=self.registry, popen=self.popen, python="python.exe", log_path=self.root / "run.log")

    def test_the_plan_gives_a_time_for_each_mode_and_the_short_run_is_shorter(self):
        plans = self.make().plans()
        self.assertEqual(set(plans), {"short", "full", "test"})
        self.assertEqual(plans["short"]["rows"], plans["full"]["rows"])      # every run builds the same 3000-row set
        self.assertLess(plans["test"]["seconds"], plans["full"]["seconds"])
        self.assertLess(plans["test"]["seconds"], plans["short"]["seconds"])
        self.assertEqual(plans["full"]["rows"], 3000)
        self.assertEqual(plans["short"]["new_pairs"], 30)
        self.assertRegex(plans["full"]["human"], r"\d+ h \d\d min")

    def test_a_short_run_with_nothing_new_is_not_ready_but_a_full_one_is(self):
        for row in self.queue.approved():
            self.queue.decide([row["id"]], "rejected", "naveen")
        plans = self.make().plans()
        self.assertFalse(plans["short"]["ready"])
        self.assertTrue(plans["full"]["ready"])
        ok, why = self.make().start("short")
        self.assertFalse(ok)
        self.assertIn("no new question-and-answer pairs", why)

    def test_start_launches_a_detached_process_and_records_the_job(self):
        c = self.make()
        with mock.patch("welding_ai.retrain.state.process_alive", return_value=True):
            ok, s = c.start("short")
        self.assertTrue(ok)
        args, kwargs = self.popen.call_args
        self.assertEqual(args[0], ["python.exe", "-m", "welding_ai.retrain.run", "--mode", "short"])
        self.assertTrue(kwargs["creationflags"] & 0x00000008)                                         # DETACHED_PROCESS: the job outlives the server
        self.assertEqual((s["status"], s["pid"], s["running"]), ("running", 4321, True))
        self.assertTrue(s["eta_end"])

    def test_a_second_start_is_refused_while_one_runs(self):
        c = self.make()
        with mock.patch("welding_ai.retrain.control.reconcile", side_effect=lambda st: st.read()):
            c.start("short")
            ok, why = c.start("short")
        self.assertFalse(ok)
        self.assertIn("already running", why)
        self.assertEqual(self.popen.call_count, 1)

    def test_cancel_stops_the_processes_and_removes_what_was_made(self):
        c = self.make()
        with mock.patch("welding_ai.retrain.control.reconcile", side_effect=lambda st: st.read()):
            c.start("short")
            dataset, runs = self.root / "ds", self.root / "run"
            dataset.mkdir()
            runs.mkdir()
            self.state.update(child_pid=999, model_id="welding-slm-v3-x", run_dir=str(runs), dataset_dir=str(dataset))
            with mock.patch("welding_ai.retrain.control.kill_tree") as kill, mock.patch("welding_ai.retrain.ollama_admin.remove") as remove:
                ok, s = c.cancel()
        self.assertTrue(ok)
        self.assertEqual(sorted(call.args[0] for call in kill.call_args_list), [999, 4321])
        remove.assert_called_once_with("welding-slm-v3-x")
        self.assertFalse(dataset.exists() or runs.exists())
        self.assertEqual(s["status"], "cancelled")
        self.assertFalse(s["running"])

    def test_cancel_is_refused_once_the_switch_has_begun_or_when_nothing_runs(self):
        c = self.make()
        self.assertEqual(c.cancel()[0], False)
        with mock.patch("welding_ai.retrain.control.reconcile", side_effect=lambda st: st.read()):
            c.start("short")
            self.state.update(cancellable=False)
            ok, why = c.cancel()
        self.assertFalse(ok)
        self.assertIn("can no longer be cancelled", why)

    def test_a_job_whose_process_died_is_reported_as_failed_not_running(self):
        c = self.make()
        c.start("short")
        with mock.patch("welding_ai.retrain.state.process_alive", return_value=False):
            s = c.status()
        self.assertEqual((s["status"], s["running"]), ("failed", False))

    def test_time_left_is_shown_while_running(self):
        c = self.make()
        with mock.patch("welding_ai.retrain.control.reconcile", side_effect=lambda st: st.read()):
            c.start("short")
            self.state.update(eta_end=self.state.clock() + 2 * 3600 + 5 * 60 + 30)
            self.assertEqual(c.status()["human_left"], "2 h 05 min")


if __name__ == "__main__":
    unittest.main()
