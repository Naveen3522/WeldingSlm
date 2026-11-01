"""The question pair flow: a person sends a pair, an administrator approves, rejects or asks for changes, the approved pairs are the learned pairs file, and training can take only the ticked ones."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from welding_ai.accounts import permissions  # noqa: E402
import gateway  # noqa: E402
import model_check_routes as checks  # noqa: E402
import my_pairs_routes  # noqa: E402,F401  (registers its routes)
from welding_ai.retrain.control import Retrain  # noqa: E402
from welding_ai.training import learned  # noqa: E402
from welding_ai.training.queue import QaError, QaQueue  # noqa: E402

CHUNKS = [{"source": "08_WPS.docx", "heading": "5. Nugget", "text": "The nugget diameter is 4√t to 5√t. Growth stops at expulsion."},
          {"source": "20_Guide.docx", "heading": "2. Table", "text": "Minimum 4.0 mm at 1.0 mm sheet."}]


class Handler:
    def __init__(self, name="ann", level=1):
        self.user = {"username": name, "email": name + "@x", "level": level}
        self.sent = None

    def send_json(self, obj, code=200):
        self.sent = (code, obj)


def pair(question="What is the nugget size?"):
    return {"doc": "08_WPS.docx", "section": "Nugget", "question": question, "answer": "4√t to 5√t", "evidence": CHUNKS[0]["text"]}


class Queue(unittest.TestCase):
    def setUp(self):
        self.q = QaQueue(":memory:")
        self.calls = []
        self.q.listeners.append(lambda: self.calls.append(1))

    def test_a_sent_pair_waits_and_is_not_trained_on(self):
        pair_id = self.q.suggest(pair(), "ann")
        self.assertEqual(self.q.get(pair_id)["status"], "pending")
        self.assertEqual(self.q.approved(), [])
        self.assertEqual([p["id"] for p in self.q.mine("ann")], [pair_id])
        self.assertEqual(self.q.mine("bob"), [])
        self.assertIsNone(self.q.suggest(pair(), "ann"))                          # the same pair twice

    def test_approving_adds_it_to_the_learned_pairs_file_and_never_to_the_training_set(self):
        pair_id = self.q.suggest(pair(), "ann")
        self.q.decide([pair_id], "approved", "admin")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "training" / "learned_pairs.json"
            data = json.loads(learned.write(self.q.learned_only(), path).read_text(encoding="utf-8"))
        self.assertEqual([p["question"] for p in data["pairs"]], ["What is the nugget size?"])
        self.assertEqual(data["pairs"][0]["added_by"], "ann")
        self.assertEqual(self.q.trainable(), [])
        self.assertTrue(self.calls)                                              # listeners are told of every change

    def test_changes_need_a_note_and_the_person_can_send_it_again(self):
        pair_id = self.q.suggest(pair(), "ann")
        with self.assertRaises(QaError):
            self.q.decide([pair_id], "changes", "admin")
        self.q.decide([pair_id], "changes", "admin", note="Say which sheet.")
        row = self.q.get(pair_id)
        self.assertEqual((row["status"], row["note"]), ("changes", "Say which sheet."))
        self.assertFalse(self.q.resend(pair_id, "bob", "x" * 6, "y"))             # not theirs
        self.assertTrue(self.q.resend(pair_id, "ann", "What is the nugget size for 1 mm?", "4√t to 5√t"))
        row = self.q.get(pair_id)
        self.assertEqual((row["status"], row["note"], row["question"]), ("pending", "", "What is the nugget size for 1 mm?"))

    def test_a_rejection_keeps_its_reason(self):
        pair_id = self.q.suggest(pair(), "ann")
        self.q.decide([pair_id], "rejected", "admin", note="Already covered.")
        self.assertEqual(self.q.get(pair_id)["note"], "Already covered.")
        self.assertEqual(self.q.approved(), [])

    def test_the_ticked_rows_are_the_only_ones_a_selected_run_takes(self):
        self.q.add_many([{"doc": "08_WPS.docx", "section": "s", "question": f"Question number {n}?", "answer": f"Answer {n}", "evidence": "e", "kind": "table"} for n in range(3)])
        ids = [p["id"] for p in self.q.page()["rows"]]
        self.assertEqual(sorted(p["id"] for p in self.q.trainable(ids=ids[:2])), sorted(ids[:2]))
        retrain = Retrain(None, None, self.q, 100)
        plan = retrain.plan("short", ids[:1])
        self.assertEqual((plan["new_pairs"], plan["ready"]), (1, True))
        self.q.decide(ids[:1], "rejected", "admin")
        self.assertFalse(retrain.plan("short", ids[:1])["ready"])                  # a row that is not approved is not trained on

    def test_origin_filters_cover_pairs_sent_by_people(self):
        self.q.suggest(pair(), "ann")
        self.assertEqual(self.q.page(origin="hand")["total"], 1)
        self.assertEqual(self.q.page(origin="generated")["total"], 0)
        self.assertEqual(self.q.page(status="changes")["total"], 0)


class Highlights(unittest.TestCase):
    def test_every_highlight_must_be_in_its_document(self):
        body = {"refs": [{"doc": "08_WPS.docx", "text": "4√t to 5√t"}, {"doc": "20_Guide.docx", "text": "Minimum 4.0 mm at 1.0 mm sheet."}]}
        made, why = checks.pair_from_documents("What is the nugget size?", body, CHUNKS)
        self.assertIsNone(why)
        self.assertEqual((made["doc"], made["answer"]), ("08_WPS.docx", "4√t to 5√t Minimum 4.0 mm at 1.0 mm sheet."))
        self.assertEqual(len(made["refs"]), 2)
        _, why = checks.pair_from_documents("What is the nugget size?", {"refs": [{"doc": "20_Guide.docx", "text": "4√t to 5√t"}]}, CHUNKS)
        self.assertIn("word for word", why)
        _, why = checks.pair_from_documents("hi", body, CHUNKS)
        self.assertIn("question", why)
        _, why = checks.pair_from_documents("What is the nugget size?", {"refs": []}, CHUNKS)
        self.assertIn("Highlight", why)

    def test_the_excel_cell_has_each_document_as_a_heading_with_its_chunks(self):
        made, _ = checks.pair_from_documents("What is the nugget size?", {"refs": [{"doc": "08_WPS.docx", "text": "4√t to 5√t"}, {"doc": "08_WPS.docx", "text": "Growth stops at expulsion."},
                                                                                   {"doc": "20_Guide.docx", "text": "Minimum 4.0 mm"}]}, CHUNKS)
        made["refs"] = json.dumps(made["refs"])
        cell = checks.documents_cell(dict(made))
        self.assertEqual(cell.splitlines(), ["08 WPS", "  • 4√t to 5√t", "  • Growth stops at expulsion.", "20 Guide", "  • Minimum 4.0 mm"])


class Routes(unittest.TestCase):
    def setUp(self):
        gateway.app.queue = QaQueue(":memory:")
        gateway.app.chunks = lambda: CHUNKS
        permissions._current = permissions.Permissions()                           # the built-in levels, whatever file is on disk

    def tearDown(self):
        permissions._current = None

    def call(self, method, path, body=None, name="ann", level=1):
        h = Handler(name, level)
        gateway.dispatch(h, method, path, "", body)
        return h.sent

    def test_a_person_sends_a_pair_from_a_document_and_sees_it_waiting(self):
        code, data = self.call("POST", "/api/my/pairs", {"question": "What is the nugget size?", "refs": [{"doc": "08_WPS.docx", "text": "4√t to 5√t"}], "area": "Chat"})
        self.assertEqual(code, 200)
        code, data = self.call("GET", "/api/my/pairs")
        self.assertEqual([(p["status"], p["kind"], p["added_by"]) for p in data["pairs"]], [("pending", "suggested", "ann")])
        self.assertEqual(self.call("GET", "/api/my/pairs", name="bob")[1]["pairs"], [])

    def test_an_own_passage_is_kept_with_the_pair_and_not_published(self):
        code, _ = self.call("POST", "/api/my/pairs", {"question": "What is our gap?", "answer": "3 mm.", "passage_title": "Bracket gap", "passage_text": "The gap for the 3 mm bracket is 3 mm.", "area": "Chat"})
        self.assertEqual(code, 200)
        row = gateway.app.queue.mine("ann")[0]
        self.assertEqual((row["doc"], row["section"], row["status"]), ("Added by hand", "Bracket gap", "pending"))

    def test_a_wrong_highlight_or_a_missing_answer_is_refused(self):
        self.assertEqual(self.call("POST", "/api/my/pairs", {"question": "What is the nugget size?", "refs": [{"doc": "20_Guide.docx", "text": "not there"}]})[0], 400)
        self.assertEqual(self.call("POST", "/api/my/pairs", {"question": "What is our gap?", "answer": "x"})[0], 400)

    def test_resend_only_after_changes_were_asked_and_only_by_its_owner(self):
        self.call("POST", "/api/my/pairs", {"question": "What is our gap?", "answer": "3 mm.", "passage_title": "Bracket gap", "passage_text": "The gap for the 3 mm bracket is 3 mm."})
        pair_id = gateway.app.queue.mine("ann")[0]["id"]
        self.assertEqual(self.call("POST", f"/api/my/pairs/{pair_id}/resend", {"question": "What is our gap now?", "answer": "3 mm."})[0], 409)
        gateway.app.queue.decide([pair_id], "changes", "admin", note="Which bracket?")
        self.assertEqual(self.call("POST", f"/api/my/pairs/{pair_id}/resend", {"question": "What is our gap for the 3 mm bracket?", "answer": "3 mm."}, name="bob")[0], 404)
        self.assertEqual(self.call("POST", f"/api/my/pairs/{pair_id}/resend", {"question": "What is our gap for the 3 mm bracket?", "answer": "3 mm."})[0], 200)
        self.assertEqual(gateway.app.queue.get(pair_id)["status"], "pending")


if __name__ == "__main__":
    unittest.main()


class MyPage(unittest.TestCase):
    """My submissions is paged by the server, so /submissions?page=2&size=10 can be opened directly."""

    def setUp(self):
        gateway.app.queue = QaQueue(":memory:")
        permissions._current = permissions.Permissions()
        for n in range(23):
            gateway.app.queue.suggest(dict(pair(f"Question number {n:02d}?"), answer=f"Answer {n}"), "ann")

    def tearDown(self):
        permissions._current = None

    def get(self, query):
        h = Handler("ann", 1)
        gateway.dispatch(h, "GET", "/api/my/pairs", query, None)
        return h.sent[1]

    def test_a_page_is_cut_from_the_newest_first_list_with_the_total(self):
        first = self.get("offset=0&limit=10")
        self.assertEqual((len(first["pairs"]), first["total"]), (10, 23))
        last = self.get("offset=20&limit=10")
        self.assertEqual((len(last["pairs"]), last["total"]), (3, 23))
        self.assertEqual(first["pairs"][0]["question"], "Question number 22?")
        self.assertEqual(self.get("offset=0&limit=9999")["pairs"].__len__(), 23)                  # the limit is capped at 100, there are only 23


class MyDelete(unittest.TestCase):
    """A person deletes several of their own pairs at once, but never an approved one, and never someone else's."""

    def setUp(self):
        gateway.app.queue = QaQueue(":memory:")
        permissions._current = permissions.Permissions()
        self.mine = [gateway.app.queue.suggest(dict(pair(f"My question number {n}?"), answer=f"Answer {n}"), "ann") for n in range(3)]
        self.theirs = gateway.app.queue.suggest(dict(pair("Bob's question here?"), answer="Bob's answer"), "bob")
        gateway.app.queue.decide([self.mine[0]], "approved", "admin")

    def tearDown(self):
        permissions._current = None

    def delete(self, ids, name="ann"):
        h = Handler(name, 1)
        gateway.dispatch(h, "POST", "/api/my/pairs/delete", "", {"ids": ids})
        return h.sent

    def test_only_unapproved_pairs_of_the_person_go(self):
        code, data = self.delete(self.mine + [self.theirs])
        self.assertEqual((code, data["deleted"], data["left"]), (200, 2, 2))
        self.assertEqual([p["id"] for p in gateway.app.queue.mine("ann")], [self.mine[0]])         # the approved one stays
        self.assertIsNotNone(gateway.app.queue.get(self.theirs))                                  # nobody else's pair is touched

    def test_a_bad_list_is_refused(self):
        self.assertEqual(self.delete(["x"])[0], 400)
        self.assertEqual(self.delete([])[1]["deleted"], 0)
