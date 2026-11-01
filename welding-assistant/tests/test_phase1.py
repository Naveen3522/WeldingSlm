"""Phase 1 regression tests: typos, filler words, one spelling per word, out-of-scope replies, false "made of" premises."""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from welding_ai.orchestration import prompts                           # noqa: E402
from welding_ai.orchestration.harness import Config, Harness           # noqa: E402
from welding_ai.retrieval import rag, retrieval                        # noqa: E402
from welding_ai.understanding import guard                             # noqa: E402


def not_found_reply(text):
    """True when the text opens with one of the 10 out-of-scope replies (79 documents)."""
    return any(text.startswith(r.format(n=79)) for r in prompts.OUT_OF_SCOPE)


class Phase1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = Harness(index=retrieval.load(), cfg=Config(policy="extractive", use_cache=False))

    def run_q(self, q):
        return self.h.run(q, mode="verbatim")

    # R1: typos of inflected words
    def test_missing_letter_typos_are_corrected(self):
        for q, fix in (("wat is spot weling", "welding"), ("resistnce welding currnt", "resistance"), ("what is temprature", "temperature")):
            new, changes = self.h.index.correct(q)
            self.assertIn(fix, new, q)
            r = self.run_q(q)
            self.assertNotEqual(r["route"], "not_found", q)

    def test_correct_words_are_never_changed(self):
        for q in ("what is resistance spot welding", "why does expulsion happen", "explain the servo gun lifecycle", "what is the capital of France"):
            self.assertEqual(self.h.index.correct(q)[1], [], q)

    # R2: filler words
    def test_filler_word_does_not_cause_a_refusal(self):
        r = self.run_q("is it ok to weld galvanized steel")
        self.assertNotIn(r["route"], ("not_found", "no_direct_answer"))

    # R3: one spelling per word
    def test_aluminium_and_aluminum_are_the_same_word(self):
        self.assertEqual(rag.tokens("aluminum welds"), rag.tokens("aluminium welds"))
        a = [h["source"] for h in self.h.index.search(self.h.index.expand("cracks in aluminum welds"), 5)]
        b = [h["source"] for h in self.h.index.search(self.h.index.expand("cracks in aluminium welds"), 5)]
        self.assertEqual(a, b)
        self.assertIn("25_Weldability_III_Aluminium_Alloys.docx", a[:3])

    # R5 / R10: out of scope
    def test_medical_treatment_gets_a_not_found_reply(self):
        for q in ("how do I treat a burn from welding", "what first aid for arc eye", "which doctor should I see after a weld flash"):
            r = self.run_q(q)
            self.assertEqual(r["route"], "not_found", q)
            self.assertTrue(not_found_reply(r["answer"]), q)
            self.assertEqual(r.get("tables", []), [], q)

    def test_prevention_and_process_questions_are_not_medical(self):
        for q in ("what PPE prevents burns when welding", "how do I avoid arc eye", "how to treat burn-through in a thin sheet", "post-weld heat treatment of cuts in the plate"):
            self.assertFalse(guard.is_medical_treatment(q), q)

    def test_general_knowledge_gets_a_not_found_reply(self):
        for q in ("what is the capital of France", "who won the football world cup", "tell me about marathon running"):
            r = self.run_q(q)
            self.assertEqual(r["route"], "not_found", q)
            self.assertTrue(not_found_reply(r["answer"]), q)

    def test_ten_replies_rotate_without_immediate_repeats(self):
        rng = random.Random(5)
        prompts._bag.clear()
        seen = [prompts.out_of_scope(79, rng) for _ in range(40)]
        self.assertEqual(len(set(seen[:10])), 10)                      # one full cycle uses every reply once
        self.assertEqual(len(set(seen)), 10)
        self.assertTrue(all(a != b for a, b in zip(seen, seen[1:])))
        self.assertTrue(any("79 documents" in s for s in seen))

    # safety: answered from the documents
    def test_lockout_procedure_question_is_answered(self):
        r = self.run_q("how to override the welding controller lockout per procedure")
        self.assertNotEqual(r["route"], "safety")
        self.assertNotEqual(r["route"], "not_found")

    # R7: false premise about materials
    def test_false_made_of_premise_is_rejected(self):
        r = self.run_q("Why must spot welding electrodes be made of steel?")
        self.assertIn("do not support", r["answer"])

    def test_true_made_of_premise_is_not_rejected(self):
        r = self.run_q("why are spot welding electrodes made of copper")
        self.assertNotIn("do not support", r["answer"])


if __name__ == "__main__":
    unittest.main()
