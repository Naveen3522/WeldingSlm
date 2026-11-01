"""Answers written by hand: kept, matched to the same question worded a little differently, and given to everyone with the "added by hand" label."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import manual_routes  # noqa: E402
from welding_ai.answering.manual import ManualAnswers, ManualError  # noqa: E402
from welding_ai.orchestration import provenance  # noqa: E402
from welding_ai.orchestration.steps import Ctx, ProviderStep  # noqa: E402


def ctx(question, manual, nested=False):
    h = SimpleNamespace(manual=manual)
    return Ctx(h=h, question=question, history=[], model="m", mode="answer", k=5, q=question, nested=nested)


class Store(unittest.TestCase):
    def setUp(self):
        self.m = ManualAnswers(":memory:")

    def test_the_same_question_worded_a_little_differently_gets_the_answer(self):
        self.m.add("What is the cleaning interval for the cooling filter?", "Every 3 months.", "General")
        events = ProviderStep("manual", "x").run(ctx("what's the cleaning interval of the cooling filter", self.m))
        self.assertIsNotNone(events)
        self.assertEqual([e for e in events if e["type"] == "token"][0]["t"], "Every 3 months.")
        self.assertEqual(events[-1]["route"], "manual")

    def test_another_question_is_left_to_the_documents(self):
        self.m.add("What is the cleaning interval for the cooling filter?", "Every 3 months.")
        self.assertIsNone(ProviderStep("manual", "x").run(ctx("What is the electrode force for 1 mm steel?", self.m)))

    def test_a_part_of_a_bigger_question_is_not_taken(self):
        self.m.add("What is the cleaning interval for the cooling filter?", "Every 3 months.")
        self.assertIsNone(ProviderStep("manual", "x").run(ctx("What is the cleaning interval for the cooling filter?", self.m, nested=True)))

    def test_checks(self):
        for question, answer, area in (("hi", "x", "General"), ("A real question here?", "", "General"), ("A real question here?", "x", "Nowhere"), ("the of a", "x", "General")):
            with self.assertRaises(ManualError):
                self.m.add(question, answer, area)

    def test_a_second_answer_for_the_same_question_is_refused_until_the_first_is_removed(self):
        first = self.m.add("What is the cleaning interval for the cooling filter?", "Every 3 months.")
        with self.assertRaises(ManualError) as stop:
            self.m.add("What is the cleaning interval of the cooling filter?", "Monthly.")
        self.assertEqual(stop.exception.code, "exists")
        self.assertTrue(self.m.remove(first["id"]))
        self.m.add("What is the cleaning interval of the cooling filter?", "Monthly.")

    def test_the_label_says_it_was_added_by_hand(self):
        label = provenance.describe("manual")
        self.assertEqual((label["kind"], label["label"], label["tone"]), ("manual", "Added by hand", "warn"))


class Handler:
    def __init__(self, level):
        self.user = {"level": level, "username": "asha", "email": "a@x.com"}
        self.sent = None

    def send_json(self, obj, code=200):
        self.sent = (code, obj)

    def send_bytes(self, code, data, kind):
        self.sent = (code, data)


class Routes(unittest.TestCase):
    def setUp(self):
        self.m = ManualAnswers(":memory:")

    def call(self, method, path, body=None, level=10):
        h = Handler(level)
        manual_routes.handle(h, self.m, method, path, body)
        return h.sent

    def test_write_list_remove(self):
        code, out = self.call("POST", "/api/answers/manual", {"question": "Who cleans the filter?", "answer": "Maintenance.", "area": "General"})
        self.assertEqual(code, 200)
        self.assertEqual(len(self.call("GET", "/api/answers/manual")[1]["answers"]), 1)
        self.assertEqual(self.call("POST", f"/api/answers/manual/{out['answer']['id']}/delete")[0], 200)
        self.assertEqual(self.call("POST", "/api/answers/manual/99/delete")[0], 404)

    def test_duplicates_and_bad_input(self):
        self.call("POST", "/api/answers/manual", {"question": "Who cleans the filter?", "answer": "Maintenance."})
        self.assertEqual(self.call("POST", "/api/answers/manual", {"question": "Who cleans the filter?", "answer": "Others."})[0], 409)
        self.assertEqual(self.call("POST", "/api/answers/manual", {"question": "x", "answer": "y"})[0], 400)

    def test_a_user_may_not(self):
        self.assertEqual(self.call("GET", "/api/answers/manual", level=1)[0], 403)
        self.assertEqual(self.call("POST", "/api/answers/manual", {"question": "Who cleans the filter?", "answer": "M"}, level=1)[0], 403)


if __name__ == "__main__":
    unittest.main()
