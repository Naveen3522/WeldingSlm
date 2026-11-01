"""Regressions for the answer-quality fixes: ask-words are not subjects, table rows keep every column, "pointers" is a list,
"stop" is not a defect, and a summary that invents a meaning or glues two table rows together is rejected."""
import unittest

from welding_ai.answering import answering, composer
from welding_ai.core.config import Config
from welding_ai.orchestration.harness import Harness
from welding_ai.understanding import planner

TENSILE = "a material tensile strength breaks what is the meaning and what action needs to taken place explain in pointers?"


class PlannerFixes(unittest.TestCase):
    def test_pointers_is_a_list(self):
        self.assertEqual(planner.analyze(TENSILE)["format"], "list")
        self.assertEqual(planner.analyze("explain porosity in pointers")["format"], "list")

    def test_meaning_and_action_are_not_subjects_or_parts(self):
        p = planner.analyze(TENSILE)
        self.assertEqual(p["entity"], "")
        self.assertEqual(len(p["parts"]), 1)                       # "what is the meaning" / "what action ..." join the first part
        self.assertEqual(p["attrs"], ["meaning", "action"])
        self.assertEqual(p["subject"], ["material", "tensile", "strength", "break"])

    def test_stop_is_an_action_not_a_subject(self):
        p = planner.analyze("how do I stop porosity")
        self.assertEqual(p["subject"], ["porosity"])
        self.assertIn("action", p["attrs"])

    def test_a_real_second_question_is_still_a_part(self):
        p = planner.analyze("what is WPS, what does it contain and what does it lack")
        self.assertGreaterEqual(len(p["parts"]), 2)


class ClaimCheck(unittest.TestCase):
    CTX = ("[1] (document: d.docx | section: s)\n"
           "| Problem | Meaning | Action |\n"
           "| Tensile test breaks in the weld metal below requirement | Filler undermatched or deposit chemistry wrong for the requirement | Review filler selection and dilution; verify the consumable actually used |\n"
           "| Tensile breaks in the HAZ below requirement | HAZ softening welded above the heat input ceiling | Reduce heat input and interpass; requalify within the producer's limits |\n")

    def test_invented_meaning_is_rejected(self):
        ok, why = answering.verify_claims("The tensile break means the joint failed completely.", self.CTX, "tensile strength breaks meaning")
        self.assertFalse(ok)

    def test_blended_rows_are_rejected(self):
        ok, why = answering.verify_claims("Review filler selection and dilution, and reduce heat input and interpass.", self.CTX, "tensile breaks action")
        self.assertFalse(ok)
        self.assertIn("different table rows", why)

    def test_one_row_is_accepted(self):
        ok, why = answering.verify_claims("A weld metal break means the filler is undermatched or its chemistry is wrong; review filler selection and dilution.", self.CTX, "tensile breaks")
        self.assertTrue(ok, why)


class EngineFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = Config.load()
        cfg.use_cache = False
        cls.h = Harness(cfg=cfg)

    def test_tensile_rows_keep_meaning_and_action(self):
        for q in (TENSILE, TENSILE.replace(" explain in pointers?", "")):
            r = self.h.run(q, mode="verbatim")
            self.assertEqual(r["route"], "composed", q)
            text = r["answer"]
            self.assertIn("Filler undermatched", text)
            self.assertIn("Review filler selection and dilution", text)
            self.assertIn("Reduce heat input", text)
            self.assertNotIn("do not cover", text)

    def test_simple_card_is_bulleted_per_row(self):
        s = self.h.run(TENSILE, mode="verbatim")["simple"]["blocks"][0]["md"]
        self.assertIn("**Tensile test breaks in the weld metal below requirement**", s)
        self.assertIn("- Meaning: Filler undermatched", s)
        self.assertIn("- Action: Review filler selection and dilution", s)
        self.assertNotIn("Here is the list", s)

    def test_stop_is_not_a_heading(self):
        r = self.h.run("how do I stop porosity", mode="verbatim")
        self.assertNotIn("**Stop**", r["answer"])

    def test_composer_off_switch_keeps_old_output(self):
        self.h.cfg.use_simple = False
        try:
            self.assertNotIn("simple", self.h.run(TENSILE, mode="verbatim"))
        finally:
            self.h.cfg.use_simple = True


if __name__ == "__main__":
    unittest.main()
