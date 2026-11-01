"""The label under every answer says where its wording came from, and an AI answer always arrives with the document text beside it."""
import unittest
from unittest import mock

from welding_ai.answering import answering
from welding_ai.core.config import Config
from welding_ai.orchestration import harness as harness_mod
from welding_ai.orchestration import provenance
from welding_ai.orchestration.harness import Harness
from welding_ai.retrieval import retrieval

QUESTION = "What does E7018 mean?"                         # answered from the passages, so the model gets a turn
SENTENCE = "E7018 is the low-hydrogen structural default, and it only remains low-hydrogen if it is stored correctly."


def fake_model(text):
    def stream(model, messages, opts, timeout):
        yield "token", text
        yield "stats", {}
    return stream


def broken_model(model, messages, opts, timeout):
    raise harness_mod.LLMError("model offline")
    yield                                                   # (makes this a generator)


class Describe(unittest.TestCase):
    def kind(self, route, source=None, ai=False):
        d = provenance.describe(route, source, ai)
        return d and d["kind"]

    def test_every_route_has_the_right_kind(self):
        cases = {"llm": "ai", "llm_refusal": "not_covered", "fallback_extractive": "ai_failed", "extractive_degraded": "ai_down",
                 "faq": "verified", "composed": "quoted", "extractive": "quoted", "formula": "calculated"}
        for route, kind in cases.items():
            self.assertEqual(self.kind(route), kind, route)

    def test_routes_that_need_no_label(self):
        for route in ("not_found", "no_direct_answer", "search_only", "clarify", "intent", "guard_empty", "guard_injection", "odd_input", "formula_ask", None):
            self.assertIsNone(provenance.describe(route), route)

    def test_a_cached_answer_keeps_its_original_label(self):
        self.assertEqual(self.kind("cache", "llm"), "ai")
        self.assertEqual(self.kind("cache", "fallback_extractive"), "ai_failed")
        self.assertEqual(self.kind("cache", "extractive"), "quoted")
        self.assertIsNone(provenance.describe("cache", "something_else"))

    def test_only_ai_answers_carry_the_warning_tone(self):
        self.assertEqual(provenance.describe("llm")["tone"], "warn")
        for route in ("faq", "composed", "extractive", "formula"):
            self.assertEqual(provenance.describe(route)["tone"], "ok")

    def test_summary_on_but_no_ai_needed_is_said(self):
        self.assertIn("needed no AI", provenance.describe("faq", None, True)["note"])
        self.assertIn("needed no AI", provenance.describe("composed", None, True)["note"])
        self.assertEqual(provenance.describe("faq", None, False)["note"], "")
        self.assertEqual(provenance.describe("llm", None, True)["note"], "")          # the AI was used: nothing to explain
        self.assertEqual(provenance.describe("fallback_extractive", None, True)["note"], "")

    def test_the_ai_label_says_it_can_still_be_wrong(self):
        detail = provenance.describe("llm")["detail"].lower()
        self.assertIn("can still", detail)
        self.assertIn("check", detail)


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = retrieval.load()

    def setUp(self):
        cfg = Config(use_cache=False)
        cfg.agree_min = 0                                   # these tests are about the label, not about the agreement check
        self.h = Harness(index=self.index, cfg=cfg)
        self.real_stream = harness_mod.llm_stream

    def tearDown(self):
        harness_mod.llm_stream = self.real_stream

    def events(self, mode, model=None, question=QUESTION, verify_ok=False):
        if model:
            harness_mod.llm_stream = model
        patch = mock.patch.object(answering, "verify", return_value={"ok": True, "refusal": False, "reasons": [], "numbers_missing": []}) if verify_ok else mock.MagicMock()
        with patch:
            return list(self.h.stream(question, mode=mode, model="welding-slm-q8"))

    @staticmethod
    def types(events):
        return [e["type"] for e in events]

    def test_exact_mode_never_calls_the_model_and_is_labelled_quoted(self):
        harness_mod.llm_stream = broken_model                # would raise if it were called
        ev = self.events("verbatim")
        done = ev[-1]
        self.assertEqual(done["type"], "done")
        self.assertNotIn("exact", self.types(ev))
        self.assertEqual(done["provenance"]["kind"], "quoted")
        self.assertEqual(done["provenance"]["note"], "")

    def test_summary_mode_sends_the_document_text_before_the_ai_text(self):
        ev = self.events("answer", fake_model(SENTENCE), verify_ok=True)
        t = self.types(ev)
        self.assertIn("exact", t)
        self.assertLess(t.index("exact"), t.index("token"))   # the reader sees the document first
        self.assertEqual(ev[-1]["route"], "llm")
        self.assertEqual(ev[-1]["provenance"]["kind"], "ai")
        exact = next(e for e in ev if e["type"] == "exact")
        self.assertTrue(exact["text"].strip())
        self.assertNotEqual(exact["text"].strip(), SENTENCE)   # the AI text is not just a copy of the document text

    def test_an_answer_that_fails_the_checks_falls_back_and_says_so(self):
        ev = self.events("answer", fake_model("Aluminium is best welded with the moon at 9999 amps."))
        done = ev[-1]
        self.assertEqual(done["route"], "fallback_extractive")
        self.assertEqual(done["provenance"]["kind"], "ai_failed")
        self.assertIn("replace", self.types(ev))              # the page swaps the AI text for the document text

    def test_a_model_that_cannot_be_reached_falls_back_and_says_so(self):
        ev = self.events("answer", broken_model)
        done = ev[-1]
        self.assertEqual(done["route"], "extractive_degraded")
        self.assertEqual(done["provenance"]["kind"], "ai_down")
        self.assertIn("error", self.types(ev))

    def test_a_model_refusal_is_labelled_not_covered(self):
        ev = self.events("answer", fake_model(answering.NOT_FOUND))
        self.assertEqual(ev[-1]["route"], "llm_refusal")
        self.assertEqual(ev[-1]["provenance"]["kind"], "not_covered")

    def test_a_verified_faq_with_summary_on_says_no_ai_was_needed(self):
        ev = self.events("answer", broken_model, question="What causes porosity in GMAW and how is it corrected?")
        done = ev[-1]
        self.assertEqual(done["route"], "faq")
        self.assertEqual(done["provenance"]["kind"], "verified")
        self.assertIn("needed no AI", done["provenance"]["note"])
        self.assertNotIn("error", self.types(ev))              # the model was never asked

    def test_every_done_event_in_a_refusal_path_is_still_well_formed(self):
        ev = self.events("answer", question="What is the best pizza topping?")
        self.assertEqual(ev[-1]["type"], "done")
        self.assertIn("provenance", ev[-1])                  # present for every answer; None where no label applies
        self.assertIsNone(ev[-1]["provenance"])

    def test_a_cached_ai_answer_also_arrives_with_the_document_text(self):
        import os
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "cache.json")
        cfg = Config(use_cache=True, cache_path=path)
        cfg.agree_min = 0
        h = Harness(index=self.index, cfg=cfg)
        harness_mod.llm_stream = fake_model(SENTENCE)
        ok = {"ok": True, "refusal": False, "reasons": [], "numbers_missing": []}
        with mock.patch.object(answering, "verify", return_value=ok):
            first = list(h.stream(QUESTION, mode="answer", model="welding-slm-q8"))
            second = list(h.stream(QUESTION, mode="answer", model="welding-slm-q8"))
        self.assertEqual(first[-1]["route"], "llm")
        self.assertEqual(second[-1]["route"], "cache")
        types = [e["type"] for e in second]
        self.assertIn("exact", types)
        self.assertLess(types.index("exact"), types.index("token"))
        self.assertEqual(second[-1]["provenance"]["kind"], "ai")

    def test_the_evaluation_helper_ignores_the_new_event(self):
        harness_mod.llm_stream = fake_model(SENTENCE)
        with mock.patch.object(answering, "verify", return_value={"ok": True, "refusal": False, "reasons": [], "numbers_missing": []}):
            result = self.h.run(QUESTION, mode="answer", model="welding-slm-q8")
        self.assertEqual(result["answer"], SENTENCE)         # exact text is not mixed into the answer the evaluation scores
        self.assertEqual(result["route"], "llm")


if __name__ == "__main__":
    unittest.main()
