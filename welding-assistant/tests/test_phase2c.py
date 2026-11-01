"""Phase 2C: headings recovered from formatting, navigation lists kept out of answers, chapter and heading questions answered from the
documents' own structure. The expected answers are the documents' own headings and catalogue rows (no hand-written answers)."""
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from welding_ai.orchestration.harness import Config, Harness           # noqa: E402
from welding_ai.retrieval import retrieval, roles                      # noqa: E402

NUMBERING = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+")


class Structure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = Harness(index=retrieval.load(), cfg=Config(policy="extractive", use_cache=False))
        cls.e = cls.h.engine

    def ask(self, q):
        return self.h.run(q, mode="verbatim")

    def test_every_document_has_headings(self):
        per_doc = Counter(s["source"] for s in self.e.sec)
        self.assertEqual([d for d, n in per_doc.items() if n < 2], [], "documents whose headings were not recognised")

    def test_chunks_carry_their_section_heading(self):
        bare = [c for c in self.h.index.chunks if c["heading"] == c["source"].rsplit(".", 1)[0]]
        self.assertLess(len(bare) / len(self.h.index.chunks), 0.05)

    def test_navigation_lists_are_never_retrieved(self):
        navs = [s for s in self.e.sec if roles.is_nav_title(s["title"])]
        self.assertGreater(len(navs), 50)
        for s in navs[:40]:
            line = next((b["text"] for b in s["blocks"] if b["t"] in ("p", "li") and len(b["text"]) > 20), None)
            if not line:
                continue
            hits = self.h.index.search(NUMBERING.sub("", line), 5)
            self.assertFalse(any(roles.is_nav_heading(x["heading"]) for x in hits), line)

    def test_a_heading_asked_as_the_question_gives_that_section(self):
        titles = Counter(NUMBERING.sub("", s["title"]).strip().lower() for s in self.e.sec if s["level"] == 1 and re.match(r"^\s*\d+\.", s["title"]))
        unique = [s for s in self.e.sec if s["level"] == 1 and re.match(r"^\s*\d+\.", s["title"]) and titles[NUMBERING.sub("", s["title"]).strip().lower()] == 1]
        self.assertGreater(len(unique), 500)
        wrong = []
        for s in unique[::7]:                                          # every 7th unique heading (about 130 questions)
            title = NUMBERING.sub("", s["title"]).strip()
            r = self.ask(title + "?")
            shown = r["answer"].lower()[:len(title) + 40] + " " + " ".join(t["title"].lower() for t in r.get("tables", [])) + " " + " ".join(x["heading"].lower() for x in r["sources"][:3])
            if not (r["route"] == "composed" and title.lower() in shown):                # the section itself, or the table that is the section
                wrong.append((title, r["route"], r["answer"][:60]))
        self.assertLessEqual(len(wrong), len(unique[::7]) * 0.08, wrong[:8])

    def test_the_two_reported_examples(self):
        r = self.ask("Traceability: What It Means in Practice?")
        self.assertTrue(r["answer"].startswith("**Traceability: What It Means in Practice**"))
        self.assertGreater(len(r["answer"]), 300)                       # the section, not the heading echoed back
        r = self.ask("Failure Catalog?")
        self.assertIn("sections have this heading", r["answer"])
        self.assertGreaterEqual(len(r["tables"][0]["rows"]), 3)

    def test_chapter_questions_come_from_the_catalogue(self):
        cat = self.e._catalogue()
        self.assertEqual(len(cat), 78)
        bad = []
        for n, c in cat.items():
            r = self.ask(f"what is chapter {n} about")
            if not (c["title"] in r["answer"] and c["covers"][:40] in r["answer"]):
                bad.append(n)
        self.assertEqual(bad, [])

    def test_chapter_summary_lists_the_chapters_own_headings(self):
        c = self.e._catalogue()[5]
        r = self.ask("summarize chapter 5")
        heads = [re.sub(r"^\s*\d+\.\s*", "", s["title"]) for s in self.e._chapter_headings(c["source"])]
        self.assertGreaterEqual(len(heads), 5)
        self.assertTrue(all(h in r["answer"] for h in heads))

    def test_chapter_layouts(self):
        self.assertTrue(self.ask("chapter 33 in a table")["tables"])
        self.assertNotIn("Sections", self.ask("briefly summarize chapter 33")["answer"])

    def test_which_chapter_covers(self):
        cat = self.e._catalogue()
        miss = []
        for n in list(cat)[::4]:                                        # a phrase from each chapter's own Covers text
            words = cat[n]["covers"].split()[:7]
            r = self.ask("which chapter covers " + " ".join(words))
            rows = [row[0] for t in r.get("tables", []) for row in t["rows"]]
            if f"{n:02d}" not in rows:
                miss.append((n, " ".join(words)))
        self.assertLessEqual(len(miss), 3, miss)

    def test_counts(self):
        self.assertIn("78 chapters", self.ask("how many chapters are there")["answer"])
        self.assertIn("10 volumes", self.ask("how many volumes are there")["answer"])

    def test_a_question_that_names_a_chapter_and_a_topic_is_not_a_chapter_summary(self):
        r = self.ask("what does chapter 12 say about lockout")
        self.assertNotIn("**Covers:**", r["answer"])

    def test_glossary_words_are_not_mistaken_for_headings(self):
        for q in ("what is nugget", "what is porosity"):
            self.assertNotIn("sections have this heading", self.ask(q)["answer"], q)


if __name__ == "__main__":
    unittest.main()
