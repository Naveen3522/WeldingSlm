"""The retrain job's building blocks: the time estimate, the saved state, and the promotion gate."""
import tempfile
import unittest
from pathlib import Path

from welding_ai.answering.answering import NOT_FOUND
from welding_ai.retrain import eta, gate
from welding_ai.retrain.state import JobState, reconcile


class Estimate(unittest.TestCase):
    def test_human(self):
        self.assertEqual(eta.human(45), "45 s")
        self.assertEqual(eta.human(7 * 60 + 20), "7 min")
        self.assertEqual(eta.human(6 * 3600 + 20 * 60), "6 h 20 min")
        self.assertEqual(eta.human(3600), "1 h 00 min")
        self.assertEqual(eta.human(-5), "0 s")

    def test_steps_are_rows_over_the_batch_rounded_up(self):
        self.assertEqual(eta.steps_for(3000), 375)
        self.assertEqual(eta.steps_for(3001), 376)
        self.assertEqual(eta.steps_for(1), 1)

    def test_the_default_estimate_matches_the_first_full_training(self):
        e = eta.estimate(3000, "full")
        self.assertEqual(e["steps"], 375)
        hours = e["seconds"] / 3600
        self.assertTrue(5.0 < hours < 8.0, hours)             # the owner's slides say about 6.5 h for 3,000 rows
        self.assertFalse(e["measured"])

    def test_a_test_run_takes_minutes_not_hours(self):
        self.assertLess(eta.estimate(24, "test")["seconds"], 40 * 60)
        self.assertEqual(eta.estimate(24, "test")["steps"], 2)

    def test_a_short_run_is_much_shorter_than_a_full_one(self):
        self.assertLess(eta.estimate(400, "short")["seconds"], eta.estimate(3400, "full")["seconds"] / 4)

    def test_measured_speed_replaces_the_default_and_a_bad_file_is_ignored(self):
        h = eta.History(Path(tempfile.mkdtemp()) / "h.json")
        self.assertEqual(h.measured()["sec_per_step"], eta.DEFAULT_SEC_PER_STEP)
        for s in (40.0, 44.0, 60.0):
            h.record(sec_per_step=s, export_s=300.0)
        self.assertEqual(h.measured()["sec_per_step"], 44.0)            # the median, so one slow run does not swing it
        self.assertEqual(h.measured()["export_s"], 300.0)
        self.assertTrue(eta.estimate(800, "full", h)["measured"])
        h.path.write_text("not json", encoding="utf-8")
        self.assertEqual(h.measured()["sec_per_step"], eta.DEFAULT_SEC_PER_STEP)

    def test_only_the_last_few_runs_are_kept(self):
        h = eta.History(Path(tempfile.mkdtemp()) / "h.json")
        for i in range(20):
            h.record(sec_per_step=float(i + 1))
        self.assertEqual(len(h._all()), eta.HISTORY_KEEP)


class State(unittest.TestCase):
    def setUp(self):
        self.s = JobState(Path(tempfile.mkdtemp()) / "sub" / "job.json", clock=lambda: 5.0)

    def test_empty_is_idle(self):
        self.assertEqual(self.s.read()["status"], "idle")

    def test_updates_merge_and_stamp_the_time(self):
        self.s.write({"status": "running", "stage": "training", "step": 3})
        self.s.update(step=4, loss=1.5)
        got = self.s.read()
        self.assertEqual((got["status"], got["step"], got["loss"], got["updated"]), ("running", 4, 1.5, 5.0))

    def test_a_corrupt_file_reads_as_idle_rather_than_crashing(self):
        self.s.write({"status": "running"})
        self.s.path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(self.s.read()["status"], "idle")

    def test_cancel_is_only_recorded_for_a_running_job(self):
        self.s.request_cancel()
        self.assertFalse(self.s.cancel_requested())
        self.s.write({"status": "running", "pid": 1})
        self.s.request_cancel()
        self.assertTrue(self.s.cancel_requested())

    def test_a_job_whose_process_vanished_is_marked_failed_and_says_the_old_model_stays(self):
        self.s.write({"status": "running", "pid": 99999, "stage": "training"})
        got = reconcile(self.s, alive=lambda pid: False)
        self.assertEqual(got["status"], "failed")
        self.assertIn("still in use", got["message"])
        self.s.write({"status": "running", "pid": 7})
        self.assertEqual(reconcile(self.s, alive=lambda pid: True)["status"], "running")
        self.s.write({"status": "finished", "pid": 7})
        self.assertEqual(reconcile(self.s, alive=lambda pid: False)["status"], "finished")        # a finished job is left alone


def rows(kinds):
    out = []
    for i, kind in enumerate(kinds):
        answer = NOT_FOUND if kind in gate.REFUSAL_KINDS else f"The value is {i + 10} mm [1]"
        out.append({"kind": kind, "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": f"EXCERPTS:\n[1] The value is {i + 10} mm\n\nQUESTION: q{i}"},
                                               {"role": "assistant", "content": answer}]})
    return out


KINDS = ["gold"] * 12 + ["wrong_passage"] * 6 + ["empty"] * 4


def model(**behaviour):
    """A fake model: by default it quotes the right number on gold rows and refuses on the others."""
    def chat(name, messages):
        user = messages[1]["content"]
        index = int(user.rsplit("QUESTION: q", 1)[1])
        kind = KINDS[index]
        if behaviour.get("fail"):
            return None
        if kind in gate.REFUSAL_KINDS:
            return NOT_FOUND if index not in behaviour.get("miss_refusals", ()) else f"The value is {index + 10} mm [1]"
        if index in behaviour.get("false_refusals", ()):
            return NOT_FOUND
        if index in behaviour.get("invent", ()):
            return f"The value is 999 mm [1]"
        if index in behaviour.get("source_line", ()):
            return f"The value is {index + 10} mm [1]\nSource: made_up.docx"
        return f"The value is {index + 10} mm [1]" + behaviour.get("extra", "")
    return chat


class Gate(unittest.TestCase):
    def measure(self, **b):
        return gate.score("m", rows(KINDS), chat=model(**b))

    def test_rouge_l(self):
        self.assertEqual(gate.rouge_l("the same words here", "the same words here"), 1.0)
        self.assertEqual(gate.rouge_l("alpha beta", "gamma delta"), 0.0)
        self.assertEqual(gate.rouge_l("", "x"), 0.0)
        self.assertAlmostEqual(gate.rouge_l("a b c d", "a b x d"), 0.75)
        self.assertEqual(gate.rouge_l("value [1]", "value"), 1.0)                    # citation markers are not words

    def test_measures_on_a_clean_model(self):
        m = self.measure()
        self.assertEqual((m["gold"], m["refusal_rows"], m["errors"]), (12, 10, 0))
        self.assertEqual((m["invented"], m["false_refusal"], m["refusal_recall"], m["source_line"]), (0.0, 0.0, 1.0, 0.0))
        self.assertGreater(m["quality"], 0.99)

    def test_each_failure_shows_in_its_own_measure(self):
        self.assertEqual(self.measure(invent=(0, 1, 2))["invented"], 0.25)
        self.assertEqual(self.measure(false_refusals=(3,))["false_refusal"], round(1 / 12, 4))
        self.assertEqual(self.measure(miss_refusals=(12, 13))["refusal_recall"], 0.8)
        self.assertEqual(self.measure(source_line=(4, 5))["source_line"], round(2 / 22, 4))

    def test_a_model_that_cannot_answer_is_counted_not_crashed_on(self):
        m = self.measure(fail=True)
        self.assertEqual((m["errors"], m["answered"]), (22, 0))
        self.assertIn("failed to answer", " ".join(gate.compare(self.measure(), m)["reasons"]))

    def test_a_better_model_passes_and_says_where(self):
        active = self.measure(invent=(0, 1), miss_refusals=(12, 13, 14, 15))
        candidate = self.measure()
        verdict = gate.compare(active, candidate)
        self.assertTrue(verdict["passed"], verdict["reasons"])
        self.assertIn("fewer invented numbers", verdict["improved"])
        self.assertIn("refusing wrong or missing passages", verdict["improved"])

    def test_an_identical_model_is_not_promoted(self):
        verdict = gate.compare(self.measure(), self.measure())
        self.assertFalse(verdict["passed"])
        self.assertIn("no measurable improvement", verdict["reasons"][0])

    def test_each_safety_rule_blocks_on_its_own(self):
        base = self.measure(miss_refusals=(12, 13, 14))                              # a weaker active model, so the candidate does improve somewhere
        cases = {"invented numbers": dict(invent=(0, 1)), "false refusals": dict(false_refusals=(0, 1)), "Source: lines": dict(source_line=tuple(range(0, 6)))}
        for label, bad in cases.items():
            verdict = gate.compare(base, self.measure(**bad))
            self.assertFalse(verdict["passed"], label)
            self.assertTrue(any(label.split()[0] in r for r in verdict["reasons"]), (label, verdict["reasons"]))

    def test_refusing_less_often_blocks(self):
        verdict = gate.compare(self.measure(), self.measure(miss_refusals=(12, 13)))
        self.assertFalse(verdict["passed"])
        self.assertTrue(any("refuses wrong or missing passages less often" in r for r in verdict["reasons"]))

    def test_small_samples_cannot_pass_unless_it_is_only_a_test(self):
        few = gate.score("m", rows(KINDS[:6]), chat=model())
        self.assertIn("too few rows", " ".join(gate.compare(few, few)["reasons"]))
        small = [r for r in rows(KINDS) if r["kind"] != "empty"][:10]
        self.assertTrue(any("too few" in r for r in gate.compare(gate.score("m", small, chat=model()), gate.score("m", small, chat=model(extra=" ok")))["reasons"]))

    def test_sampling_keeps_every_kind_and_is_reproducible(self):
        pool = rows(["gold"] * 60 + ["wrong_passage"] * 20 + ["empty"] * 10 + ["contrastive"] * 10)
        a, b = gate.sample(pool, 20, seed=3), gate.sample(pool, 20, seed=3)
        self.assertEqual([r["messages"][1]["content"] for r in a], [r["messages"][1]["content"] for r in b])
        self.assertEqual({r["kind"] for r in a}, {"gold", "wrong_passage", "empty", "contrastive"})
        self.assertLessEqual(len(a), 20)
        self.assertEqual(len(gate.sample(pool, None)), 100)

    def test_progress_is_reported(self):
        seen = []
        gate.score("m", rows(KINDS), chat=model(), progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen[0], (1, 22))
        self.assertEqual(seen[-1], (22, 22))


if __name__ == "__main__":
    unittest.main()
