"""The documents are sorted into agents and formulas on every reindex; a question asking several things is answered part by part, in the style it was asked in."""
import unittest

from welding_ai.formulas import extract
from welding_ai.formulas.store import FormulaStore
from welding_ai.knowledge import passage_types
from welding_ai.orchestration import grounded

TROUBLE = {"source": "12_Troubleshooting.docx", "heading": "3. Porosity: causes and corrective action",
           "text": "Cause | Corrective action\nPorosity | Check gas flow\nSpatter | Lower the voltage\nThe defect is caused by a gas leak; the fix is to replace the hose."}
PROCEDURE = {"source": "08_WPS.docx", "heading": "5. Setup and Execution Procedure",
             "text": "1. Clean the joint.\n2. Fit the electrode.\n3. Set the current.\n4. Run a test weld.\n5. Inspect the weld."}
FORMULA = {"source": "01_Physics.docx", "heading": "4. Governing Physical Relationships",
           "text": "The heat generated is:\n\nQ = I² R t\n\nwhere Q is heat energy in joules, I is welding current in amperes, R is resistance in ohms, and t is weld time in seconds."}
WORDS = {"source": "22_Heat.docx", "heading": "3. Heat input",
         "text": "Heat input is calculated as:\n\nHeat input (kJ/mm) = (Arc voltage × Welding current × 60) ÷ (Travel speed in mm/min × 1000)\n\nSo a weld at 28 V gives 1.25 kJ/mm."}
PROSE = {"source": "99_History.docx", "heading": "1. Background", "text": "Welding has been used for a very long time in many industries."}


class Sorting(unittest.TestCase):
    def test_each_passage_gets_its_type(self):
        self.assertEqual(passage_types.classify(TROUBLE), "troubleshooting")
        self.assertEqual(passage_types.classify(PROCEDURE), "procedure")
        self.assertEqual(passage_types.classify(FORMULA), "formula")
        self.assertEqual(passage_types.classify(PROSE), "reference")

    def test_documents_follow_the_passages_into_agents(self):
        result = passage_types.sort_documents([TROUBLE, PROCEDURE, FORMULA, PROSE])
        self.assertEqual([r["source"] for r in result["agents"]["fault-finder"]], ["12_Troubleshooting.docx"])
        self.assertEqual([r["source"] for r in result["agents"]["procedure-advisor"]], ["08_WPS.docx"])
        self.assertNotIn("99_History.docx", [r["source"] for rows in result["agents"].values() for r in rows])

    def test_a_deleted_document_leaves_its_agent(self):
        result = passage_types.sort_documents([PROCEDURE, PROSE])
        self.assertNotIn("fault-finder", result["agents"])


class FormulasFromDocuments(unittest.TestCase):
    def test_symbol_and_word_formulas_are_read_and_checked(self):
        entries, report = extract.from_documents([FORMULA, WORDS, PROSE])
        by = {e["id"]: e for e in entries}
        self.assertEqual(set(by), {"heat_energy", "heat_input"})
        self.assertEqual(by["heat_energy"]["equations"][0]["expression"], "I**2 * R * t")
        self.assertEqual({v["symbol"] for v in by["heat_input"]["variables"]}, {"arc_voltage", "welding_current", "travel_speed"})
        store = FormulaStore(":memory:")
        synced = store.sync_documents(entries)
        self.assertEqual(sorted(synced["added"]), ["heat_energy", "heat_input"])
        self.assertEqual(synced["invalid"], {})

    def test_a_formula_whose_symbols_are_not_defined_is_left_out(self):
        bad = dict(FORMULA, text="Q = I² R t\n\nwhere Q is heat energy in joules.")
        entries, _ = extract.from_documents([bad])
        self.assertEqual(entries, [])

    def test_deleting_the_document_retires_the_formula(self):
        store = FormulaStore(":memory:")
        store.sync_documents(extract.from_documents([FORMULA])[0])
        self.assertEqual([f["id"] for f in store.usable()], ["heat_energy"])
        report = store.sync_documents(extract.from_documents([PROSE])[0])
        self.assertEqual(report["retired"], ["heat_energy"])
        self.assertEqual(store.usable(), [])

    def test_a_hand_checked_formula_stays_only_while_its_line_is_in_the_documents(self):
        seed = [{"id": "joule_heat", "name": "Joule heat", "source": {"document": "01_Physics.docx", "anchor": "Q = I² R t"}}]
        self.assertEqual(extract.from_documents([FORMULA], seed)[1]["seed_kept"], ["joule_heat"])
        self.assertEqual(extract.from_documents([PROSE], seed)[1]["seed_dropped"], ["joule_heat"])


class QuestionStyle(unittest.TestCase):
    def test_a_question_with_two_parts_is_split(self):
        self.assertEqual(len(grounded.parts_of("What causes porosity in MIG welding, and how do I stop it?")), 2)
        self.assertEqual(grounded.parts_of("What causes porosity in MIG welding?"), [])

    def test_the_layout_asked_for_reaches_the_model(self):
        self.assertIn("table", grounded.style_line("Compare MIG and TIG in a table"))
        self.assertIn("numbered", grounded.style_line("How do I set up a spot welder step by step?"))
        self.assertIn("one to three sentences", grounded.style_line("Briefly, what is arc energy?"))
        line = grounded.style_line("what causes porosity and how to stop it", ["what causes porosity", "how to stop it"])
        self.assertIn("2 parts", line)

    def test_a_part_with_nothing_found_is_told_to_the_model(self):
        line = grounded.style_line("a and b", ["what is x", "what is y"], ["what is y"])
        self.assertIn("Not covered in the documents.", line)


if __name__ == "__main__":
    unittest.main()


class RemovingFormulas(unittest.TestCase):
    """An administrator can take a formula read from the documents off the list. It stays off after a reindex and can be put back."""

    def setUp(self):
        self.store = FormulaStore(":memory:")
        self.entries = extract.from_documents([FORMULA, WORDS])[0]
        self.store.sync_documents(self.entries)

    def ids(self):
        return sorted(f["id"] for f in self.store.usable())

    def test_a_removed_document_formula_stays_off_after_a_reindex(self):
        self.assertEqual(self.ids(), ["heat_energy", "heat_input"])
        self.store.delete("heat_energy", "admin")
        self.assertEqual(self.ids(), ["heat_input"])
        report = self.store.sync_documents(self.entries)                                  # the next reindex finds the same formulas
        self.assertEqual((self.ids(), report["updated"], report["added"]), (["heat_input"], [], []))

    def test_a_removed_formula_can_be_put_back_and_then_follows_the_documents_again(self):
        self.store.delete("heat_energy", "admin")
        self.store.restore("heat_energy", "admin")
        self.assertEqual(self.ids(), ["heat_energy", "heat_input"])
        self.assertEqual(self.store.sync_documents(self.entries)["retired"], [])
        self.assertEqual(self.store.sync_documents(extract.from_documents([WORDS])[0])["retired"], ["heat_energy"])     # the document is gone: it goes with it

    def test_a_document_formula_still_cannot_be_edited(self):
        from welding_ai.formulas.store import FormulaError
        with self.assertRaises(FormulaError):
            self.store.update("heat_energy", {"name": "x"}, "admin")
