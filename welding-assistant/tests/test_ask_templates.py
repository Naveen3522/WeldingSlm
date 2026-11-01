"""Ask with templates: listing, explaining, making a Word file in the mode the question says, creating a template from named rows, and the permission."""
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import ask_templates as at  # noqa: E402
from welding_ai.forms.docx_table import read_table  # noqa: E402
from welding_ai.forms.store import FormStore  # noqa: E402


def kinds(events):
    return [e["type"] for e in events]


def text_of(events):
    return " ".join(e["t"] for e in events if e["type"] == "token")


class Handler:
    def __init__(self, level):
        self.user = {"level": level}
        self.sent = None

    def send_json(self, obj, code=200):
        self.sent = (code, obj)

    def send_bytes(self, code, data, kind, headers=None):
        self.sent = (code, data, kind)


class Asking(unittest.TestCase):
    def setUp(self):
        self.forms = FormStore(":memory:")
        self.tpl = self.forms.save_template(None, "Spot welding WPS", ["Field", "Value"], [["Process", "RSW"], ["Electrode force", ""], ["Weld time", ""]])
        self.ask = at.TemplateAsk(self.forms)

    def claim(self, question, allowed=True):
        return self.ask.claim(question, allowed)

    def test_other_questions_are_left_to_the_documents(self):
        self.assertIsNone(self.claim("What is the electrode force for 1 mm steel?"))
        self.assertIsNone(self.claim("What does a welding procedure specification contain?"))

    def test_listing(self):
        out = self.claim("Which templates are there?")
        self.assertIn("table", kinds(out))
        self.assertEqual([e for e in out if e["type"] == "table"][0]["rows"], [["Spot welding WPS", "3"]])

    def test_explaining_a_named_template(self):
        out = self.claim("What is the Spot welding WPS template?")
        self.assertIn("2 cells are left for you to fill in", text_of(out))
        self.assertEqual([e for e in out if e["type"] == "table"][0]["rows"][1], ["Electrode force", ""])

    def test_a_vague_word_file_request_asks_how_to_fill_it(self):
        out = self.claim("Make a Word file from the Spot welding WPS template")
        options = [e for e in out if e["type"] == "clarify"][0]["options"]
        self.assertEqual(len(options), 3)
        self.assertTrue(all("Spot welding WPS" in o["query"] for o in options))

    def test_defaults_and_blank(self):
        out = self.claim("Make a Word file from the Spot welding WPS template with defaults")
        self.assertEqual([e for e in out if e["type"] == "template_file"][0]["rows"][0], ["Process", "RSW"])
        out = self.claim("make a blank word file from the spot welding wps template")
        self.assertEqual([e for e in out if e["type"] == "template_file"][0]["rows"][0], ["Process", ""])

    def test_ask_me_starts_the_row_by_row_questions(self):
        out = self.claim("Make a Word file from the Spot welding WPS template and ask me each row")
        fill = [e for e in out if e["type"] == "template_fill"][0]
        self.assertEqual((fill["name"], fill["heads"]), ("Spot welding WPS", ["Field", "Value"]))
        self.assertEqual(out[-1]["route"], "template_fill")

    def test_no_access_is_said_plainly(self):
        out = self.claim("Make a Word file from the Spot welding WPS template with defaults", allowed=False)
        self.assertEqual(out[-1]["route"], "template_denied")
        self.assertIn("don't have access", text_of(out))
        self.assertNotIn("template_file", kinds(out))
        self.assertIn("table", kinds(self.claim("What is the Spot welding WPS template?", allowed=False)))

    def test_creating_a_template_from_named_rows(self):
        out = self.claim("Create a template with rows Electrode force, Weld time and Hold time")
        made = [e for e in out if e["type"] == "template_file"][0]
        self.assertEqual(made["rows"], [["Electrode force", ""], ["Weld time", ""], ["Hold time", ""]])

    def test_a_repeated_row_name_is_refused(self):
        out = self.claim("Create a template with rows Weld time, weld time")
        self.assertNotIn("template_file", kinds(out))
        self.assertEqual(out[-1]["route"], "template_invalid")

    def test_creating_without_rows_says_how(self):
        self.assertIn("Which rows", text_of(self.claim("Create a new template")))


class File(unittest.TestCase):
    def setUp(self):
        self.forms = FormStore(":memory:")
        self.tpl = self.forms.save_template(None, "Spot welding WPS", ["Field", "Value"], [["Process", "RSW"], ["Electrode force", ""]])

    def post(self, body, level=1):
        h = Handler(level)
        at.handle(h, self.forms, "POST", "/api/ask/table.docx", body)
        return h.sent

    def test_a_filled_template_comes_back_as_a_word_file_that_reads_back(self):
        code, data, kind = self.post({"name": "Spot welding WPS", "template_id": self.tpl["id"], "heads": ["Field", "Value"], "rows": [["Process", "RSW"], ["Electrode force", "2.2 kN"]]})
        self.assertEqual(code, 200)
        table = read_table(data)
        self.assertEqual((table["heads"], table["rows"]), (["Field", "Value"], [["Process", "RSW"], ["Electrode force", "2.2 kN"]]))

    def test_a_fixed_cell_or_a_field_name_cannot_change(self):
        for rows in ([["Process", "TIG"], ["Electrode force", ""]], [["Procedure", "RSW"], ["Electrode force", ""]], [["Process", "RSW"]]):
            code, out = self.post({"name": "x", "template_id": self.tpl["id"], "heads": ["Field", "Value"], "rows": rows})
            self.assertEqual(code, 400)

    def test_without_the_right_it_is_refused(self):
        code, out = self.post({"name": "x", "heads": ["Field", "Value"], "rows": [["A", ""]]}, level=0)
        self.assertEqual(code, 403)


if __name__ == "__main__":
    unittest.main()
