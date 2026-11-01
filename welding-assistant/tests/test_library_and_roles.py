"""Roles and what each may do, deleting documents safely, model names made by the retrain, and the settings behind automatic training."""
import tempfile
import unittest
from pathlib import Path

from welding_ai.accounts import levels
from welding_ai.formulas import simple, validate
from welding_ai.ingestion import uploads
from welding_ai.llm.registry import ModelRegistry
from welding_ai.training.queue import QaQueue


class Roles(unittest.TestCase):
    def test_three_starting_roles_with_the_levels_agreed(self):
        names = {1: "User", 4: "User", 5: "Weld Engineer", 9: "Weld Engineer", 10: "Admin"}
        for level, name in names.items():
            self.assertEqual(levels.name(level), name, level)

    def test_what_each_level_may_do(self):
        can = lambda level, right: levels.allowed(level, right)
        for right in ("ask", "summary", "history", "agents", "suggest_formula"):
            self.assertTrue(can(1, right), right)                       # a plain User uses the whole app and may suggest a formula
        self.assertFalse(can(1, "edit_formula") or can(1, "library") or can(1, "admin"))
        self.assertTrue(can(5, "edit_formula") and can(5, "library"))      # a Weld Engineer adds formulas and documents
        self.assertFalse(can(5, "admin"))
        self.assertTrue(can(10, "admin") and can(10, "library") and can(10, "edit_formula"))


class Deleting(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.docs, self.trash = self.root / "docs", self.root / "trash"
        self.docs.mkdir()
        (self.docs / "01_A.docx").write_bytes(b"x")
        (self.docs / "02_B.docx").write_bytes(b"y")

    def test_a_deleted_document_is_moved_not_erased(self):
        out = uploads.delete_all(self.docs, ["01_A.docx"], self.trash)
        self.assertEqual(out, {"deleted": ["01_A.docx"], "missing": []})
        self.assertFalse((self.docs / "01_A.docx").exists())
        self.assertEqual([p.read_bytes() for p in self.trash.iterdir()], [b"x"])
        self.assertTrue((self.docs / "02_B.docx").exists())

    def test_an_unknown_name_or_a_path_outside_is_reported_and_touches_nothing(self):
        (self.root / "secret.docx").write_bytes(b"s")
        out = uploads.delete_all(self.docs, ["nothing.docx", "../secret.docx", "notes.txt"], self.trash)
        self.assertEqual(out["deleted"], [])
        self.assertEqual(len(out["missing"]), 3)
        self.assertTrue((self.root / "secret.docx").exists())

    def test_the_trash_is_not_inside_the_documents_folder(self):
        uploads.delete_all(self.docs, ["02_B.docx"], self.trash)
        self.assertEqual([p.name for p in self.docs.rglob("*.docx")], ["01_A.docx"])


class ModelNames(unittest.TestCase):
    def test_labels_and_forget(self):
        reg = ModelRegistry(":memory:")
        reg.rename("m1", "weld-2026-10-21", "", "retrain")
        reg.record_training("m1", "v3", {"passed": True})
        self.assertIn("weld-2026-10-21", reg.labels())
        reg.forget("m1")
        self.assertEqual(reg.labels(), set())
        self.assertEqual(reg.entry("m1")["dataset"], "")


class Settings(unittest.TestCase):
    def test_automatic_training_is_on_until_turned_off(self):
        q = QaQueue(":memory:")
        self.assertEqual(q.setting("auto_retrain", "1"), "1")
        q.set_setting("auto_retrain", "0")
        self.assertEqual(q.setting("auto_retrain", "1"), "0")
        self.assertTrue(q.auto_approve())                                  # the approval setting is untouched


class SimpleFormula(unittest.TestCase):
    BODY = {"name": "Arc energy test", "description": "Energy per mm of weld", "expression": "(V*I*60)/(v*1000)", "symbol": "Q", "unit": "kJ/mm",
            "variables": [{"symbol": "V", "unit": "V"}, {"symbol": "I", "unit": "A"}, {"symbol": "v", "unit": "mm/min"}]}

    def check(self, body):
        return validate.problems(validate.clean(simple.expand(body)), require_example=False)

    def test_used_for_becomes_the_summary_and_is_capped(self):
        full = simple.expand(dict(self.BODY, description="x" * 400))
        self.assertEqual(len(full["summary"]), 200)
        self.assertEqual(simple.expand(self.BODY)["summary"], "Energy per mm of weld")

    def test_without_a_source_it_is_marked_manual(self):
        self.assertEqual(simple.expand(self.BODY)["source"]["reference"], simple.MANUAL)
        self.assertEqual(simple.expand(self.BODY)["source"]["document"], "")

    def test_a_source_document_and_passage_are_kept(self):
        body = dict(self.BODY, source={"document": "22_Heat_Input.docx", "anchor": "arc energy is"})
        source = simple.expand(body)["source"]
        self.assertEqual((source["document"], source["anchor"]), ("22_Heat_Input.docx", "arc energy is"))

    def test_every_symbol_must_be_used(self):
        self.assertEqual(self.check(self.BODY), [])
        unused = dict(self.BODY, variables=self.BODY["variables"] + [{"symbol": "x", "unit": "-"}])
        self.assertTrue(any("never used" in p for p in self.check(unused)))

    def test_it_round_trips_to_the_edit_form(self):
        stored = validate.clean(simple.expand(self.BODY))
        back = simple.to_simple(stored)
        self.assertEqual((back["name"], back["description"], back["expression"]), ("Arc energy test", "Energy per mm of weld", "(V*I*60)/(v*1000)"))


if __name__ == "__main__":
    unittest.main()
