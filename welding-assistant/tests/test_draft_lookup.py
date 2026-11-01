"""Chat answers from a draft it is asked about, and says when a draft holds something about any question."""
import unittest

from welding_ai.forms.lookup import DraftLookup
from welding_ai.forms.store import FormStore


class Lookup(unittest.TestCase):
    def setUp(self):
        self.forms = FormStore(":memory:")
        heads = ["Field", "Value"]
        tpl = self.forms.save_template(None, "Gap sheet", heads, [["Gap we leave", ""], ["Electrode force", ""]])
        self.forms.create_draft("Line 4 setup", tpl["id"], heads, [["Gap we leave", "2.5 mm at the left gun"], ["Electrode force", ""]])
        self.lookup = DraftLookup(self.forms)

    def text(self, events):
        return " ".join(e.get("t", "") for e in events if e.get("type") == "token")

    def test_a_cell_is_quoted_from_the_draft_it_names(self):
        out = self.lookup.claim("What is the gap we leave in the Line 4 setup draft?")
        self.assertIn("2.5 mm at the left gun", self.text(out))
        self.assertIn("Line 4 setup", self.text(out))
        self.assertEqual(out[-1]["route"], "drafts")
        self.assertEqual(out[-1]["provenance"]["kind"], "draft")

    def test_a_renamed_column_is_the_one_asked_for(self):
        heads = ["Parameter", "Setting", "Remarks"]
        tpl = self.forms.save_template(None, "Setup", heads, [["Weld time", "", ""]])
        self.forms.create_draft("Cell 9", tpl["id"], heads, [["Weld time", "12 cycles", "check weekly"]])
        text = self.text(self.lookup.claim("What are the Remarks for weld time in the Cell 9 draft?"))
        self.assertIn("check weekly", text)
        self.assertNotIn("12 cycles", text)
        self.assertIn("12 cycles", self.text(self.lookup.claim("What is the weld time in the Cell 9 draft?")))

    def test_a_named_draft_without_a_matching_field_is_shown_whole(self):
        out = self.lookup.claim("show the Line 4 setup draft")
        self.assertIn("1 of 2 cells", self.text(out))
        self.assertTrue([e for e in out if e["type"] == "table"])

    def test_the_drafts_are_listed(self):
        self.assertIn("1 draft", self.text(self.lookup.claim("which drafts are there?")))

    def test_an_ordinary_question_is_left_to_the_pipeline(self):
        self.assertIsNone(self.lookup.claim("What is heat input?"))
        self.assertIsNone(self.lookup.claim("What is the gap we leave?"))                # no draft named: advice, not an answer

    def test_advice_names_the_drafts_that_hold_the_field_and_ignores_empty_cells(self):
        line = self.lookup.advice("What gap do we leave at the gun?")
        self.assertIn("1 draft", line)
        self.assertIn("Line 4 setup", line)
        self.assertEqual(self.lookup.advice("What is the electrode force?"), "")        # that cell is empty
        self.assertEqual(self.lookup.advice("What is heat input?"), "")


if __name__ == "__main__":
    unittest.main()
