"""The plain-English composer: shape follows the question, the rewrite must be in the chunks, and a bad rewrite is refused."""
import unittest

from welding_ai.answering import composer
from welding_ai.answering.evidence import Answer


class FakeIndex:
    def __init__(self, texts):
        self.chunks = [{"text": t, "source": "doc.docx", "heading": "H"} for t in texts]


def plan(intent="topic", fmt=None, length=None, terms=(), q="question"):
    return {"question": q, "intent": intent, "format": fmt, "length": length, "terms": list(terms)}


def answer(md=None, table=None, chunk_text="", kind="topic"):
    a = Answer(kind)
    if md:
        a.text(md)
    if table:
        a.table(*table, chunk=0)
    a.cite("doc.docx", "H", 0)
    return a, FakeIndex([chunk_text or md or ""])


class ComposerTests(unittest.TestCase):
    def test_paragraph_becomes_steps(self):
        md = "Switch off the machine. Wait for the tip to cool. Remove the tip with the puller. Fit the new tip."
        a, idx = answer(md)
        r = composer.compose(plan("procedure", "steps"), a, idx)
        self.assertTrue(r["fit"])
        self.assertEqual(len(__import__("re").findall(r"(?m)^\d+\.", composer.flat(r["blocks"]))), 4)
        self.assertTrue(composer.validate(plan("procedure", "steps"), r["blocks"], a, idx)[0])

    def test_paragraph_becomes_bullets(self):
        a, idx = answer("Porosity is a gas cavity. It comes from poor shielding. It also comes from dirty joints.")
        r = composer.compose(plan("enumerate", "list"), a, idx)
        self.assertGreaterEqual(composer.flat(r["blocks"]).count("\n- "), 2)

    def test_table_rows_become_bullets(self):
        a, idx = answer(table=(["Mode", "Spatter"], [["Spray", "Very low"], ["Globular", "Highest"]]), chunk_text="Mode Spray Very low Globular Highest Spatter")
        r = composer.compose(plan("enumerate", "list"), a, idx)
        self.assertIn("Spray: Spatter Very low", composer.flat(r["blocks"]))

    def test_label_lines_become_table(self):
        a, idx = answer("- Spray: very low spatter\n- Globular: highest spatter")
        r = composer.compose(plan("enumerate", "table"), a, idx)
        self.assertTrue(any(b["k"] == "table" for b in r["blocks"]))
        self.assertEqual(r["blocks"][-1]["header"], ["Item", "Detail"])

    def test_steps_asked_but_only_options(self):
        a, idx = answer("Spray is a mode. Globular is a mode.")
        r = composer.compose(plan("topic", "steps"), a, idx)
        self.assertFalse(r["fit"])
        self.assertIn("not as a sequence of steps", composer.flat(r["blocks"]))

    def test_brief_is_short(self):
        a, idx = answer("- One fact here.\n- Two fact here.\n- Three fact here.\n- Four fact here.", chunk_text="One fact here. Two fact here. Three fact here. Four fact here.")
        r = composer.compose(plan("topic", None, "brief"), a, idx)
        self.assertNotIn("- ", composer.flat(r["blocks"]))

    def test_plain_words_keep_numbers(self):
        self.assertEqual(composer.simplify("Prior to welding, the current shall be 8 kA."), "Before welding, the current must be 8 kA.")

    def test_headings_are_not_content(self):
        a, idx = answer("**Chapter 1 — Basics** · *Fundamentals*\nSpot welding joins sheets by heat from current.", chunk_text="Spot welding joins sheets by heat from current.")
        r = composer.compose(plan("define"), a, idx)
        self.assertNotIn("Chapter 1", composer.flat(r["blocks"]))

    def test_invented_number_is_refused(self):
        a, idx = answer("The current is 8 kA.")
        ok, why = composer.validate(plan(), [{"k": "text", "md": "The current is 12 kA."}], a, idx)
        self.assertFalse(ok)

    def test_missing_shape_is_refused(self):
        a, idx = answer("Spray is a mode. Globular is a mode.")
        ok, why = composer.validate(plan("procedure", "steps"), [{"k": "text", "md": "Spray is a mode. Globular is a mode."}], a, idx)
        self.assertFalse(ok)

    def test_off_topic_is_refused(self):
        a, idx = answer("The gas flow is set on the regulator.")
        ok, why = composer.validate(plan(terms=["battery", "charger", "voltage"]), [{"k": "text", "md": "The gas flow is set on the regulator."}], a, idx)
        self.assertFalse(ok)

    def test_markdown_table_is_parsed(self):
        blocks = composer.parse_markdown("Intro line.\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        self.assertEqual([b["k"] for b in blocks], ["text", "table"])
        self.assertEqual(blocks[1]["rows"], [["1", "2"]])


if __name__ == "__main__":
    unittest.main()
