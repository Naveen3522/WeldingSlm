"""The question pairs people add only answer at question time. The SLM is trained on rows generated from the documents and on rows an administrator adds to the training set."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

import gateway  # noqa: E402
import training_routes  # noqa: E402,F401  (registers its routes)
from welding_ai.accounts import permissions  # noqa: E402
from welding_ai.retrain.control import Retrain  # noqa: E402
from welding_ai.training import learned  # noqa: E402
from welding_ai.training.queue import QaQueue  # noqa: E402

CHUNKS = [{"source": "22_Heat.docx", "heading": "3. Heat input", "text": "Heat input is the energy delivered per unit length of weld. It is arc energy times a thermal efficiency factor.", "chunk_id": 0}]


def person_pair(question, answer, doc="22_Heat.docx"):
    return {"doc": doc, "section": "s", "question": question, "answer": answer, "evidence": "e"}


def generated(question, answer="42"):
    return {"doc": "22_Heat.docx", "section": "s", "question": question, "answer": answer, "evidence": "e", "kind": "table"}


class Lists(unittest.TestCase):
    def setUp(self):
        self.q = QaQueue(":memory:")
        self.q.add_many([generated("What is heat input?"), generated("What is arc energy?")])
        self.mine = self.q.suggest(person_pair("What gap do we leave?", "We leave 2.5 mm.", doc="Added by hand"), "ann")
        self.q.decide([self.mine], "approved", "admin")

    def test_a_pair_a_person_added_is_never_trained_on(self):
        self.assertNotIn(self.mine, [p["id"] for p in self.q.trainable()])
        self.assertEqual([p["id"] for p in self.q.learned_only()], [self.mine])

    def test_generated_pairs_are_the_training_set(self):
        self.assertEqual(len(self.q.trainable()), 2)
        self.assertEqual(self.q.goes_to(self.q.get(self.mine)), "learned")
        self.assertEqual(self.q.goes_to(self.q.page(origin="generated")["rows"][0]), "training")

    def test_a_pair_a_person_added_does_not_make_a_retrain_ready(self):
        q = QaQueue(":memory:")
        only = q.suggest(person_pair("What gap do we leave?", "We leave 2.5 mm.", doc="Added by hand"), "ann")
        q.decide([only], "approved", "admin")
        self.assertFalse(Retrain(None, None, q, 100).plan("short")["ready"])

    def test_the_two_files_are_disjoint(self):
        with tempfile.TemporaryDirectory() as folder:
            learned.write_both(self.q, Path(folder) / "learned.json", Path(folder) / "training.json")
            both = [json.loads((Path(folder) / n).read_text(encoding="utf-8"))["pairs"] for n in ("learned.json", "training.json")]
        self.assertEqual([p["id"] for p in both[0]], [self.mine])
        self.assertEqual(len(both[1]), 2)
        self.assertFalse({p["id"] for p in both[0]} & {p["id"] for p in both[1]})


class Routes(unittest.TestCase):
    def setUp(self):
        permissions._current = permissions.Permissions()
        gateway.app.queue = QaQueue(":memory:")
        gateway.app.chunks = lambda: CHUNKS

    def tearDown(self):
        permissions._current = None

    def call(self, method, path, body=None, level=10):
        h = SimpleNamespace(user={"username": "admin", "email": "a@x", "level": level}, sent=None)
        h.send_json = lambda obj, code=200: setattr(h, "sent", (code, obj))
        gateway.dispatch(h, method, path, "", body)
        return h.sent

    def test_the_summary_counts_the_training_set_and_the_learned_pairs(self):
        gateway.app.queue.add_many([generated("What is heat input?")])
        mine = gateway.app.queue.suggest(person_pair("What gap do we leave?", "We leave 2.5 mm.", doc="Added by hand"), "ann")
        gateway.app.queue.decide([mine], "approved", "admin")
        code, data = self.call("GET", "/api/training/summary")
        self.assertEqual((code, data["training"], data["learned"], data["added"]), (200, 1, 1, 0))


if __name__ == "__main__":
    unittest.main()
