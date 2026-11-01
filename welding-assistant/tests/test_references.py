"""A passage that points to "Chapter 47, Section 4" has that section read as well, when an answer is made and when training rows are made."""
import unittest
from types import SimpleNamespace

from welding_ai.knowledge import references
from welding_ai.orchestration import grounded

CHUNKS = [
    {"source": "12_Troubleshooting.docx", "heading": "Chapter 12 > 3. Porosity", "text": "Porosity: check the gas. Standard controls (Chapter 47, Section 4).", "chunk_id": 0},
    {"source": "47_Arc_Defects.docx", "heading": "Chapter 47 > 3. Other things", "text": "Not this one.", "chunk_id": 1},
    {"source": "47_Arc_Defects.docx", "heading": "Chapter 47 > 4. Standard porosity controls", "text": "Clean the joint, set the flow, screen the draught.", "chunk_id": 2},
    {"source": "47_Arc_Defects.docx", "heading": "Chapter 47 > 5. Spatter", "text": "Lower the voltage.", "chunk_id": 3},
    {"source": "20_Guide.docx", "heading": "Chapter 20 > 1. Intro", "text": "See Chapter 47 for defects.", "chunk_id": 4},
    {"source": "Added by hand", "heading": "Gap", "text": "Chapter 12 manual", "chunk_id": 5, "manual": True},
]


class Find(unittest.TestCase):
    def test_references_are_found_in_the_order_written(self):
        self.assertEqual(references.find("see (Chapter 47, Section 4) and Ch 5, Sec. 2, also Chapter 47, Section 4 and chapter 9"), [(47, 4), (5, 2), (9, None)])
        self.assertEqual(references.find("no pointers here"), [])

    def test_the_chapter_is_the_number_in_the_file_name(self):
        self.assertEqual((references.chapter_of("47_Arc_Defects.docx"), references.chapter_of("Added by hand")), (47, None))

    def test_a_section_is_found_by_its_heading_and_a_chapter_by_the_best_score(self):
        chapters = references.Chapters(CHUNKS)
        self.assertEqual(chapters.resolve(47, 4), [2])
        self.assertEqual(chapters.resolve(47, None, score=lambda i: {1: 0.1, 2: 0.2, 3: 0.9}[i]), [3])
        self.assertEqual(chapters.resolve(99, 1), [])
        self.assertEqual(chapters.resolve(47, 4, skip={2}), [])                      # already in hand
        self.assertNotIn(5, chapters._by.get(12, []))                                # a passage written by hand belongs to no chapter


class Follow(unittest.TestCase):
    def harness(self):
        def hit(cid, coverage=1.0):
            c = CHUNKS[cid]
            return {"chunk_id": cid, "source": c["source"], "heading": c["heading"], "text": c["text"], "score": 0.0, "coverage": coverage}
        index = SimpleNamespace(chunks=CHUNKS, _coverage=lambda q, i: 0.3)
        return SimpleNamespace(index=index, _hit=hit)

    def test_the_section_a_passage_points_to_is_added_after_it(self):
        h, ctx = self.harness(), SimpleNamespace(notes=[])
        chosen = [h._hit(0)]
        out = grounded.follow_references(h, ctx, "How do I stop porosity?", chosen)
        self.assertEqual([c["chunk_id"] for c in out], [0, 2])
        self.assertTrue(out[1]["via_reference"])
        self.assertIn("Chapter 47, Section 4", ctx.notes[0])

    def test_a_chapter_only_pointer_to_the_same_chapter_is_ignored_and_nothing_repeats(self):
        h, ctx = self.harness(), SimpleNamespace(notes=[])
        self.assertEqual([c["chunk_id"] for c in grounded.follow_references(h, ctx, "q", [h._hit(2)])], [2])           # nothing points elsewhere
        both = grounded.follow_references(h, ctx, "q", [h._hit(0), h._hit(2)])
        self.assertEqual([c["chunk_id"] for c in both], [0, 2])                                                       # section 4 is already in hand


if __name__ == "__main__":
    unittest.main()
