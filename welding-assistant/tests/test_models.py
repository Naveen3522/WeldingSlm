"""Model names, notes and the active model (in-memory SQLite)."""
import unittest
from pathlib import Path

from welding_ai.llm.registry import ModelError, ModelRegistry

INSTALLED = [{"id": "welding-slm-q8", "label": "Fast (recommended)", "note": "about 2 s"}, {"id": "welding-slm-v2", "label": "Fine-tuned v2", "note": ""}]


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.r = ModelRegistry(":memory:")

    def labels(self, default="welding-slm-q8"):
        return {m["id"]: m["label"] for m in self.r.describe(INSTALLED, default)}

    def test_with_nothing_set_the_builtin_names_and_the_default_apply(self):
        d = self.r.describe(INSTALLED, "welding-slm-q8")
        self.assertEqual([m["label"] for m in d], ["Fast (recommended)", "Fine-tuned v2"])
        self.assertEqual([m["active"] for m in d], [True, False])
        self.assertFalse(any(m["renamed"] for m in d))
        self.assertIsNone(self.r.active())

    def test_rename_changes_only_the_label(self):
        self.r.rename("welding-slm-v2", "  October corpus, glossary fix  ", "trained on 8.2k rows", "naveen")
        d = {m["id"]: m for m in self.r.describe(INSTALLED, "welding-slm-q8")}
        self.assertEqual(d["welding-slm-v2"]["label"], "October corpus, glossary fix")
        self.assertEqual(d["welding-slm-v2"]["builtin_label"], "Fine-tuned v2")
        self.assertEqual(d["welding-slm-v2"]["notes"], "trained on 8.2k rows")
        self.assertTrue(d["welding-slm-v2"]["renamed"])
        self.assertEqual(d["welding-slm-q8"]["label"], "Fast (recommended)")          # the others are untouched

    def test_an_empty_name_goes_back_to_the_builtin_label(self):
        self.r.rename("welding-slm-v2", "My name", "", "naveen")
        self.r.rename("welding-slm-v2", "", "", "naveen")
        self.assertEqual(self.labels()["welding-slm-v2"], "Fine-tuned v2")

    def test_name_rules(self):
        for bad in ("ab", "x" * 61):
            with self.assertRaises(ModelError) as cm:
                self.r.rename("welding-slm-v2", bad, "", "naveen")
            self.assertEqual(cm.exception.code, "bad_name")
        with self.assertRaises(ModelError) as cm:
            self.r.rename("welding-slm-v2", "Fine", "n" * 501, "naveen")
        self.assertEqual(cm.exception.code, "bad_notes")

    def test_a_name_another_model_uses_is_refused_ignoring_case(self):
        with self.assertRaises(ModelError) as cm:
            self.r.rename("welding-slm-v2", "fast (RECOMMENDED)", "", "naveen", taken=["Fast (recommended)"])
        self.assertEqual(cm.exception.code, "name_taken")
        self.r.rename("welding-slm-v2", "Fine-tuned v2", "", "naveen", taken=[])     # keeping its own name is fine

    def test_the_active_model_is_remembered(self):
        self.r.set_active("welding-slm-v2")
        self.assertEqual(self.r.active(), "welding-slm-v2")
        d = {m["id"]: m["active"] for m in self.r.describe(INSTALLED, "welding-slm-q8")}
        self.assertEqual(d, {"welding-slm-q8": False, "welding-slm-v2": True})
        self.r.set_active("welding-slm-q8")
        self.assertEqual(self.r.active(), "welding-slm-q8")

    def test_training_details_are_kept_and_survive_a_rename(self):
        self.r.record_training("welding-slm-v2", "v2-20261020", {"accuracy": 0.79, "refusal": 0.86, "passed": True})
        self.r.rename("welding-slm-v2", "October model", "", "naveen")
        m = next(m for m in self.r.describe(INSTALLED, "welding-slm-q8") if m["id"] == "welding-slm-v2")
        self.assertEqual(m["dataset"], "v2-20261020")
        self.assertEqual(m["gate"], {"accuracy": 0.79, "refusal": 0.86, "passed": True})
        self.assertEqual(m["label"], "October model")

    def test_a_model_that_failed_the_gate_can_be_hidden_and_is_never_hidden_once_active(self):
        self.assertFalse(any(m["hidden"] for m in self.r.describe(INSTALLED, "welding-slm-q8")))
        self.r.set_hidden("welding-slm-v2", True)
        d = {m["id"]: m for m in self.r.describe(INSTALLED, "welding-slm-q8")}
        self.assertTrue(d["welding-slm-v2"]["hidden"])
        self.assertFalse(d["welding-slm-q8"]["hidden"])
        self.r.set_active("welding-slm-v2")
        self.assertFalse(next(m for m in self.r.describe(INSTALLED, "welding-slm-q8") if m["id"] == "welding-slm-v2")["hidden"])

    def test_an_older_database_without_the_column_is_upgraded(self):
        import sqlite3
        import tempfile
        path = Path(tempfile.mkdtemp()) / "old.db"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE model_versions (ollama_id TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', "
                   "dataset TEXT NOT NULL DEFAULT '', gate TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL DEFAULT '', updated REAL NOT NULL DEFAULT 0)")
        db.execute("INSERT INTO model_versions (ollama_id, display_name) VALUES ('welding-slm-v2', 'Kept name')")
        db.commit()
        db.close()
        r = ModelRegistry(path)
        m = next(m for m in r.describe(INSTALLED, "welding-slm-q8") if m["id"] == "welding-slm-v2")
        self.assertEqual((m["label"], m["hidden"]), ("Kept name", False))


if __name__ == "__main__":
    unittest.main()


class ShownTests(unittest.TestCase):
    """The page lists two models: the built-in one and the trained one."""

    def setUp(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
        import model_routes
        self.shown = model_routes.shown

    @staticmethod
    def m(id, dataset="", active=False, hidden=False, updated=0):
        return {"id": id, "dataset": dataset, "active": active, "hidden": hidden, "updated": updated, "label": id}

    def test_the_trained_model_in_use_and_the_builtin_one(self):
        got = self.shown([self.m("welding-slm"), self.m("welding-slm-q8"), self.m("welding-slm-v3", "v3", active=True, updated=5)])
        self.assertEqual([(x["id"], x["kind"]) for x in got], [("welding-slm-v3", "trained"), ("welding-slm-q8", "builtin")])

    def test_with_no_trained_model_only_the_builtin_one_is_listed(self):
        self.assertEqual([x["id"] for x in self.shown([self.m("welding-slm"), self.m("welding-slm-q8", active=True)])], ["welding-slm-q8"])

    def test_when_the_builtin_one_is_in_use_the_newest_trained_model_that_is_not_hidden_is_listed(self):
        got = self.shown([self.m("welding-slm-q8", active=True), self.m("old", "v1", updated=1), self.m("new", "v2", updated=9), self.m("failed", "v3", hidden=True, updated=20)])
        self.assertEqual([x["id"] for x in got], ["new", "welding-slm-q8"])

    def test_the_original_slow_model_is_never_listed(self):
        self.assertNotIn("welding-slm", [x["id"] for x in self.shown([self.m("welding-slm"), self.m("welding-slm-q8")])])
