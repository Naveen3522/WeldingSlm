"""Question-and-answer pairs from new documents, their review queue, and the training datasets built from the approved ones."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from welding_ai import paths
from welding_ai.answering.answering import NOT_FOUND
from welding_ai.orchestration.prompts import SYSTEM_PROMPT
from welding_ai.retrieval import retrieval
from welding_ai.training import baseset, datasets, qa
from welding_ai.training.queue import QaError, QaQueue


class Fake:
    """A stand-in for the search index: a few chunks, a search that returns them in order, and a focus that just cuts."""

    def __init__(self):
        self.chunks = [{"source": f"{n:02d}_doc.docx", "heading": f"Part > Section {n}", "text": f"Passage {n} says something about topic {n} in some detail. " * 6} for n in range(8)]

    def search(self, question, k):
        return [{"source": c["source"], "heading": c["heading"], "text": c["text"]} for c in self.chunks[:k]]

    def focus(self, text, query, max_chars):
        return text[:max_chars]


def pair(doc="01_doc.docx", n=1):
    return {"doc": doc, "section": "Section 1", "question": f"What is the corrective action for Fault {n}?", "answer": f"Replace part {n} and re-test it.",
            "evidence": f"Fault {n} | Replace part {n} and re-test it.", "kind": "table"}


class RealTables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = retrieval.load()
        with open(paths.INDEX_DIR / "tables.json", encoding="utf-8") as fh:
            cls.tables = json.load(fh)

    def pairs(self, doc="74_Master_Defect_Cause_and_Corrective_Action_Index.docx"):
        return qa.generate(self.tables, self.index.chunks, [doc], self.index.focus)

    def test_pairs_come_from_the_named_document_only(self):
        pairs = self.pairs()
        self.assertGreater(len(pairs), 50)
        self.assertEqual({p["doc"] for p in pairs}, {"74_Master_Defect_Cause_and_Corrective_Action_Index.docx"})
        self.assertEqual(self.pairs("no_such.docx"), [])

    def test_every_answer_is_a_table_cell_word_for_word_and_is_in_the_passage_shown(self):
        by_doc = {}
        for t in self.tables:
            by_doc.setdefault(t["source"], []).append(t)
        cells = {re.sub(r"\s+", " ", c).strip() for t in by_doc["74_Master_Defect_Cause_and_Corrective_Action_Index.docx"] for r in t["rows"] for c in r}
        for p in self.pairs():
            self.assertIn(p["answer"], cells)
            self.assertIn(p["answer"].replace("|", "/").lower(), re.sub(r"\s+", " ", p["evidence"]).lower())

    def test_a_known_row_becomes_the_expected_question_and_answer(self):
        mine = {p["question"]: p["answer"] for p in self.pairs()}
        self.assertEqual(mine.get("What is the corrective action for Expulsion?") or mine.get("What is the corrective action for Expulsion (in 10. Resistance Welding Imperfections)?"),
                         "Reduce current or time; increase force; correct electrode alignment and fit-up")

    def test_no_pair_repeats_and_questions_are_questions(self):
        pairs = self.pairs()
        self.assertEqual(len({(p["question"], p["answer"]) for p in pairs}), len(pairs))
        self.assertTrue(all(p["question"].endswith("?") for p in pairs))
        self.assertTrue(all(10 <= len(p["question"]) <= 200 for p in pairs))

    def test_questions_are_grammatical_and_unambiguous_within_a_document(self):
        pairs = self.pairs("58_Ultrasonic_Metal_Welding.docx")
        self.assertTrue(pairs)
        self.assertFalse([p for p in pairs if re.match(r"^What is the (?!corrective action)[a-z ]+ for ", p["question"])])        # the awkward "What is the controls for ..." is gone
        self.assertTrue(any('under "Controls"' in p["question"] for p in pairs))
        for doc in ("74_Master_Defect_Cause_and_Corrective_Action_Index.docx", "39_GMAW_Gas_Metal_Arc_Welding.docx", "76_Welding_Economics_and_Productivity.docx"):
            seen = {}
            for p in self.pairs(doc):
                seen.setdefault(p["question"], set()).add(p["answer"])
            self.assertFalse([q for q, answers in seen.items() if len(answers) > 1], doc)

    def test_codes_and_chapter_columns_are_not_turned_into_facts(self):
        pairs = self.pairs()
        self.assertFalse([p for p in pairs if re.match(r"^What is the (chapter|code|ref|page)", p["question"], re.I)])


class Queue(unittest.TestCase):
    def setUp(self):
        self.q = QaQueue(":memory:", clock=lambda: 100.0)

    def test_pairs_are_approved_on_arrival_and_duplicates_are_skipped(self):
        r = self.q.add_many([pair(n=1), pair(n=2)])
        self.assertEqual((r["added"], r["skipped"], r["status"]), (2, 0, "approved"))
        again = self.q.add_many([pair(n=1), pair(n=3)])
        self.assertEqual((again["added"], again["skipped"]), (1, 1))
        self.assertEqual(self.q.counts()["approved"], 3)

    def test_with_automatic_approval_off_pairs_wait_for_review(self):
        self.q.set_auto_approve(False)
        self.assertFalse(self.q.counts()["auto_approve"])
        self.assertEqual(self.q.add_many([pair(n=1)])["status"], "pending")
        self.assertEqual(self.q.approved(), [])
        self.q.decide([self.q.page()["rows"][0]["id"]], "approved", "naveen")
        self.assertEqual(len(self.q.approved()), 1)
        self.assertEqual(self.q.approved()[0]["decided_by"], "naveen")

    def test_a_rejected_pair_is_never_used_and_stays_rejected_when_regenerated(self):
        self.q.add_many([pair(n=1), pair(n=2)])
        first = self.q.page()["rows"][-1]["id"]
        self.assertEqual(self.q.decide([first], "rejected", "naveen"), 1)
        self.assertEqual(len(self.q.approved()), 1)
        self.q.add_many([pair(n=1)])                                         # generated again: still the same pair, still rejected
        self.assertEqual(self.q.counts()["rejected"], 1)
        self.assertEqual(len(self.q.approved()), 1)

    def test_deciding_for_a_whole_document(self):
        self.q.set_auto_approve(False)
        self.q.add_many([pair("01_doc.docx", 1), pair("01_doc.docx", 2), pair("02_doc.docx", 3)])
        self.assertEqual(self.q.decide([], "approved", "naveen", doc="01_doc.docx"), 2)
        self.assertEqual(self.q.counts()["pending"], 1)
        self.assertEqual(self.q.decide([], "rejected", "naveen", doc="01_doc.docx", only_pending=True), 0)    # nothing pending there any more
        with self.assertRaises(QaError):
            self.q.decide([1], "maybe", "naveen")
        self.assertEqual(self.q.decide([], "approved", "naveen"), 0)

    def test_filters_paging_and_search(self):
        self.q.add_many([pair("01_doc.docx", n) for n in range(1, 8)] + [pair("02_doc.docx", 9)])
        self.assertEqual(self.q.page(doc="02_doc.docx")["total"], 1)
        self.assertEqual(len(self.q.page(limit=3)["rows"]), 3)
        self.assertEqual(self.q.page(limit=3, offset=6)["rows"].__len__(), 2)
        self.assertEqual(self.q.page(text="Fault 4")["total"], 1)
        self.assertEqual(self.q.page(text="100%")["total"], 0)
        self.assertEqual([d["n"] for d in self.q.documents()], [7, 1])

    def test_pairs_from_a_changed_document_are_retired_and_new_ones_for_it_are_still_added(self):
        self.q.add_many([pair("01_doc.docx", 1), pair("01_doc.docx", 2), pair("02_doc.docx", 3)])
        self.assertEqual(self.q.retire(["01_doc.docx"]), 2)
        self.assertEqual([r["doc"] for r in self.q.approved()], ["02_doc.docx"])
        self.assertEqual(self.q.retire(["01_doc.docx"]), 0)                                  # already retired
        self.assertEqual(self.q.retire([]), 0)
        again = self.q.add_many([pair("01_doc.docx", 1), pair("01_doc.docx", 9)])            # the unchanged pair stays rejected; the new one is accepted
        self.assertEqual(again["added"], 1)
        self.assertEqual(len(self.q.approved()), 2)

    def test_used_pairs_are_remembered_by_dataset_version(self):
        self.q.add_many([pair(n=1), pair(n=2)])
        ids = [r["id"] for r in self.q.approved()]
        self.assertEqual(self.q.counts()["unused"], 2)
        self.q.mark_used(ids[:1], "v3-a")
        self.q.mark_used(ids[:1], "v3-b")
        self.assertEqual(self.q.counts()["unused"], 1)
        self.assertEqual(len(self.q.approved(only_unused=True)), 1)
        self.assertEqual(self.q.approved()[0]["used_in"], "v3-a,v3-b")


class Datasets(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.index = Fake()
        old = [{"kind": k, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"old {i}"}, {"role": "assistant", "content": "old answer"}]}
               for i, k in enumerate(["gold"] * 80 + ["wrong_passage"] * 15 + ["empty"] * 5)]
        self.train, self.dev = self.root / "train.jsonl", self.root / "dev.jsonl"
        self.train.write_text("\n".join(json.dumps(r) for r in old), encoding="utf-8")
        self.dev.write_text("\n".join(json.dumps(r) for r in old[:40]), encoding="utf-8")
        self.approved = [pair("05_doc.docx", n) for n in range(1, 41)]

    def make(self, mode, approved=None):
        return datasets.make(self.approved if approved is None else approved, self.index, mode, self.train, self.dev, out_root=self.root / "out", seed=1, clock=lambda: 1_700_000_000)

    def rows(self, meta, name="train"):
        return [json.loads(l) for l in (Path(meta["dir"]) / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_a_gold_row_looks_exactly_like_a_served_question_and_cites_the_right_passage(self):
        meta = self.make("short")
        gold = [r for r in self.rows(meta) if r["kind"] == "gold" and "Fault" in r["messages"][1]["content"]]
        self.assertEqual(len(gold), 40)
        for r in gold:
            system, user, assistant = (m["content"] for m in r["messages"])
            self.assertEqual(system, SYSTEM_PROMPT)
            self.assertTrue(user.startswith("EXCERPTS:\n[1] (document: "))
            self.assertIn("\n\nQUESTION: What is the corrective action for Fault", user)
            cited = int(re.search(r"\[(\d)\]$", assistant).group(1))
            block = re.split(r"\n\n(?=\[\d\] \(document:)", user.split("EXCERPTS:\n", 1)[1].split("\n\nQUESTION:")[0])[cited - 1]
            self.assertIn("05_doc.docx", block)                                # the number cited is the block the answer came from

    def test_wrong_passage_rows_never_contain_the_right_document_and_refuse(self):
        for r in self.rows(self.make("short")):
            if r["kind"] == "wrong_passage" and "Fault" in r["messages"][1]["content"]:
                self.assertNotIn("05_doc.docx", r["messages"][1]["content"])
                self.assertEqual(r["messages"][2]["content"], NOT_FOUND)

    def test_empty_rows_have_no_passages_and_refuse(self):
        empty = [r for r in self.rows(self.make("short")) if r["kind"] == "empty" and "Fault" in r["messages"][1]["content"]]
        self.assertTrue(empty)
        for r in empty:
            self.assertIn("(no passages were found)", r["messages"][1]["content"])
            self.assertEqual(r["messages"][2]["content"], NOT_FOUND)

    def test_the_modes(self):
        full, short, test = self.make("full"), self.make("short"), self.make("test")
        for meta in (full, short):                                             # both build the fresh set from the documents, at most the target size
            self.assertLessEqual(meta["rows"], baseset.TARGET)
            self.assertEqual(meta["rows"], meta["new_rows"] + meta["replay_rows"])
            self.assertIn("report", meta)
        self.assertEqual((test["rows"], test["dev_rows"]), (datasets.TEST_ROWS, datasets.TEST_DEV))
        self.assertEqual(full["dev_rows"], 40)

    def test_the_dev_set_is_the_existing_dev_set_and_never_contains_new_rows(self):
        dev = self.rows(self.make("full"), "dev")
        self.assertEqual(len(dev), 40)
        self.assertFalse([r for r in dev if "Fault" in r["messages"][1]["content"]])

    def test_the_same_inputs_give_the_same_dataset(self):
        a, b = self.make("short"), self.make("short")
        self.assertEqual((Path(a["dir"]) / "train.jsonl").read_text(encoding="utf-8"), (Path(b["dir"]) / "train.jsonl").read_text(encoding="utf-8"))
        self.assertRegex(a["version"], r"^v3-\d{8}-\d{6}$")

    def test_nothing_to_train_on_is_refused_except_for_a_full_retrain(self):
        for mode in ("short", "test"):
            with self.assertRaises(ValueError):
                self.make(mode, approved=[])
        self.assertEqual(self.make("full", approved=[])["new_rows"], 0)
        with self.assertRaises(ValueError):
            self.make("weekly")

    def test_meta_describes_the_dataset(self):
        meta = self.make("short")
        on_disk = json.loads((Path(meta["dir"]) / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual((on_disk["mode"], on_disk["pairs"], on_disk["documents"]), ("short", 40, ["05_doc.docx"]))
        self.assertTrue(meta["version"].startswith("v3-"))


if __name__ == "__main__":
    unittest.main()
