"""The Agents page dropdowns are made from the documents."""
import tempfile
import unittest
from pathlib import Path

from welding_ai.agents import options


def chunk(source, heading, text="x"):
    return {"source": source, "heading": heading, "text": text}


CHUNKS = [chunk("39_GMAW.docx", "Chapter 39 — Gas Metal Arc Welding (GMAW / MIG / MAG)", "automotive"), chunk("43_SAW.docx", "Chapter 43 — Submerged Arc Welding (SAW)", "automotive"),
          chunk("45_Stud.docx", "Chapter 45 — Stud Welding", "automotive"), chunk("47_Defects.docx", "Chapter 47 — Arc Welding Defects, Troubleshooting and Process Comparison"),
          chunk("26_W.docx", "Chapter 26 — Weldability IV: Nickel Alloys, Titanium, Copper Alloys and Cast Iron"), chunk("99_Hand.docx", "x", "y")]
TABLES = [{"source": "a.docx", "header": ["Defect", "Cause"], "rows": [["Porosity (ISO 6520)", "gas"], ["Category", "x"], ["Hydrogen cracking", "h"]]},
          {"source": "b.docx", "header": ["Defect", "Cause"], "rows": [["Porosity", "gas"], ["Hydrogen cracking in the HAZ", "h"], ["Hydrogen cracking", "h"]]}]


class Options(unittest.TestCase):
    def test_processes_come_from_chapter_titles_and_defect_chapters_are_not_processes(self):
        self.assertEqual(options.processes(CHUNKS), ["MIG", "Submerged Arc (SAW)", "Stud"])

    def test_materials_are_split_from_the_weldability_titles(self):
        self.assertEqual(options.materials(CHUNKS), ["nickel alloys", "titanium", "copper alloys", "cast iron"])

    def test_a_problem_must_be_in_two_documents_and_variants_are_merged(self):
        self.assertEqual(options.problems(TABLES), ["hydrogen cracking", "porosity"])

    def test_an_industry_must_be_in_enough_documents(self):
        self.assertEqual(options.industries(CHUNKS), ["automotive"])

    def test_save_and_load(self):
        path = Path(tempfile.mkdtemp()) / "o.json"
        self.assertIsNone(options.load(path))
        options.save({"processes": ["MIG"]}, path)
        self.assertEqual(options.load(path), {"processes": ["MIG"]})


if __name__ == "__main__":
    unittest.main()
