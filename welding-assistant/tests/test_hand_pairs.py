"""Pairs written by hand with their own passage: the passage joins the search, the pairs can be saved as Excel, and a retrain clears them."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import hand_routes  # noqa: E402
import model_check_routes  # noqa: E402
from welding_ai.core import xlsx  # noqa: E402
from welding_ai.knowledge import manual_passages as mp  # noqa: E402
from welding_ai.training.queue import QaQueue  # noqa: E402

PASSAGE = "For the 3 mm bracket the minimum gap is 0.8 mm; below that the weld bridges and burns through."


class Handler:
    def __init__(self, level=10, path=""):
        self.user, self.path, self.sent, self.raw = {"level": level, "username": "asha"}, path, None, None

    def send_json(self, obj, code=200):
        self.sent = (code, obj)

    def send_bytes(self, code, data, kind, headers=None):
        self.sent, self.raw = (code, kind), data


class Xlsx(unittest.TestCase):
    def test_round_trip_keeps_text_symbols_and_line_breaks(self):
        rows = [["Question", "Answer"], ["What is 5 < 6 & more?", 'Line one\nLine two "quoted"'], ["", "last"]]
        self.assertEqual(xlsx.read(xlsx.write(rows)), rows)

    def test_control_characters_are_dropped(self):
        self.assertEqual(xlsx.read(xlsx.write([["a\x00b"]])), [["ab"]])


class Passages(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = mp.ManualPassages(Path(self.dir.name) / "p.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_added_passage_is_merged_after_the_document_passages(self):
        self.store.add("Bracket gap", PASSAGE, "asha")
        merged = mp.merge([{"source": "A.docx", "heading": "H", "text": "t", "chunk_id": 0}, {"source": "A.docx", "heading": "H", "text": "u", "chunk_id": 1}], self.store.listing())
        self.assertEqual((merged[-1]["source"], merged[-1]["chunk_id"], merged[-1]["heading"]), (mp.SOURCE, 2, "Bracket gap"))

    def test_the_same_title_updates_instead_of_duplicating(self):
        self.store.add("Bracket gap", PASSAGE)
        self.store.add("bracket gap", PASSAGE + " Checked on site.")
        self.assertEqual(len(self.store.listing()), 1)
        self.assertIn("Checked on site", self.store.listing()[0]["text"])

    def test_short_text_is_refused(self):
        with self.assertRaises(mp.PassageError):
            self.store.add("Gap", "too short")

    def test_it_survives_the_file_being_read_again(self):
        self.store.add("Bracket gap", PASSAGE)
        self.assertEqual(len(mp.ManualPassages(self.store.path).listing()), 1)


class Routes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.clock = [1000.0]
        self.queue, self.checks = QaQueue(":memory:", clock=lambda: self.clock[0]), model_check_routes.PairChecks(":memory:")
        self.passages = mp.ManualPassages(Path(self.dir.name) / "p.json")
        self.changed = []

    def tearDown(self):
        self.dir.cleanup()

    def call(self, method, path, body=None, level=10):
        h = Handler(level, path)
        hand_routes.handle(h, self.queue, self.checks, self.passages, method, path.split("?")[0], body, on_change=lambda: self.changed.append(1), clock=lambda: self.clock[0])
        return h

    def add(self, question="What is the minimum gap for the 3 mm bracket?", area="Fault Finder"):
        return self.call("POST", "/api/admin/qa/hand", {"question": question, "answer": "0.8 mm", "passage_title": "Bracket gap", "passage_text": PASSAGE, "area": area})

    def test_a_pair_and_its_passage_are_made_and_the_index_is_rebuilt(self):
        self.assertEqual(self.add().sent[0], 201)
        self.assertEqual(len(self.passages.listing()), 1)
        self.assertEqual(self.changed, [1])
        pair = self.call("GET", "/api/admin/qa/hand").sent[1]["pairs"][0]
        self.assertEqual((pair["area"], pair["passage_title"], pair["saved"]), ("Fault Finder", "Bracket gap", False))
        self.assertEqual(self.queue.counts()["approved"], 1)

    def test_a_second_question_on_the_same_passage_does_not_duplicate_it(self):
        self.add()
        self.add("How small can the gap be on a 3 mm bracket?")
        self.assertEqual((len(self.passages.listing()), len(self.queue.by_kind("manual"))), (1, 2))

    def test_agents_show_only_their_own_questions(self):
        self.add(area="Fault Finder")
        self.add("Which wire do we use for P91?", area="Procedure Advisor")
        self.assertEqual(self.call("GET", "/api/agents/questions?area=Procedure%20Advisor", level=1).sent[1]["questions"], ["Which wire do we use for P91?"])

    def test_chat_is_the_default_place(self):
        self.call("POST", "/api/admin/qa/hand", {"question": "What is the gap on the bracket?", "answer": "0.8", "passage_title": "Bracket gap", "passage_text": PASSAGE})
        self.assertEqual(self.call("GET", "/api/agents/questions?area=Chat", level=1).sent[1]["questions"], ["What is the gap on the bracket?"])

    def test_excel_has_the_columns_and_marks_the_pairs_saved(self):
        self.add()
        h = self.call("GET", "/api/admin/qa/hand.xlsx")
        rows = xlsx.read(h.raw)
        self.assertEqual(rows[0], hand_routes.COLUMNS)
        self.assertEqual((rows[1][0], rows[1][2], rows[1][4]), ("What is the minimum gap for the 3 mm bracket?", "Bracket gap", "Fault Finder"))
        self.assertTrue(self.call("GET", "/api/admin/qa/hand").sent[1]["pairs"][0]["saved"])
        self.clock[0] = 2000.0
        self.add("A pair made after saving the file, about the gap?")
        self.assertEqual([p["saved"] for p in self.call("GET", "/api/admin/qa/hand").sent[1]["pairs"]], [True, False])

    def test_a_retrain_deletes_the_pairs_but_not_the_passages(self):
        self.add()
        self.assertEqual(self.queue.delete_kind("manual"), 1)
        self.assertEqual(self.queue.by_kind("manual"), [])
        self.assertEqual(len(self.passages.listing()), 1)

    def test_the_check_against_documents_judges_these_pairs_by_their_passage(self):
        self.add()
        texts = model_check_routes.document_texts(mp.merge([], self.passages.listing()))
        pair = self.queue.by_kind("manual")[0]
        self.assertEqual(model_check_routes.judge(pair, texts), "found")

    def test_users_below_weld_engineer_cannot_write_or_download(self):
        self.assertEqual(self.call("POST", "/api/admin/qa/hand", {"question": "x" * 10}, level=1).sent[0], 403)
        self.assertEqual(self.call("GET", "/api/admin/qa/hand.xlsx", level=1).sent[0], 403)

    def test_bad_input_is_refused(self):
        self.assertEqual(self.call("POST", "/api/admin/qa/hand", {"question": "ok question?", "answer": "", "passage_title": "T", "passage_text": PASSAGE}).sent[0], 400)
        self.assertEqual(self.call("POST", "/api/admin/qa/hand", {"question": "ok question?", "answer": "a", "passage_title": "T", "passage_text": "short"}).sent[0], 400)
        self.assertEqual(self.call("POST", "/api/admin/qa/hand", {"question": "ok question?", "answer": "a", "passage_title": "Title", "passage_text": PASSAGE, "area": "Nope"}).sent[0], 400)


if __name__ == "__main__":
    unittest.main()
