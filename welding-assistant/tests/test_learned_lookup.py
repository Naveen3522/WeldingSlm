"""An approved pair added by a person is used at once, by the meaning of its question, without training."""
import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

import model_check_routes as checks  # noqa: E402
from welding_ai.knowledge.embed import TextVectors  # noqa: E402
from welding_ai.knowledge.learned_lookup import LearnedLookup  # noqa: E402
from welding_ai.orchestration import grounded  # noqa: E402

CHUNKS = [{"source": "08_WPS.docx", "heading": "5. Nugget", "text": "The nugget diameter is 4√t to 5√t. Growth stops at expulsion.", "chunk_id": 0},
          {"source": "Added by hand", "heading": "Bracket gap", "text": "The gap for the 3 mm bracket is 3 mm.", "chunk_id": 1, "manual": True},
          {"source": "20_Guide.docx", "heading": "2. Table", "text": "Minimum 4.0 mm at 1.0 mm sheet.", "chunk_id": 2}]
WORDS = ["nugget", "diameter", "size", "gap", "bracket", "porosity", "heat", "input", "minimum", "what"]


def embed(texts):
    """A tiny stand-in for the embedding model: one dimension per topic word, so questions that share words are close."""
    out = []
    for text in texts:
        low = text.lower()
        v = [1.0 if w in low else 0.0 for w in WORDS]
        if text.startswith("search_document: ") or text.startswith("search_query: "):
            low = text.split(": ", 1)[1].lower()
            v = [1.0 if w in low else 0.0 for w in WORDS]
        out.append(v if any(v) else [0.0] * (len(WORDS) - 1) + [0.01])
    return out


def pair(pid, question, answer, kind="manual", status="approved", doc="08_WPS.docx", section="Nugget", refs=""):
    return {"id": pid, "uid": f"u{pid}", "question": question, "answer": answer, "kind": kind, "status": status, "doc": doc, "section": section, "refs": refs, "evidence": ""}


def lookup():
    vectors = TextVectors(tempfile.mkdtemp(), embed=embed)
    vectors._up = (time.time() + 1e6, True)
    return LearnedLookup(vectors)


class Lookup(unittest.TestCase):
    def test_a_close_question_finds_its_approved_pair_and_a_far_one_does_not(self):
        look = lookup()
        look.refresh([pair(1, "What is the nugget diameter?", "4√t to 5√t")])
        found = look.find("nugget size diameter what")
        self.assertEqual(found[0]["id"], 1)
        self.assertIsNone(look.find("what causes porosity"))

    def test_only_approved_pairs_added_by_people_count(self):
        look = lookup()
        self.assertEqual(look.refresh([pair(1, "What is the nugget diameter?", "x", status="pending"), pair(2, "What is the nugget diameter?", "x", kind="table"),
                                       pair(3, "What is the nugget diameter?", "x", status="rejected"), pair(4, "What is the nugget diameter?", "x", kind="suggested")]), 1)
        self.assertEqual(look.find("What is the nugget diameter?")[0]["id"], 4)

    def test_a_rejected_pair_drops_out_at_the_next_refresh(self):
        look = lookup()
        look.refresh([pair(1, "What is the nugget diameter?", "4√t to 5√t")])
        self.assertIsNotNone(look.find("What is the nugget diameter?"))
        look.refresh([pair(1, "What is the nugget diameter?", "4√t to 5√t", status="rejected")])
        self.assertIsNone(look.find("What is the nugget diameter?"))

    def test_the_passages_a_pair_stands_on_are_found(self):
        look = lookup()
        refs = json.dumps([{"doc": "08_WPS.docx", "text": "4√t to 5√t"}, {"doc": "20_Guide.docx", "text": "Minimum 4.0 mm"}])
        self.assertEqual([c["chunk_id"] for c in look.passages_of(pair(1, "q", "a", refs=refs), CHUNKS)], [0, 2])
        self.assertEqual([c["chunk_id"] for c in look.passages_of(pair(2, "q", "a", doc="Added by hand", section="Bracket gap"), CHUNKS)], [1])
        self.assertEqual(look.passages_of(pair(3, "q", "no longer there", doc="08_WPS.docx"), CHUNKS), [])        # the document changed


class Context(unittest.TestCase):
    def harness(self, pairs):
        look = lookup()
        look.refresh(pairs)

        def hit(cid, coverage=1.0):
            c = CHUNKS[cid]
            return {"chunk_id": cid, "source": c["source"], "heading": c["heading"], "text": c["text"], "score": 0.0, "coverage": coverage}
        return SimpleNamespace(learned=look, index=SimpleNamespace(chunks=CHUNKS), _hit=hit)

    def test_the_approved_answer_goes_first_with_its_passage_and_the_note_says_so(self):
        h = self.harness([pair(1, "What is the nugget diameter?", "4√t to 5√t")])
        ctx = SimpleNamespace(notes=[])
        other = {"chunk_id": 2, "source": "20_Guide.docx", "heading": "2. Table", "text": "x", "relevance": 0.4}
        chosen, match = grounded.with_approved(h, ctx, "nugget diameter size what", [other], "weak")
        self.assertEqual(match, "good")
        self.assertEqual([c["chunk_id"] for c in chosen], [0, 2])
        self.assertTrue(chosen[0]["text"].startswith("Approved answer: 4√t to 5√t"))
        self.assertIn("Manually referenced from document", ctx.notes[0])

    def test_an_own_passage_pair_says_manually_added(self):
        h = self.harness([pair(1, "What is the bracket gap?", "3 mm.", doc="Added by hand", section="Bracket gap")])
        ctx = SimpleNamespace(notes=[])
        chosen, _ = grounded.with_approved(h, ctx, "bracket gap what", [], "none")
        self.assertEqual([c["chunk_id"] for c in chosen], [1])
        self.assertIn("Manually added", ctx.notes[0])

    def test_no_close_pair_leaves_the_search_result_alone(self):
        h = self.harness([pair(1, "What is the nugget diameter?", "4√t to 5√t")])
        ctx = SimpleNamespace(notes=[])
        before = [{"chunk_id": 2, "source": "x", "heading": "h", "text": "t"}]
        self.assertEqual(grounded.with_approved(h, ctx, "what causes porosity", before, "weak"), (before, "weak"))
        self.assertEqual(ctx.notes, [])


class Answered(unittest.TestCase):
    def test_a_reply_that_words_the_answer_its_own_way_still_counts(self):
        self.assertTrue(checks.answered("4√t to 5√t", "The nugget is 4√t to 5√t thick."))
        self.assertTrue(checks.answered("The gap for the 3 mm bracket is 3 mm.", "For the 3 mm bracket the gap is 3 mm."))
        self.assertFalse(checks.answered("The gap for the 3 mm bracket is 3 mm.", "The gap for the 3 mm bracket is 5 mm."))      # a changed number never counts
        self.assertFalse(checks.answered("Growth stops at expulsion.", "The documents don't answer this directly."))


class Judge(unittest.TestCase):
    def test_a_pair_from_several_documents_is_found_only_while_every_highlight_is(self):
        texts = checks.document_texts(CHUNKS)
        refs = json.dumps([{"doc": "08_WPS.docx", "text": "4√t to 5√t"}, {"doc": "20_Guide.docx", "text": "Minimum 4.0 mm"}])
        p = pair(1, "q", "4√t to 5√t Minimum 4.0 mm", refs=refs)
        self.assertEqual(checks.judge(p, texts), "found")
        texts["20_Guide.docx"] = "something else"
        self.assertEqual(checks.judge(p, texts), "changed")
        del texts["20_Guide.docx"]
        self.assertEqual(checks.judge(p, texts), "missing")


if __name__ == "__main__":
    unittest.main()
