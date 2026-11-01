"""The fact layer: facts read from the documents' structure, facets matched to the documents' own column headers, questions split into asks, each answered from the right cell.

The tests run on the real index. Word matching is tested everywhere; matching by meaning only when the local embedding model answers.
"""
import json
import unittest
import urllib.request

from welding_ai import paths
from welding_ai.answering.facts import FactAnswerer
from welding_ai.core.config import Config
from welding_ai.knowledge import units as units_mod
from welding_ai.knowledge.catalogue import capabilities
from welding_ai.knowledge.embed import TextVectors
from welding_ai.orchestration.harness import Harness
from welding_ai.retrieval import retrieval
from welding_ai.understanding import asks as asks_mod

SECTIONS = json.loads((paths.INDEX_DIR / "sections.json").read_text(encoding="utf-8"))


def ollama_up():
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1.5).read()
        return True
    except Exception:
        return False


class Extraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.units = units_mod.extract(SECTIONS)

    def test_every_document_with_a_table_or_card_has_facts(self):
        have = {u.source for u in self.units}
        with_tables = {s["source"] for s in SECTIONS if any(b["t"] == "table" for b in s["blocks"])}
        self.assertTrue(with_tables <= have)
        self.assertGreater(len(have), 70)

    def test_the_three_kinds_of_fact(self):
        kinds = {u.kind for u in self.units}
        self.assertEqual(kinds, {"row", "card", "item"})

    def test_a_table_row_is_an_entity_with_named_attributes(self):
        tip = next(u for u in self.units if u.source.startswith("39_") and u.entity == "Contact tip")
        self.assertEqual(tip.kind, "row")
        self.assertIn("function", dict(tip.attrs))
        self.assertIn("failure consequence", dict(tip.attrs))

    def test_a_card_gathers_its_labelled_lines(self):
        card = next(u for u in self.units if u.kind == "card" and u.source.startswith("38_"))
        self.assertTrue({"symptom", "likely causes", "steps"} <= {label for label, _v in card.attrs})

    def test_labels_are_labels_not_sentences(self):
        labels = {label for u in self.units for label, _v in u.attrs}
        for label in labels:
            self.assertLessEqual(len(label.split()), 6, label)
        self.assertNotIn("the consequences are consistent", labels)

    def test_a_label_only_one_line_uses_is_not_a_facet(self):
        used = {}
        for u in self.units:
            if u.kind == "item":
                used[u.attrs[0][0]] = used.get(u.attrs[0][0], 0) + 1
        self.assertTrue(all(n >= units_mod.MIN_ITEM_LABEL_USES for n in used.values()))

    def test_every_fact_points_at_a_passage(self):
        with_chunk = sum(1 for u in self.units if u.chunk is not None)
        self.assertGreater(with_chunk / len(self.units), 0.98)


class Facets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.answerer = FactAnswerer(SECTIONS, None)             # by words only
        cls.facets = cls.answerer.facets

    def test_the_vocabulary_is_the_documents_own_headers(self):
        for label in ("corrective action", "failure consequence", "symptom if wrong", "how it is detected", "likely causes"):
            self.assertIn(label, self.facets.labels)

    def test_labels_of_one_concept_belong_together(self):
        cause = self.facets.family("cause")
        self.assertTrue({"causes", "likely causes", "gtaw-specific causes"} <= cause)
        self.assertNotIn("corrective action", cause)
        self.assertIn("failure consequence", self.facets.family("consequence"))

    def test_general_facet_words_are_told_from_the_things_asked_about(self):
        lex = self.answerer.lexicon
        for w in ("causes", "consequences", "detected", "function", "corrective", "governs", "required"):
            self.assertTrue(lex.is_facet(w), w)
        for w in ("welding", "current", "preheat", "expulsion", "porosity", "tip"):
            self.assertFalse(lex.is_facet(w), w)

    def test_tasks_come_from_the_labels(self):
        self.assertEqual(self.facets.task_of("corrective action"), "troubleshoot")
        self.assertEqual(self.facets.task_of("what it controls"), "parameters")
        self.assertEqual(self.facets.task_of("function"), "equipment")
        self.assertEqual(self.facets.task_of("title"), "reference")

    def test_a_new_documents_column_joins_its_concept(self):
        sections = json.loads(json.dumps(SECTIONS))
        sections.append({"source": "99_New.docx", "path": "Chapter 99 - New (NEW)", "title": "1. Defects", "level": 1, "id": 99999, "blocks": [
            {"t": "table", "header": ["Defect", "Detection", "NEW-specific causes", "Corrective action"], "rows": [["Zorp", "Visual", "Too much flux", "Use less flux"]], "chunk": 0}]})
        added = FactAnswerer(sections, None)
        self.assertIn("new-specific causes", added.facets.family("cause"))
        seen = added.understand("What causes Zorp?")
        self.assertTrue(any(lk.matches for g in seen.looked for lk in g))


class Parsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = FactAnswerer(SECTIONS, None)

    def parse(self, question):
        return asks_mod.parse(question, self.a.lexicon, self.a.facets, self.a.topics)

    def test_a_question_is_split_into_its_asks(self):
        parts = asks_mod.clauses("Why does expulsion happen, what are its consequences and which standard governs spot weld acceptance?")
        self.assertEqual(len(parts), 3)
        self.assertEqual(len(asks_mod.clauses("What are the causes, detection and corrective action of porosity?")), 1)

    def test_the_thing_asked_about_is_told_from_what_is_wanted(self):
        ask = self.parse("What is the function of the contact tip in GMAW?")[0]
        self.assertEqual(ask.entity_words, ["contact", "tip"])
        self.assertEqual(len(ask.facets), 1)
        self.assertEqual(ask.facets[0].task, "equipment")

    def test_several_facets_after_one_subject_are_several_asks_of_it(self):
        ask = self.parse("What are the causes and corrective action of undercut?")[0]
        self.assertEqual(len(ask.facets), 2)
        self.assertEqual(ask.entity_words, ["undercut"])

    def test_it_means_what_was_just_asked_about(self):
        a, b = self.parse("What causes porosity and how is it detected?")
        self.assertEqual(b.entity_words, a.entity_words)
        self.assertTrue(b.carried)

    def test_the_subject_can_come_after_the_asks(self):
        a, b = self.parse("Tell me what causes it and how to detect it for porosity")
        self.assertEqual(a.entity_words, ["porosity"])
        self.assertTrue(a.carried)

    def test_a_named_process_is_a_topic_not_part_of_the_subject(self):
        ask = self.parse("What causes porosity in GTAW?")[0]
        self.assertEqual(ask.entity_words, ["porosity"])
        self.assertTrue(any(s.startswith("41_") for s in ask.topics))

    def test_a_follow_up_is_written_out_as_a_complete_question(self):
        _a, b = self.parse("What causes expulsion and what are its consequences?")
        self.assertIn("consequences of expulsion", b.resolved)

    def test_a_question_without_a_facet_is_left_alone(self):
        ask = self.parse("What is preheat?")[0]
        self.assertEqual(ask.facets, [])


class Lookup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = FactAnswerer(SECTIONS, None)

    def lookup(self, question, index=0):
        seen = self.a.understand(question)
        return seen, [lk for group in seen.looked for lk in group]

    def test_only_the_asked_cell_comes_back(self):
        _seen, looked = self.lookup("What is the function of the contact tip in GMAW?")
        match = looked[0].matches[0]
        self.assertEqual(match.unit.entity, "Contact tip")
        self.assertEqual([label for label, _v in match.cells], ["function"])

    def test_a_named_process_limits_the_documents(self):
        _seen, looked = self.lookup("What causes porosity in GTAW?")
        self.assertTrue(looked[0].matches)
        self.assertTrue(all(m.unit.source.startswith("41_") for m in looked[0].matches))

    def test_without_a_process_other_documents_are_named(self):
        _seen, looked = self.lookup("What causes undercut?")
        self.assertTrue(looked[0].others)

    def test_a_fact_the_documents_do_not_state_is_not_invented(self):
        _seen, looked = self.lookup("What is the function of the flux capacitor?")
        self.assertFalse(any(lk.matches for lk in looked))

    def test_the_subject_must_really_be_the_rows_subject(self):
        seen, looked = self.lookup("What are the causes of undercut in GMAW?")
        for m in looked[0].matches:
            self.assertIn("undercut", m.unit.entity.lower())


def harness(facts):
    index = retrieval.load()
    h = Harness(index=index, cfg=Config(use_cache=False))
    h.facts = facts
    return h


def text_of(evs):
    return "\n".join(e["t"] for e in evs if e["type"] == "token")


class Answering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts = FactAnswerer(SECTIONS, None)
        cls.h = harness(cls.facts)

    def ask(self, q, history=None):
        return list(self.h.stream(q, history=history or [], mode="verbatim"))

    def test_two_facets_of_one_thing_come_from_two_columns(self):
        evs = self.ask("What is the function of the contact tip in GMAW and what is the consequence if it fails?")
        text = text_of(evs)
        self.assertEqual(evs[-1]["route"], "facts")
        self.assertIn("Transfers current to the wire", text)                  # the function cell
        self.assertIn("Wear enlarges the bore", text)                         # the failure consequence cell
        self.assertEqual(text.count("## "), 1)                                  # one heading for one subject
        self.assertIn("## Contact tip", text)

    def test_every_cite_marker_points_at_a_listed_source(self):
        import re
        evs = self.ask("What causes porosity in GTAW and how is it detected?")
        used = {int(n) for n in re.findall(r"\[(\d+)\]", text_of(evs))}
        listed = len(next(e for e in evs if e["type"] == "sources")["sources"])
        self.assertTrue(used and max(used) <= listed)

    def test_the_answer_says_how_the_question_was_understood(self):
        evs = self.ask("What causes porosity in GTAW and how is it detected?")
        plan = next(e for e in evs if e["type"] == "plan")
        self.assertIn("Troubleshooting", plan["tasks"])
        self.assertEqual(len(plan["asks"]), 2)
        stages = [e["id"] for e in evs if e["type"] == "stage"]
        self.assertIn("ask0", stages)

    def test_a_part_the_facts_cannot_answer_goes_to_the_ordinary_pipeline_and_is_not_dropped(self):
        evs = self.ask("What is the function of the contact tip in GMAW and what is preheat?")
        text = text_of(evs)
        self.assertIn("Transfers current", text)
        self.assertIn("reheat", text)

    def test_a_part_nothing_answers_is_reported_not_guessed(self):
        evs = self.ask("What is the function of the contact tip in GMAW and what is the price of a flux capacitor?")
        text = text_of(evs)
        self.assertIn("Transfers current", text)
        self.assertNotRegex(text, r"\$\s*\d")

    def test_a_single_plain_question_is_unchanged(self):
        with_facts = self.ask("What is a weld nugget?")
        without = list(harness(None).stream("What is a weld nugget?", history=[], mode="verbatim"))
        self.assertEqual(text_of(with_facts), text_of(without))
        self.assertNotEqual(with_facts[-1]["route"], "facts")

    def test_nested_parts_do_not_split_again(self):
        stages = [e["id"] for e in self.h.stream("What causes porosity in GTAW and how is it detected?", history=[], mode="verbatim", nested=True) if e["type"] == "stage"]
        self.assertNotIn("ask0", stages)

    def test_without_the_fact_layer_the_pipeline_is_as_before(self):
        evs = list(harness(None).stream("What causes porosity in GTAW and how is it detected?", history=[], mode="verbatim"))
        self.assertNotEqual(evs[-1]["route"], "facts")

    def test_the_provenance_label_is_quoted_from_the_documents(self):
        evs = self.ask("What causes porosity in GTAW and how is it detected?")
        self.assertEqual(evs[-1]["provenance"]["kind"], "quoted")


class Capabilities(unittest.TestCase):
    def test_the_catalogue_is_made_from_the_documents(self):
        cap = capabilities(FactAnswerer(SECTIONS, None))
        names = {t["id"] for t in cap["tasks"]}
        self.assertTrue({"troubleshoot", "parameters", "equipment", "standards"} <= names)
        trouble = next(t for t in cap["tasks"] if t["id"] == "troubleshoot")
        self.assertGreater(len(trouble["documents"]), 40)
        self.assertEqual(len(trouble["examples"]), 3)

    def test_a_new_chapter_widens_a_task(self):
        sections = json.loads(json.dumps(SECTIONS))
        sections.append({"source": "99_New.docx", "path": "Chapter 99 - Plasma Spray (PS)", "title": "1. Defects", "level": 1, "id": 99999, "blocks": [
            {"t": "table", "header": ["Defect", "Cause", "Corrective action"], "rows": [["Zorp", "Too much flux", "Use less flux"], ["Blarg", "Cold gun", "Warm the gun"]], "chunk": 0}]})
        before = capabilities(FactAnswerer(SECTIONS, None))
        after = capabilities(FactAnswerer(sections, None))
        count = lambda c: len(next(t for t in c["tasks"] if t["id"] == "troubleshoot")["documents"])
        self.assertEqual(count(after), count(before) + 1)


@unittest.skipUnless(ollama_up(), "the local embedding model is not running")
class ByMeaning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = FactAnswerer(SECTIONS, TextVectors(paths.INDEX_DIR))
        cls.a.prepare()

    def test_a_phrase_is_matched_to_the_documents_header_by_meaning(self):
        hit = self.a.facets.match(["what happens if it goes wrong"])[0][0]
        self.assertTrue(hit.semantic)
        self.assertIn("symptom if wrong", self.a.facets.family(hit.label))

    def test_consequences_and_who_governs_are_found_without_a_word_list(self):
        seen = self.a.understand("Why does expulsion happen in spot welding, what are its consequences and which standard governs spot weld acceptance?")
        tasks = {f.task for ask in seen.asks for f in ask.facets}
        self.assertTrue({"troubleshoot", "standards"} <= tasks)
        self.assertTrue(seen.takes_over)

    def test_the_same_question_in_other_words(self):
        for q in ("Tell me why it happens and how to fix it for Hydrogen cracking", "What is the contact tip for and what goes wrong when it fails in GMAW?"):
            seen = self.a.understand(q)
            self.assertTrue(any(lk.matches for g in seen.looked for lk in g), q)


class GenericWps(unittest.TestCase):
    def test_a_draft_for_a_process_chapter_leaves_every_value_to_the_engineer(self):
        from welding_ai.agents import wps_generic
        a = FactAnswerer(SECTIONS, None)
        source = max(a.topics.detect("Gas Metal Arc Welding GMAW"), key=a.topics.detect("GMAW").get)
        d = wps_generic.build(a, source, "Draft a WPS for GMAW")
        parameters = [r for r in d.rows if r[0] == "Parameters"]
        self.assertGreaterEqual(len(parameters), 4)
        self.assertTrue(all(r[3] == wps_generic.TO_CONFIRM for r in parameters))
        self.assertTrue(wps_generic.to_docx(d).startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
