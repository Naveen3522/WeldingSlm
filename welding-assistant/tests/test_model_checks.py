"""The Model management page's checks: pairs compared with the documents, the system check, and the hand-written pair."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import model_check_routes as m  # noqa: E402
from welding_ai.training.queue import QaQueue  # noqa: E402

CHUNKS = [{"source": "A.docx", "heading": "A > Table 1", "text": "| Key | Value |\n| gap | 0.8 mm |\n| force | 2.2 kN |"},
          {"source": "B.docx", "heading": "B > Intro", "text": "Nothing about forces here."}]


def pair(doc, question, answer):
    return {"doc": doc, "section": "S", "question": question, "answer": answer, "evidence": answer, "kind": "table"}


class Handler:
    def __init__(self, level=10, path="/api/admin/qa"):
        self.user = {"level": level, "username": "asha"}
        self.path = path
        self.sent = None

    def send_json(self, obj, code=200):
        self.sent = (code, obj)


class Judging(unittest.TestCase):
    def test_found_changed_and_missing(self):
        texts = m.document_texts(CHUNKS)
        self.assertEqual(m.judge(pair("A.docx", "q", "0.8 mm"), texts), "found")
        self.assertEqual(m.judge(pair("A.docx", "q", "0.9 mm"), texts), "changed")
        self.assertEqual(m.judge(pair("C.docx", "q", "0.8 mm"), texts), "missing")

    def test_spacing_case_and_table_bars_do_not_matter(self):
        self.assertEqual(m.judge(pair("A.docx", "q", "0.8   MM"), m.document_texts(CHUNKS)), "found")


class SystemCheck(unittest.TestCase):
    def run_check(self, smi_out=None, libs=True, ollama=True, approved=3, questions=10):
        def fake_run(*a, **k):
            if smi_out is None:
                raise FileNotFoundError
            return SimpleNamespace(stdout=smi_out)
        return m.system_check(Path("."), {"approved": approved}, questions, ollama, run=fake_run, which=lambda name: object() if libs else None)

    def test_ready_when_everything_is_there(self):
        result = self.run_check("NVIDIA GeForce GTX 1650, 4096, 3500\n")
        self.assertTrue(result["ready"])
        self.assertEqual(result["lines"][0]["state"], "ok")

    def test_no_card_blocks_training(self):
        result = self.run_check(None)
        self.assertFalse(result["ready"])
        self.assertEqual(result["lines"][0]["state"], "bad")

    def test_low_graphics_memory_is_a_warning_not_a_block(self):
        result = self.run_check("GPU, 4096, 1000\n")
        self.assertEqual(result["lines"][1]["state"], "warn")
        self.assertTrue(result["ready"])

    def test_missing_pieces_block(self):
        self.assertFalse(self.run_check("GPU, 4096, 3500\n", libs=False)["ready"])
        self.assertFalse(self.run_check("GPU, 4096, 3500\n", ollama=False)["ready"])
        self.assertFalse(self.run_check("GPU, 4096, 3500\n", approved=0)["ready"])
        self.assertFalse(self.run_check("GPU, 4096, 3500\n", questions=0)["ready"])


class Routes(unittest.TestCase):
    def setUp(self):
        self.queue = QaQueue(":memory:")
        self.checks = m.PairChecks(":memory:")
        self.queue.set_auto_approve(False)
        self.queue.add_many([pair("A.docx", "Gap?", "0.8 mm"), pair("A.docx", "Force?", "9 kN"), pair("C.docx", "Old?", "x y")])
        self.chunks = lambda: CHUNKS

    def call(self, method, path, body=None, level=10):
        h = Handler(level, path)
        m.handle(h, self.queue, self.checks, self.chunks, method, path.split("?")[0], body, lambda: (Path("."), 5, True))
        return h.sent

    def test_check_counts_and_rows_carry_the_result(self):
        code, counts = self.call("POST", "/api/admin/qa/check")
        self.assertEqual((counts["found"], counts["changed"], counts["missing"]), (1, 1, 1))
        rows = self.call("GET", "/api/admin/qa")[1]["rows"]
        self.assertEqual({r["question"]: r["in_document"] for r in rows}, {"Gap?": "found", "Force?": "changed", "Old?": "missing"})

    def test_filter_on_the_check(self):
        self.call("POST", "/api/admin/qa/check")
        page = self.call("GET", "/api/admin/qa?document=changed")[1]
        self.assertEqual([r["question"] for r in page["rows"]], ["Force?"])
        self.assertEqual(page["total"], 1)

    def test_a_pair_not_found_cannot_be_approved(self):
        self.call("POST", "/api/admin/qa/check")
        rows = {r["question"]: r for r in self.call("GET", "/api/admin/qa")[1]["rows"]}
        code, out = self.call("POST", "/api/admin/qa/decide", {"ids": [rows["Force?"]["id"]], "status": "approved"})
        self.assertEqual(code, 409)
        code, out = self.call("POST", "/api/admin/qa/decide", {"ids": [rows["Gap?"]["id"]], "status": "approved"})
        self.assertEqual((code, out["changed"]), (200, 1))

    def test_approving_a_whole_document_leaves_out_what_is_not_found(self):
        self.call("POST", "/api/admin/qa/check")
        code, out = self.call("POST", "/api/admin/qa/decide", {"doc": "A.docx", "status": "approved", "only_pending": True})
        self.assertEqual((out["changed"], out["left_out"]), (1, 1))

    def test_a_hand_written_pair_needs_its_answer_in_the_document(self):
        code, out = self.call("POST", "/api/admin/qa/add", {"question": "Weld force?", "answer": "2.2 kN", "doc": "A.docx"})
        self.assertEqual(code, 200)
        self.assertEqual(out["added"], 1)
        code, out = self.call("POST", "/api/admin/qa/add", {"question": "Weld force?", "answer": "5 kN", "doc": "A.docx"})
        self.assertEqual(code, 400)
        self.assertIn("not in the document", out["error"])
        code, out = self.call("POST", "/api/admin/qa/add", {"question": "", "answer": "2.2 kN", "doc": "A.docx"})
        self.assertEqual(code, 400)

    def test_a_hand_written_pair_is_kept_as_manual_and_checked_found(self):
        self.call("POST", "/api/admin/qa/add", {"question": "Weld force?", "answer": "2.2 kN", "doc": "A.docx"})
        rows = self.call("GET", "/api/admin/qa")[1]["rows"]
        mine = next(r for r in rows if r["question"] == "Weld force?")
        self.assertEqual((mine["kind"], mine["in_document"]), ("manual", "found"))

    def test_edit_keeps_the_answer_tied_to_the_document(self):
        rows = {r["question"]: r for r in self.call("GET", "/api/admin/qa")[1]["rows"]}
        gap = rows["Gap?"]["id"]
        code, out = self.call("POST", "/api/admin/qa/edit", {"id": gap, "question": "What is the gap?", "answer": "2.2 kN", "area": "Fault Finder"})
        self.assertEqual(code, 200)
        again = {r["id"]: r for r in self.call("GET", "/api/admin/qa")[1]["rows"]}[gap]
        self.assertEqual((again["question"], again["answer"], again["area"], again["in_document"]), ("What is the gap?", "2.2 kN", "Fault Finder", "found"))
        code, out = self.call("POST", "/api/admin/qa/edit", {"id": gap, "question": "q", "answer": "7 kN"})
        self.assertEqual(code, 400)

    def test_an_area_must_be_a_known_one(self):
        code, out = self.call("POST", "/api/admin/qa/add", {"question": "Weld force?", "answer": "2.2 kN", "doc": "A.docx", "area": "Nowhere"})
        self.assertEqual(code, 400)

    def test_the_approve_block_can_be_turned_off(self):
        self.call("POST", "/api/admin/qa/check")
        self.assertTrue(self.call("GET", "/api/admin/qa/summary")[1]["only_found"])
        self.call("POST", "/api/admin/qa/only-found", {"on": False})
        rows = {r["question"]: r for r in self.call("GET", "/api/admin/qa")[1]["rows"]}
        code, out = self.call("POST", "/api/admin/qa/decide", {"ids": [rows["Force?"]["id"]], "status": "approved"})
        self.assertEqual((code, out["changed"]), (200, 1))

    def test_test_the_answers_reports_the_differences(self):
        self.queue.decide([r["id"] for r in self.call("GET", "/api/admin/qa")[1]["rows"]], "approved", "x")
        h = Handler(10, "/api/admin/qa/test")
        m.handle(h, self.queue, self.checks, self.chunks, "POST", "/api/admin/qa/test", {}, lambda: (Path("."), 5, True), lambda q: "the gap is 0.8 mm" if q == "Gap?" else "no idea")
        code, out = h.sent
        self.assertEqual((out["tested"], out["matched"]), (3, 1))
        self.assertEqual({d["question"] for d in out["differ"]}, {"Force?", "Old?"})

    def test_only_weld_engineers_and_admins(self):
        self.assertEqual(self.call("GET", "/api/admin/qa", level=1)[0], 403)
        self.assertEqual(self.call("GET", "/api/admin/system-check", level=1)[0], 403)


if __name__ == "__main__":
    unittest.main()
