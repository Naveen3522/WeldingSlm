"""The fresh training set: pruned, topped up with guards, always the same size."""
import unittest

import numpy as np

from welding_ai.knowledge import references
from welding_ai.training import baseset

SYSTEM = {"role": "system", "content": "s"}


def row(kind, doc, answer, shown=None):
    shown = shown or [doc]
    blocks = "\n\n".join(f"[{i + 1}] (document: {d} | section: S)\ntext" for i, d in enumerate(shown))
    return {"kind": kind, "messages": [SYSTEM, {"role": "user", "content": f"EXCERPTS:\n{blocks}\n\nQUESTION: q"}, {"role": "assistant", "content": answer}]}


def pair(doc, question, answer, used=False):
    return {"doc": doc, "question": question, "answer": answer, "used_in": "v1" if used else ""}


class FakeVectors:
    """Questions that start with the same word get the same direction."""

    def ensure(self, texts):
        return True

    def get(self, texts):
        out = np.zeros((len(texts), 8))
        for i, t in enumerate(texts):
            out[i, hash(t.split()[0]) % 8] = 1.0
        return out


class Prune(unittest.TestCase):
    def test_rows_of_a_deleted_document_are_dropped(self):
        rows = [row("gold", "a.docx", "argon shielding gas flow"), row("gold", "gone.docx", "argon shielding gas flow")]
        kept, why = baseset.prune(rows, {"a.docx": "argon shielding gas flow rate"})
        self.assertEqual(len(kept), 1)
        self.assertEqual(why["a document it shows was deleted"], 1)

    def test_a_gold_answer_no_longer_in_its_document_is_dropped(self):
        rows = [row("gold", "a.docx", "[1] nickel alloys require preheating temperatures considerably higher")]
        rows[0]["messages"][2]["content"] = "nickel alloys require preheating temperatures considerably higher [1]"
        kept, why = baseset.prune(rows, {"a.docx": "completely different text about aluminium"})
        self.assertEqual(kept, [])
        self.assertEqual(why["its answer is no longer in the document"], 1)


class Select(unittest.TestCase):
    docs = {"a.docx": "x", "b.docx": "x", "held.docx": "x"}

    def test_guards(self):
        pairs = [pair("held.docx", "q1", "a long enough answer about the weld"), pair("gone.docx", "q2", "a long enough answer about the weld"),
                 pair("a.docx", "q3", "short"), pair("a.docx", "q4", "4.5 kN at 1 mm"), pair("a.docx", "q5", "a long enough answer about the weld")]
        picked, report = baseset.select(pairs, {"held.docx"}, self.docs)
        self.assertEqual(sorted(p["question"] for p in picked), ["q4", "q5"])
        self.assertEqual(report["from a held-out test document"], 1)
        self.assertEqual(report["from a document that is gone"], 1)
        self.assertEqual(report["answer too short"], 1)

    def test_near_copies_are_merged_by_embedding_within_a_document(self):
        pairs = [pair("a.docx", "Alpha one?", "answer words that are long enough"), pair("a.docx", "Alpha two?", "answer words that are long enough"),
                 pair("b.docx", "Alpha one?", "answer words that are long enough")]
        picked, report = baseset.select(pairs, set(), self.docs, vectors=FakeVectors())
        self.assertEqual(len(picked), 2)
        self.assertEqual(report["merged: asks what another already asks"], 1)

    def test_a_pointer_is_followed_into_the_chapter_and_section(self):
        chunks = [{"source": "47_Weld_Quality.docx", "heading": "Chapter 47 > 4. Common Defects", "text": "porosity causes"}]
        pairs = [pair("a.docx", "Q?", "Controls (Chapter 47, Section 4)")]
        picked, report = baseset.select(pairs, set(), self.docs, chapters=references.Chapters(chunks))
        self.assertEqual(picked[0]["extra"], [0])
        self.assertEqual(report["pointer followed"], 1)

    def test_not_yet_trained_pairs_come_first(self):
        pairs = [pair("a.docx", "old", "answer words that are long enough", used=True), pair("a.docx", "new", "answer words that are long enough")]
        picked, _ = baseset.select(pairs, set(), self.docs)
        self.assertEqual(picked[0]["question"], "new")

    def test_one_document_cannot_fill_the_set(self):
        pairs = [pair("a.docx", f"w{i} q", f"answer words that are long enough {i}") for i in range(500)]
        picked, report = baseset.select(pairs, set(), self.docs)
        self.assertEqual(len(picked), round(1950 * baseset.DOC_SHARE))
        self.assertTrue(report["over one document's share"])


class Build(unittest.TestCase):
    def test_always_exactly_the_target_with_the_v2_proportions(self):
        docs = {f"d{i}.docx": "argon shielding gas flow rate" for i in range(40)}
        base = [row(k, f"d{i % 40}.docx", f"argon shielding gas flow [1]" if k == "gold" else "NOT_FOUND") for i, k in
                enumerate(["gold"] * 1950 + ["wrong_passage"] * 450 + ["contrastive"] * 450 + ["empty"] * 150)]
        pairs = [pair(f"d{i % 40}.docx", f"w{i} question text", f"a new answer with enough words {i}") for i in range(900)]
        make_gold = lambda p: row("gold", p["doc"], p["answer"])
        make_ref = lambda pool, kind, n: [row(kind, pool[i % len(pool)]["doc"], "NOT_FOUND") for i in range(n)]
        rows, report = baseset.build(base, set(), pairs, None, docs, make_gold, make_ref)
        self.assertEqual(len(rows), baseset.TARGET)
        self.assertEqual(report["by kind"], {"gold": 1950, "wrong_passage": 450, "contrastive": 450, "empty": 150})
        self.assertGreater(report["new rows added"], 0)
        self.assertEqual(report["short by"], 0)

    def test_a_deleted_document_is_made_up_from_the_pool(self):
        docs = {f"d{i}.docx": "argon shielding gas flow rate" for i in range(40)}
        base = [row("gold", f"d{i % 41}.docx", "argon shielding gas flow [1]") for i in range(1950)] + [row("wrong_passage", "d1.docx", "NOT_FOUND")] * 1050
        pairs = [pair(f"d{i % 40}.docx", f"w{i} question text", f"a new answer with enough words {i}") for i in range(900)]
        rows, report = baseset.build(base, set(), pairs, None, docs, lambda p: row("gold", p["doc"], p["answer"]),
                                     lambda pool, kind, n: [row(kind, pool[0]["doc"], "NOT_FOUND") for _ in range(n)])
        self.assertEqual(len(rows), baseset.TARGET)
        self.assertTrue(report["base rows dropped"])


if __name__ == "__main__":
    unittest.main()
