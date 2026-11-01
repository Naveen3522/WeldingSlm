"""Unit tests for question understanding (intent, format, parts)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from welding_ai.understanding import planner     # noqa: E402


class Planner(unittest.TestCase):
    def test_multi_part_question_is_split(self):
        p = planner.analyze("what is WPS what does it contain and what does it lack")
        self.assertEqual([x["own"] for x in p["parts"]], [[], ["contain"], ["lack"]])
        self.assertEqual(p["entity"], "WPS")

    def test_format_words(self):
        self.assertEqual(planner.analyze("give the gun states in a table")["format"], "table")
        self.assertEqual(planner.analyze("list the gun states as bullets")["format"], "list")
        self.assertEqual(planner.analyze("how to prepare electrodes step by step")["format"], "steps")
        self.assertEqual(planner.analyze("what is WPS briefly")["length"], "brief")

    def test_intents(self):
        cases = {"what are 4 different testing layer": "enumerate", "servo gun state shows Ready what does it mean": "situation",
                 "compare WPS and machine program": "compare", "gun does not reach position how to diagnose": "diagnose",
                 "how to dress electrode tips": "procedure", "what is porosity": "define"}
        for q, want in cases.items():
            self.assertEqual(planner.analyze(q)["intent"], want, q)

    def test_vague_question(self):
        self.assertTrue(planner.analyze("choosing material for my welding")["vague"])
        self.assertFalse(planner.analyze("what is WPS")["vague"])


if __name__ == "__main__":
    unittest.main()
