"""Phase 2D: lists typed with a bullet character are real lists; a question that restates a list's lead-in gets the whole list; the fallback
sentence picker keeps a lead-in with its items. Expected answers come from the documents' own lists."""
import json
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from welding_ai import paths                                            # noqa: E402
from welding_ai.answering import answering                              # noqa: E402
from welding_ai.ingestion import ingest                                 # noqa: E402
from welding_ai.orchestration.harness import Config, Harness            # noqa: E402
from welding_ai.retrieval import rag, retrieval, roles                        # noqa: E402


class ListsAreLists(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = Harness(index=retrieval.load(), cfg=Config(policy="extractive", use_cache=False))
        cls.sec = json.loads((paths.INDEX_DIR / "sections.json").read_text(encoding="utf-8"))

    def pairs(self):
        out = []
        for s in self.sec:
            b = s["blocks"]
            i = 0
            while i < len(b):
                if b[i]["t"] == "li":
                    j = i
                    while j < len(b) and b[j]["t"] == "li":
                        j += 1
                    lead = b[i - 1]["text"] if i and b[i - 1]["t"] == "p" else ""
                    if j - i >= 3 and lead.endswith(":") and 25 <= len(lead) <= 160 and len(set(rag.tokens(lead))) >= 3:
                        out.append((lead.rstrip(":").strip(), [x["text"] for x in b[i:j]]))
                    i = j
                else:
                    i += 1
        return out

    def test_typed_list_markers_are_recognised(self):
        b = ingest.BULLET_CHARS[0]
        self.assertEqual(ingest.marker(f"{b} Moisture.  Sweat")[2], "Moisture. Sweat")
        self.assertEqual(ingest.marker("Step 3 - Close the gap")[:2], ("word:Step", 3))
        self.assertEqual(ingest.marker("2) Clamp both parts")[:2], ("number", 2))
        self.assertIsNone(ingest.marker("Moisture. Sweat and rain"))
        self.assertIsNone(ingest.marker(b + "x"))                       # a bullet glued to a word is not a marker

    def test_numbered_items_count_only_as_a_counting_run(self):
        items = ingest.typed_list_items(["1. one", "2. two", "3. three", "Plain sentence.", "2020. A year", "7. lone"])
        self.assertEqual(items[:3], ["one", "two", "three"])
        self.assertEqual(items[3:], [None, None, None])
        self.assertEqual(ingest.typed_list_items(["Step 1 - a", "Step 3 - b", "Step 4 - c"]), [None, None, None])      # does not count up by one
        self.assertEqual(ingest.typed_list_items(["- x", "y"]), [None, None])

    def test_no_paragraph_starts_with_a_typed_bullet(self):
        # bullets inside a table cell ("| Likely Causes | a bullet-separated list |") are cell text, not paragraphs, and are not covered here
        left = [c["heading"] for c in self.h.index.chunks if any(ln.lstrip().startswith(tuple(ingest.BULLET_CHARS)) for ln in c["text"].split(chr(10)))]
        self.assertEqual(left, [])

    def test_a_question_that_restates_a_lead_in_gets_the_whole_list(self):
        pairs = self.pairs()
        self.assertGreater(len(pairs), 80)
        miss = []
        for q, items in pairs:
            ans = self.h.run(q, mode="verbatim")["answer"].lower()
            if sum(1 for it in items if it[:35].lower() in ans) < 0.7 * len(items):
                miss.append(q)
        self.assertLessEqual(len(miss), len(pairs) * 0.06, miss[:5])

    def test_typed_step_runs_are_lists_in_every_document(self):
        runs = [(s["source"], s["title"]) for s in self.sec for b in s["blocks"] if b["t"] == "li" and b.get("num") and "Worked Example" in s["title"]]
        docs = {d for d, _ in runs}
        self.assertGreater(len(docs), 45)                               # the worked examples of the chapters ("Situation." + Step 1 ... Step 7)

    def test_the_reported_example(self):
        r = self.h.run("The conditions that make secondary shock dangerous", mode="verbatim")["answer"]
        for word in ("Moisture", "Confined", "Damaged insulation", "Two machines"):
            self.assertIn(word, r)
        self.assertNotIn("Incident:", r)

    def test_the_picker_keeps_a_lead_in_with_its_items(self):
        q = "The conditions that make secondary shock dangerous"
        hits = self.h.index.search(self.h.index.correct(q)[0], 5)
        text = answering.compose_extractive(self.h.index, q, hits)["text"]
        self.assertTrue(text.startswith("The conditions that make secondary shock dangerous:"))
        self.assertIn("Moisture", text)

    def test_a_fact_question_does_not_dump_a_whole_list(self):
        r = self.h.run("What is the mandatory action to avoid severe acute lung injury from cadmium during welding?", mode="verbatim")["answer"]
        self.assertLess(r.count("\n- "), 6, r[:300])


class StepsAndFollowUps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = Harness(index=retrieval.load(), cfg=Config(policy="extractive", use_cache=False))
        cls.sec = json.loads((paths.INDEX_DIR / "sections.json").read_text(encoding="utf-8"))

    def ask(self, q, history=None):
        return self.h.run(q, mode="verbatim", history=history or [])

    def test_typed_steps_answer_a_steps_question_with_all_the_steps(self):
        r = self.ask("what are the stpes after surveillance findings")
        self.assertIn("Recognize that the three findings share a cause", r["answer"])
        self.assertIn("Reject the proposed corrective action", r["answer"])
        self.assertGreaterEqual(r["answer"].count(chr(10) + "7. "), 1)

    def test_the_document_named_in_the_question_wins_between_equal_titles(self):
        for doc, word in (("gtaw", "GTAW"), ("brazing", "Brazing"), ("friction welding", "Friction Welding")):
            r = self.ask(f"{doc} setup and execution procedure steps")["answer"]
            self.assertIn(word, r.split(chr(10))[0], r[:120])

    def test_a_list_named_by_its_title_beats_a_loosely_matching_table(self):
        r = self.ask("what are the steps in Setup and Execution Procedure in Plasma Arc Welding")["answer"]
        self.assertTrue(r.startswith("**Setup and Execution Procedure**"), r[:100])

    def test_sections_with_typed_steps_are_answered_by_their_list(self):
        strip = lambda t: re.sub(r"^\d+\.\s*", "", t)
        count = Counter(strip(s["title"]).lower() for s in self.sec if s["level"] == 1)
        groups = {}
        for s in self.sec:
            if s["level"] == 1 and not roles.is_nav_title(s["title"]) and any(b["t"] == "li" and b.get("num") for b in s["blocks"]):
                groups[s["id"]] = s
        ok = 0
        for s in groups.values():
            title = strip(s["title"])
            doc = re.sub(r"^\d+_", "", s["source"].rsplit(".", 1)[0]).replace("_", " ")
            ans = self.ask(f"what are the steps in {title}" + (f" in {doc}" if count[title.lower()] > 1 else ""))["answer"].lower()
            runs, cur = [], []
            for b in s["blocks"]:
                if b["t"] == "li":
                    cur.append(b["text"])
                elif cur:
                    runs.append(cur)
                    cur = []
            runs.append(cur) if cur else None
            ok += any(sum(1 for it in run if it[:30].lower() in ans) >= 0.7 * len(run) for run in runs if run)
        self.assertGreater(len(groups), 100)
        self.assertGreaterEqual(ok / len(groups), 0.85)

    def test_a_follow_up_without_words_of_its_own_stays_on_the_topic(self):
        first = "what are the stpes after surveillance findings"
        r1 = self.ask(first)
        history = [{"role": "user", "content": first}, {"role": "assistant", "content": r1["answer"][:300]}]
        source = r1["sources"][0]["source"]
        for q in ("why was that response not enough?", "how long does it take", "give me more details"):
            r = self.ask(q, history)
            self.assertNotEqual(r["route"], "not_found", q)
            self.assertEqual(r["sources"][0]["source"], source, q)

    def test_a_follow_up_is_answered_from_what_was_just_shown(self):
        first = "How do I choose the right welding process for a job?"
        r1 = self.ask(first)
        plain = [{"role": "user", "content": first}, {"role": "assistant", "content": r1["answer"]}]
        with_ids = [plain[0], dict(plain[1], chunks=[s["chunk_id"] for s in r1["sources"][:3]])]
        for history in ([], plain, with_ids):                             # alone, with the answer's text, with the answer's passage ids
            r = self.ask("why does the order matter", history)
            self.assertIn("The order matters because", r["answer"], history and list(history[-1]))

    def test_a_question_that_opens_with_why_but_has_its_own_topic_stands_alone(self):
        histories = [[], [{"role": "user", "content": "I am spot welding 1.2 mm DP600 and I get expulsion. What should I do?"}, {"role": "assistant", "content": "Expulsion causes."}],
                     [{"role": "user", "content": "What is the electrode force for 1.0 mm mild steel?"}, {"role": "assistant", "content": "Starting values."}],
                     [{"role": "user", "content": "What is a weld nugget?"}, {"role": "assistant", "content": "The fused zone."}]]
        for history in histories:
            r = self.ask("Why does expulsion happen in spot welding?", history)
            self.assertEqual(r["route"], "composed", history[-1]["content"] if history else "no history")

    def test_history_does_not_make_an_off_topic_question_answerable(self):
        first = "what are the stpes after surveillance findings"
        history = [{"role": "user", "content": first}, {"role": "assistant", "content": "x"}]
        for q in ("what is the capital of France", "tell me a joke"):
            self.assertEqual(self.ask(q, history)["route"], "not_found", q)


if __name__ == "__main__":
    unittest.main()
