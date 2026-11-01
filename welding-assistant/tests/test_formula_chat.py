"""The formula agent inside the chat: a calculation request is answered by the calculator, everything else goes to the documents."""
import json
import re
import unittest

from welding_ai import paths
from welding_ai.core.config import Config
from welding_ai.formulas.chat import FormulaChat
from welding_ai.formulas.store import FormulaStore
from welding_ai.orchestration.harness import Harness
from welding_ai.retrieval import retrieval


def text_of(events):
    return "\n".join(e["t"] for e in events if e["type"] == "token")


class ChatCalculator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = retrieval.load()
        store = FormulaStore(":memory:")
        store.sync_documents(json.loads(paths.FORMULAS_SEED.read_text(encoding="utf-8")))
        squash = lambda t: re.sub(r"\s+", " ", t)
        locate = lambda doc, anchor: next((i for i, c in enumerate(cls.index.chunks) if c["source"] == doc and squash(anchor) in squash(c["text"])), None)
        cls.h = Harness(index=cls.index, cfg=Config(use_cache=False))
        cls.h.formulas = FormulaChat(store.usable, locate)

    def ask(self, question, history=None, mode="verbatim"):
        return list(self.h.stream(question, history=history or [], mode=mode))

    def test_a_calculation_with_numbers_is_answered_by_the_calculator(self):
        ev = self.ask("What is the heat input at 29 V, 290 A and 400 mm/min with an efficiency of 0.8?")
        done = ev[-1]
        self.assertEqual(done["route"], "formula")
        self.assertEqual(done["provenance"]["kind"], "calculated")
        text = text_of(ev)
        self.assertIn("1.26 kJ/mm", text)                                    # arc energy: the document's own worked example (doc 22, section 16)
        self.assertIn("1.01 kJ/mm", text)                                    # heat input with the 0.8 factor
        self.assertIn("Numbers read from your question", text)
        self.assertIn("Check them", text)

    def test_the_answer_cites_the_passage_the_formula_came_from(self):
        ev = self.ask("calculate the heat input for 28 V, 260 A and 350 mm/min")
        sources = next(e for e in ev if e["type"] == "sources")
        self.assertEqual(sources["route"], "formula")
        self.assertEqual(len(sources["sources"]), 1)
        self.assertTrue(sources["sources"][0]["source"].startswith("22_Shared_Physics"))
        cite = next(e for e in ev if e["type"] == "cite")
        self.assertEqual(cite["items"][0]["chunk"], sources["sources"][0]["chunk_id"])
        table = next(e for e in ev if e["type"] == "table")
        self.assertEqual(table["chunk"], sources["sources"][0]["chunk_id"])
        self.assertEqual([r[0] for r in table["rows"]], ["V", "I", "v"])

    def test_a_result_the_inputs_cannot_give_yet_says_what_is_missing(self):
        text = text_of(self.ask("calculate the heat input for 28 V, 260 A and 350 mm/min"))
        self.assertIn("1.25 kJ/mm", text)
        self.assertIn("Heat input needs", text)                              # no efficiency was given: it is asked for, never assumed

    def test_missing_numbers_are_asked_for_with_an_example(self):
        ev = self.ask("Calculate the heat input at 28 V")
        self.assertEqual(ev[-1]["route"], "formula_ask")
        self.assertIsNone(ev[-1]["provenance"])
        text = text_of(ev)
        self.assertIn("still need", text)
        self.assertIn("I = 260", text)                                       # the example comes from the document's own worked example

    def test_a_reply_that_only_gives_numbers_continues_the_calculation(self):
        history = [{"role": "user", "content": "Calculate the heat input at 28 V"}, {"role": "assistant", "content": "To calculate this I still need ..."}]
        ev = self.ask("I = 260, v = 350", history)
        self.assertEqual(ev[-1]["route"], "formula")
        self.assertIn("1.25 kJ/mm", text_of(ev))

    def test_a_reply_after_two_partial_replies_still_works(self):
        history = [{"role": "user", "content": "Calculate the heat input at 28 V"}, {"role": "assistant", "content": "x"},
                   {"role": "user", "content": "I = 260"}, {"role": "assistant", "content": "y"}]
        ev = self.ask("v = 350", history)
        self.assertEqual(ev[-1]["route"], "formula")
        self.assertIn("1.25 kJ/mm", text_of(ev))

    def test_a_new_topic_after_a_calculation_is_not_read_as_a_continuation(self):
        history = [{"role": "user", "content": "Calculate the heat input at 28 V"}, {"role": "assistant", "content": "To calculate this I still need ..."}]
        for q in ("calculate t8/5 cooling time for 20 mm plate", "what preheat do I need for 25 mm plate", "How is dilution of 30 percent bad?"):
            ev = self.ask(q, history)
            self.assertNotEqual(ev[-1].get("formula_id"), "arc_energy_heat_input", q)

    def test_a_reply_in_words_continues_the_calculation(self):
        history = [{"role": "user", "content": "Calculate the heat input at 28 V"}, {"role": "assistant", "content": "x"}]
        ev = self.ask("the welding current is 260 and the travel speed is 350", history)
        self.assertEqual(ev[-1]["route"], "formula")
        self.assertIn("1.25 kJ/mm", text_of(ev))

    def test_two_matching_formulas_ask_which_one(self):
        ev = self.ask("calculate the carbon equivalent")
        clarify = next(e for e in ev if e["type"] == "clarify")
        labels = [o["label"] for o in clarify["options"]]
        self.assertGreaterEqual(len(labels), 2)
        self.assertTrue(any("IIW" in label for label in labels))
        self.assertTrue(any("AWS" in label for label in labels))

    def test_choosing_one_with_the_numbers_gives_the_result(self):
        ev = self.ask("calculate the carbon equivalent")
        option = next(o for o in next(e for e in ev if e["type"] == "clarify")["options"] if "IIW" in o["label"])
        ev = self.ask(option["query"] + " C = 0.2, Mn = 1.2")
        self.assertEqual(ev[-1]["route"], "formula")
        self.assertEqual(ev[-1]["formula_id"], "ce_iiw")
        self.assertIn("0.40", text_of(ev))                                   # 0.2 + 1.2 / 6

    def test_a_plain_question_about_a_formula_goes_to_the_documents(self):
        for q in ("What is heat input?", "What causes porosity in GMAW?", "Why does expulsion happen in spot welding?"):
            self.assertFalse(self.ask(q)[-1]["route"].startswith("formula"), q)

    def test_a_calculation_the_documents_have_no_formula_for_is_not_invented(self):
        for q in ("calculate t8/5 cooling time for 20 mm plate", "calculate the preheat temperature for 25 mm steel"):
            self.assertFalse(self.ask(q)[-1]["route"].startswith("formula"), q)

    def test_summary_on_says_the_ai_was_not_needed(self):
        done = self.ask("calculate the heat input for 28 V, 260 A and 350 mm/min", mode="answer")[-1]
        self.assertEqual(done["route"], "formula")
        self.assertIn("needed no AI", done["provenance"]["note"])

    def test_out_of_range_numbers_are_refused_not_calculated(self):
        ev = self.ask("calculate the heat input for -28 V, 260 A and 350 mm/min")
        self.assertNotIn("kJ/mm**", text_of(ev))
        self.assertNotEqual(ev[-1]["route"], "formula")

    def test_without_a_formula_source_the_pipeline_is_unchanged(self):
        plain = Harness(index=self.index, cfg=Config(use_cache=False))
        done = list(plain.stream("calculate the heat input for 28 V, 260 A and 350 mm/min", mode="verbatim"))[-1]
        self.assertFalse(done["route"].startswith("formula"))


class ValueReplies(unittest.TestCase):
    def test_a_reply_that_only_gives_values_is_readable_input(self):
        from welding_ai.understanding import guard
        for text in ("v = 350", "I = 260", "C = 0.2, Mn = 1.2", "V=28, I=260, v=350", "eta: 0.8", "t = 1,4 mm"):
            self.assertFalse(guard.is_odd_input(text), text)

    def test_noise_and_code_are_still_refused(self):
        from welding_ai.understanding import guard
        for text in ("350", "= 350", "x = 1; DROP TABLE users", "aaaaaaaa", "qwrtpsdf zxcvbnm", "12345 67890"):
            self.assertTrue(guard.is_odd_input(text), text)


if __name__ == "__main__":
    unittest.main()
