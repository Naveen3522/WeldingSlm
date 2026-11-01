"""A filled draft cell is sent as a question and answer: it waits for an administrator, is shown with its draft, and is never trained on."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

import gateway  # noqa: E402
import my_pairs_routes  # noqa: E402
import model_check_routes  # noqa: E402
from welding_ai.accounts import permissions  # noqa: E402
from welding_ai.forms.store import FormStore  # noqa: E402
from welding_ai.training.queue import QaQueue  # noqa: E402


class DraftPairs(unittest.TestCase):
    def setUp(self):
        permissions._current = permissions.Permissions()
        gateway.app.queue = QaQueue(":memory:")
        gateway.app.forms = FormStore(":memory:")
        heads, rows = ["Field", "Value"], [["Gap we leave", ""], ["Fixed", "x"]]
        tpl = gateway.app.forms.save_template(None, "Gap sheet", heads, rows)
        self.draft = gateway.app.forms.create_draft("Line 4 setup", tpl["id"], heads, [["Gap we leave", "2.5 mm at the left gun"], ["Fixed", "x"]])

    def tearDown(self):
        permissions._current = None

    def call(self, path, body):
        h = SimpleNamespace(user={"username": "ann", "email": "a@x", "level": 1}, sent=None)
        h.send_json = lambda obj, code=200: setattr(h, "sent", (code, obj))
        gateway.dispatch(h, "POST", path, "", body)
        return h.sent

    def test_a_filled_cell_waits_and_keeps_its_draft(self):
        code, data = self.call("/api/my/pairs/from-draft", {"draft_id": self.draft["id"], "row": 0, "col": 1})
        self.assertEqual(code, 200)
        pair = gateway.app.queue.get(data["id"])
        self.assertEqual((pair["status"], pair["kind"], pair["answer"]), ("pending", "suggested", "2.5 mm at the left gun"))
        self.assertEqual(pair["question"], "What is the Gap we leave in the Line 4 setup draft?")           # the column "Value" is left out
        self.assertEqual(json.loads(pair["draft"])["name"], "Line 4 setup")
        self.assertEqual(json.loads(pair["draft"])["template"], "Gap sheet")

    def test_a_row_named_by_a_number_is_asked_at_that_number(self):
        self.assertEqual(my_pairs_routes.draft_question("welding draft", "Sl.No", "2", "Welding checklist"), "What is the Welding checklist at Sl.No 2 in the welding draft?")
        self.assertEqual(my_pairs_routes.draft_question("Cell 9", "Parameter", "Weld time", "Remarks"), "What is the Remarks for Weld time in the Cell 9 draft?")

    def test_a_person_edits_their_own_waiting_pair_but_a_draft_answer_stays(self):
        _, data = self.call("/api/my/pairs/from-draft", {"draft_id": self.draft["id"], "row": 0, "col": 1})
        code, _ = self.call(f"/api/my/pairs/{data['id']}/edit", {"question": "What gap is left at the left gun?", "answer": "changed"})
        pair = gateway.app.queue.get(data["id"])
        self.assertEqual((code, pair["question"], pair["answer"], pair["status"]), (200, "What gap is left at the left gun?", "2.5 mm at the left gun", "pending"))
        gateway.app.queue.decide([data["id"]], "approved", "admin")
        self.assertEqual(self.call(f"/api/my/pairs/{data['id']}/edit", {"question": "Another question here?"})[0], 409)       # approved: an administrator changes it

    def test_the_same_cell_twice_and_an_empty_cell_are_refused(self):
        body = {"draft_id": self.draft["id"], "row": 0, "col": 1}
        self.call("/api/my/pairs/from-draft", body)
        self.assertEqual(self.call("/api/my/pairs/from-draft", body)[0], 409)
        self.assertEqual(self.call("/api/my/pairs/from-draft", {"draft_id": self.draft["id"], "row": 0, "col": 0})[0], 400)
        self.assertEqual(self.call("/api/my/pairs/from-draft", {"draft_id": 999, "row": 0, "col": 1})[0], 400)

    def test_once_approved_it_is_looked_up_and_never_trained(self):
        _, data = self.call("/api/my/pairs/from-draft", {"draft_id": self.draft["id"], "row": 0, "col": 1})
        gateway.app.queue.decide([data["id"]], "approved", "admin")
        self.assertEqual(gateway.app.queue.trainable(), [])
        self.assertEqual([p["id"] for p in gateway.app.queue.learned_only()], [data["id"]])

    def test_the_excel_row_names_the_draft(self):
        _, data = self.call("/api/my/pairs/from-draft", {"draft_id": self.draft["id"], "row": 0, "col": 1})
        row = model_check_routes.export_row(gateway.app.queue.get(data["id"]), {})
        self.assertEqual(len(row), len(model_check_routes.EXPORT_COLUMNS))
        cols = model_check_routes.EXPORT_COLUMNS
        self.assertEqual([row[cols.index(c)] for c in ("Source", "Draft name", "Template", "Field", "Column")], ["From a draft", "Line 4 setup", "Gap sheet", "Gap we leave", "Value"])
        self.assertEqual(row[cols.index("Documents")], "Field: Gap we leave\nValue: 2.5 mm at the left gun")


if __name__ == "__main__":
    unittest.main()
