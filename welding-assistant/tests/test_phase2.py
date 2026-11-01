"""Phase 2 regression tests: the question's wording, layout and intent decide which part of the documents is shown."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from welding_ai.orchestration import prompts                           # noqa: E402
from welding_ai.orchestration.harness import Config, Harness           # noqa: E402
from welding_ai.retrieval import retrieval                             # noqa: E402
from welding_ai.understanding import planner                           # noqa: E402


class Understanding(unittest.TestCase):
    def test_polite_lead_ins_do_not_change_the_question(self):
        for polite, plain in (("please tell me what is Shunting", "what is Shunting"),
                              ("can you please tell me what are the Case-Pattern Quick Reference", "what are the Case-Pattern Quick Reference"),
                              ("I want to know what is alarm", "what is alarm"),
                              ("hey tell me what are the different types of welds", "what are the different types of welds")):
            a, b = planner.analyze(polite), planner.analyze(plain)
            self.assertEqual((a["intent"], a["entity"], a["format"]), (b["intent"], b["entity"], b["format"]), polite)

    def test_tell_me_about_is_still_an_explanation(self):
        self.assertEqual(planner.analyze("tell me about spot welding")["intent"], "explain")

    def test_bracketed_acronym_is_a_plain_definition(self):
        p = planner.analyze("what is resistance spot welding (RSW)")
        self.assertEqual((p["intent"], p["entity"], p["pure_define"]), ("define", "RSW", True))
        self.assertEqual(p["parts"][0]["own"], [])


class Answers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = Harness(index=retrieval.load(), cfg=Config(policy="extractive", use_cache=False))

    def run_q(self, q):
        return self.h.run(q, mode="verbatim")

    def cells(self, r):
        return " ".join(" ".join(" ".join(row) for row in t["rows"]) for t in r.get("tables", []))

    # a table asked for by its own title
    def test_table_asked_by_title(self):
        for q, header0, needle in (("what are the Case-Pattern Quick Reference", "Case", "No weld at all"),
                                   ("secondary-circuit troubleshooting quick reference", "Symptom", "Current below expected"),
                                   ("what are the AC vs. MFDC: A Real Technical Comparison", "Aspect", "Basic architecture"),
                                   ("the minimum useful weld-event schema", "Term", "weld_event_id")):
            r = self.run_q(q)
            self.assertEqual(r["tables"][0]["header"][0], header0, q)
            self.assertIn(needle, self.cells(r), q)

    def test_same_title_in_two_documents_shows_both(self):
        r = self.run_q("what are the Core Terminology Quick Reference")
        self.assertGreaterEqual(len(r["tables"]), 2)

    def test_table_layout_asked_is_respected(self):
        r = self.run_q("Case-Pattern Quick Reference as a list")
        self.assertEqual(r.get("tables", []), [])
        self.assertIn("- **", r["answer"])

    def test_definition_is_not_hijacked_by_a_table_with_a_similar_title(self):
        r = self.run_q("what is friction stir welding")
        self.assertEqual(r.get("tables", []), [])
        self.assertIn("rotating tool", r["answer"])

    # one cell of a table: "what is the <column> for <row>"
    def test_cell_lookup(self):
        for q, needle in (("what is the cause for Porosity in the weld", "Moisture, oil, paint"),
                          ("what is the laser for Materials", "Most metals and non-metals"),
                          ("what is the controls for Weld current", "Melting rate and depth"),
                          ("what is the why for Corrosion-resistant overlay", "Low dilution, high coverage rate")):
            r = self.run_q(q)
            self.assertIn(needle, r["answer"], q)
            self.assertEqual(r.get("tables", []), [], q)

    # glossary
    def test_glossary_definition_is_the_documents_own(self):
        for q, needle in (("please tell me what is Shunting", "unintended"), ("please tell me what is Adaptive control", "Closed-loop control"),
                          ("what is Reisstance spot welding (RSW)", "faying surfaces")):
            self.assertIn(needle, self.run_q(q)["answer"], q)

    def test_what_is_wps_is_unchanged(self):
        self.assertIn("Welding Procedure Specification", self.run_q("what is WPS")["answer"])

    # section named by its title
    def test_section_named_by_title(self):
        r = self.run_q("explain The Reference-Curve Lifecycle")
        self.assertIn("The Reference-Curve Lifecycle", r["answer"])

    # first-letter typo
    def test_first_letter_typo(self):
        self.assertEqual(self.h.index.correct("the character for No-nvacuum")[0], "the character for No-vacuum")

    # brand and vendor requests: the documents name none
    def test_brand_request_gets_a_not_found_reply(self):
        for q in ("Which welding helmet brand has the fastest auto-darkening lens?", "what is the best manufacturer of servo guns"):
            r = self.run_q(q)
            self.assertEqual(r["route"], "not_found", q)
            self.assertTrue(any(r["answer"].startswith(x.format(n=79)) for x in prompts.OUT_OF_SCOPE), q)
            self.assertIn("no brands", r["answer"])

    def test_vendor_naming_question_is_not_a_brand_request(self):
        self.assertNotEqual(self.run_q("what is a gun data module")["route"], "not_found")


if __name__ == "__main__":
    unittest.main()
