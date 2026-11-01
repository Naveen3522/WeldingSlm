"""Each control on the Permissions page is enforced by the server on its own: lowering one lets a lower level do exactly that and nothing else, and by default only
Weld Engineers (5) and Admins (10) have the ones that change the library, the models and the question pairs."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import hand_routes  # noqa: E402
import model_check_routes  # noqa: E402
import model_routes  # noqa: E402
import retrain_routes  # noqa: E402
from welding_ai.accounts import permissions as P  # noqa: E402
from welding_ai.knowledge.manual_passages import ManualPassages  # noqa: E402
from welding_ai.llm.registry import ModelRegistry  # noqa: E402
from welding_ai.training.queue import QaQueue  # noqa: E402


def tree_with(**changes):
    tree = P.defaults()
    for s in tree:
        if s["id"] in changes:
            s["read"], s["write"] = changes[s["id"]]
        for c in s["controls"]:
            if c["id"] in changes:
                c["read"], c["write"] = changes[c["id"]]
    return tree


class Handler:
    def __init__(self, level, path=""):
        self.user, self.path, self.sent = {"level": level, "username": "t"}, path, None

    def send_json(self, obj, code=200):
        self.sent = (code, obj)

    def send_bytes(self, code, data, kind, headers=None):
        self.sent = (code, kind)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.perms = P.Permissions(self.dir / "permissions.xml")
        self.perms.ensure_file()
        self.saved, P._current = P._current, self.perms
        self.queue, self.checks = QaQueue(":memory:"), model_check_routes.PairChecks(":memory:")
        self.passages = ManualPassages(self.dir / "p.json")

    def tearDown(self):
        P._current = self.saved

    def lower(self, **changes):
        self.perms.save(tree_with(**changes))


class Pairs(Base):
    def add_pair(self, level, question="What is the gap on the bracket?"):
        h = Handler(level, "/api/admin/qa/hand")
        hand_routes.handle(h, self.queue, self.checks, self.passages, "POST", "/api/admin/qa/hand",
                           {"question": question, "answer": "0.8 mm", "passage_title": "Bracket gap", "passage_text": "The gap on the bracket is 0.8 mm, no less."})
        return h.sent[0]

    def check(self, level, path):
        h = Handler(level, path)
        model_check_routes.handle(h, self.queue, self.checks, lambda: [], "POST", path, {}, lambda: (Path("."), 1, False), lambda q: "")
        return h.sent[0]

    def test_adding_a_pair_is_control_7_2_and_by_default_for_weld_engineers_and_admins(self):
        self.assertEqual((self.add_pair(1), self.add_pair(5)), (403, 201))
        self.lower(**{"7": (1, 1), "7.1": (1, 1), "7.2": (1, 1), "7.3": (5, 5)})
        self.assertEqual(self.add_pair(1, "How small can the bracket gap be?"), 201)

    def test_adding_a_pair_can_be_taken_away_from_weld_engineers_without_touching_the_rest(self):
        self.lower(**{"7.2": (10, 10)})
        self.assertEqual((self.add_pair(5), self.add_pair(10)), (403, 201))
        self.assertEqual(self.check(5, "/api/admin/qa/check"), 200)

    def test_check_and_test_are_control_7_3(self):
        for path in ("/api/admin/qa/check",):
            self.lower(**{"7.3": (10, 10)})
            self.assertEqual((self.check(5, path), self.check(10, path)), (403, 200))
        self.assertEqual(self.check(5, "/api/admin/qa/test"), 403)

    def test_the_excel_file_is_open_to_whoever_may_reindex_even_without_the_pairs_page(self):
        def excel(level):
            h = Handler(level, "/api/admin/qa/hand.xlsx")
            hand_routes.handle(h, self.queue, self.checks, self.passages, "GET", "/api/admin/qa/hand.xlsx", None)
            return h.sent[0]
        self.assertEqual(excel(1), 403)
        self.lower(**{"2.4": (1, 1)})
        self.assertEqual((excel(1), excel(5)), (200, 200))
        h = Handler(1, "/api/admin/qa/hand")
        hand_routes.handle(h, self.queue, self.checks, self.passages, "GET", "/api/admin/qa/hand", None)
        self.assertEqual(h.sent[0], 403)                                   # the pairs themselves stay behind the pairs page


class Models(Base):
    def call(self, level, action):
        h = Handler(level)
        model_routes.handle(h, ModelRegistry(":memory:"), type("C", (), {"default_model": "welding-slm-q8"}), "POST", f"/api/admin/models/welding-slm-q8/{action}", {})
        return h.sent[0]

    def test_rename_and_delete_are_control_6_3_not_use_this_model(self):
        self.lower(**{"6.3": (5, 5)})
        self.assertNotEqual(self.call(5, "rename"), 403)                   # allowed (it then needs Ollama, which a test does not have)
        self.assertEqual(self.call(1, "rename"), 403)
        self.lower(**{"6.2": (5, 5), "6.3": (10, 10)})
        self.assertEqual((self.call(5, "rename"), self.call(5, "delete")), (403, 403))


class Training(Base):
    def start(self, level, mode):
        h = Handler(level, "/api/refresh/start")
        retrain_routes.handle(h, self.queue, None, None, type("J", (), {"running": True})(), "POST", "/api/refresh/start", {"mode": mode})
        return h.sent[0]

    def test_the_training_test_is_control_6_5_and_a_full_retrain_stays_6_4(self):
        self.lower(**{"6.5": (5, 5)})
        self.assertEqual(self.start(10, "test"), 409)                      # allowed; refused only because this stand-in says an index is running
        self.assertEqual(self.start(5, "test"), 409)
        self.assertEqual(self.start(5, "full"), 403)
        self.assertEqual(self.start(1, "test"), 403)


if __name__ == "__main__":
    unittest.main()
