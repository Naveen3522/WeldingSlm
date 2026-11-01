"""The agents inside the chat: who answers what, the answers they give, the WPS draft and its download."""
import io
import json
import unittest
from unittest import mock

from welding_ai import paths
from welding_ai.agents import slots, wps
from welding_ai.agents.chat import AgentChat, decide
from welding_ai.agents.drafts import MAX_KEPT, TTL_SECONDS, DraftStore
from welding_ai.agents.knowledge import Knowledge
from welding_ai.core.config import Config
from welding_ai.formulas.store import FormulaStore
from welding_ai.orchestration.harness import Harness
from welding_ai.retrieval import retrieval


def route(text):
    return decide(text, slots.read(text))


class Deciding(unittest.TestCase):
    def test_who_answers(self):
        cases = {
            "I am spot welding 1.2 mm DP600 galvannealed to 1.5 mm HSLA and I get expulsion. What should I do?": "scenario",
            "I get porosity when MIG welding 3 mm mild steel. What can I check?": "scenario",
            "Which welding process should I use for 2 mm aluminium sheet in a car body?": "scenario",
            "What is the electrode force for 1.0 mm mild steel?": "parameters",
            "weld current and time for 1.4 mm sheet, spot welding": "parameters",
            "what is the electrode force for mild steel": "parameters",
            "Draft a WPS for spot welding 1.2 mm mild steel": "wps",
            "please generate a welding procedure specification for RSW": "wps",
        }
        for text, expected in cases.items():
            self.assertEqual(route(text), expected, text)

    def test_questions_the_pipeline_already_answers_stay_with_it(self):
        for text in ("Why does expulsion happen in spot welding?", "What causes porosity in GMAW and how is it corrected?", "What is porosity?",
                     "Cracks appear hours after welding a high strength steel", "What does E7018 mean?", "How is preheat decided for carbon steel?",
                     "What is a WPS?", "What is heat input?", "What are the metal transfer modes in GMAW?",
                     "What is a weld nugget?", "What is the nugget diameter?", "What is electrode force?", "How long is the weld time in resistance welding?",
                     "What does the squeeze time do?", "What is the weld current in spot welding?", "Explain electrode face diameter"):
            self.assertIsNone(route(text), text)

    def test_an_arc_welding_question_is_not_taken_for_spot_welding(self):
        self.assertNotEqual(route("what electrode diameter for 2 mm sheet with TIG"), "parameters")
        self.assertNotEqual(route("what current for 3 mm mild steel with MIG"), "parameters")

    def test_a_current_with_no_spot_welding_context_is_not_assumed_to_be_spot_welding(self):
        self.assertIsNone(route("what current for 3 mm mild steel"))


def make_harness(index):
    store = FormulaStore(":memory:")
    store.sync_documents(json.loads(paths.FORMULAS_SEED.read_text(encoding="utf-8")))
    h = Harness(index=index, cfg=Config(use_cache=False))
    h.drafts = DraftStore()
    h.agents = AgentChat(store.usable, h.drafts)
    return h


def text_of(events):
    return "\n".join(e["t"] for e in events if e["type"] == "token")


def rows_of(events):
    return [r for e in events if e["type"] == "table" for r in e["rows"]]


class InTheChat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = retrieval.load()
        cls.h = make_harness(cls.index)

    def ask(self, question, history=None):
        return list(self.h.stream(question, history=history or [], mode="verbatim"))

    def test_a_described_situation_gets_the_structured_answer(self):
        ev = self.ask("I am spot welding 1.2 mm DP600 galvannealed to 1.5 mm HSLA and I get expulsion. What should I do?")
        done = ev[-1]
        self.assertEqual(done["route"], "scenario")
        self.assertEqual(done["provenance"]["kind"], "assembled")
        text = text_of(ev)
        self.assertIn("How I read it", text)
        self.assertIn("DP600 read as advanced high-strength steel", text)
        self.assertIn("my assumption", text)
        self.assertIn("Reduce current or time; increase force", text)                  # the defect table's corrective action, verbatim
        self.assertIn("must never be transferred without requalification", text)      # the guide's own caveat for AHSS
        self.assertIn(["Electrode force", "638 lbf (2.84 kN)", "force_lbf = 6000 × 0.1063 (converted at 1 lbf = 4.448 N)"], rows_of(ev))
        sources = next(e for e in ev if e["type"] == "sources")
        self.assertGreaterEqual(len(sources["sources"]), 3)
        cite = next(e for e in ev if e["type"] == "cite")
        self.assertEqual([c["chunk"] for c in cite["items"]], [s["chunk_id"] for s in sources["sources"]])

    def test_every_citation_number_in_the_text_points_at_a_listed_source(self):
        import re
        ev = self.ask("I am spot welding 1.2 mm DP600 galvannealed to 1.5 mm HSLA and I get expulsion. What should I do?")
        used = {int(n) for n in re.findall(r"\[(\d+)\]", text_of(ev))}
        listed = len(next(e for e in ev if e["type"] == "sources")["sources"])
        self.assertTrue(used)
        self.assertLessEqual(max(used), listed)

    def test_the_old_ai_failure_is_now_a_correct_unit_converted_answer(self):
        ev = self.ask("What is the electrode force for 1.0 mm mild steel?")
        self.assertEqual(ev[-1]["route"], "parameters")
        self.assertEqual(ev[-1]["provenance"]["kind"], "calculated")
        # one stated thickness is read as two equal sheets (the guide's own basis): 2.0 mm = 0.0787 in -> 472 lbf, the document's row for this band
        self.assertEqual(rows_of(ev)[0][0], "Electrode force")
        self.assertEqual(rows_of(ev)[0][1], "472 lbf (2.10 kN)")
        self.assertIn("0.0787 in", text_of(ev))
        self.assertIn("two equal sheets", text_of(ev))
        self.assertNotIn("6000 ×", text_of(ev).replace("force_lbf", ""))                 # the bare, unit-less rule the AI printed is gone

    def test_missing_thickness_is_asked_for_not_guessed(self):
        ev = self.ask("what is the electrode force for mild steel")
        self.assertEqual(ev[-1]["route"], "parameters")
        self.assertIn("thickness", text_of(ev))
        self.assertEqual(rows_of(ev), [])

    def test_a_follow_up_with_details_continues_the_situation(self):
        history = [{"role": "user", "content": "I get porosity when MIG welding mild steel. What can I check?"}, {"role": "assistant", "content": "..."}]
        ev = self.ask("the plate is 3 mm", history)
        self.assertEqual(ev[-1]["route"], "scenario")
        self.assertIn("3 mm", text_of(ev))

    def test_a_new_question_after_a_situation_is_not_read_as_a_continuation(self):
        history = [{"role": "user", "content": "I get porosity when MIG welding 3 mm mild steel. What can I check?"}, {"role": "assistant", "content": "..."}]
        self.assertNotEqual(self.ask("What is a weld nugget?", history)[-1]["route"], "scenario")
        self.assertNotEqual(self.ask("the plate is 3 mm. Why does expulsion happen?", history)[-1]["route"], "scenario")      # it asks something, so it stands alone

    def test_a_verified_faq_is_preferred_to_an_assembled_answer(self):
        with mock.patch.object(self.h.faq, "accept", return_value=True), mock.patch.object(self.h.faq, "match", return_value={"idx": 0, "sim": 1.0}):
            ev = self.ask("I get porosity when MIG welding 3 mm mild steel. What can I check?")
        self.assertEqual(ev[-1]["route"], "faq")

    def test_summary_on_says_the_ai_was_not_needed(self):
        done = list(self.h.stream("What is the electrode force for 1.0 mm mild steel?", mode="answer"))[-1]
        self.assertIn("needed no AI", done["provenance"]["note"])

    def test_without_agents_the_pipeline_is_unchanged(self):
        plain = Harness(index=self.index, cfg=Config(use_cache=False))
        done = list(plain.stream("What is the electrode force for 1.0 mm mild steel?", mode="verbatim"))[-1]
        self.assertNotIn(done["route"], ("parameters", "scenario", "wps"))


class WpsDraft(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = retrieval.load()
        cls.h = make_harness(cls.index)
        with open(paths.INDEX_DIR / "tables.json", encoding="utf-8") as fh:
            cls.kn = Knowledge(json.load(fh), cls.index)
        store = FormulaStore(":memory:")
        store.sync_documents(json.loads(paths.FORMULAS_SEED.read_text(encoding="utf-8")))
        cls.formulas = {f["id"]: f for f in store.usable()}

    def build(self, text):
        return wps.build(slots.read(text), self.kn, self.formulas)

    def test_the_rows_are_the_documents_eleven_data_groups(self):
        d = self.build("draft a WPS for spot welding 2 x 1.0 mm mild steel")
        groups = list(dict.fromkeys(r[0] for r in d.rows))
        self.assertEqual(groups, ["Identification", "Process", "Parent materials", "Joint", "Electrodes", "Machine", "Parameters", "Utilities", "Monitoring", "Inspection", "Traceability"])
        self.assertEqual(d.status.split(":")[0], "DRAFT")

    def test_what_is_known_is_filled_and_the_rest_is_left_for_the_engineer(self):
        d = self.build("draft a WPS for spot welding 2 x 1.0 mm mild steel")
        by_field = {r[1].lower(): r for r in d.rows}
        self.assertEqual(by_field["thicknesses"][2], "1 mm and 1 mm")
        self.assertIn("ISO 4063 reference number 21", next(r[2] for r in d.rows if r[0] == "Process"))
        self.assertIn("not less than 16.0 mm", next(r[2] for r in d.rows if "pitch" in r[1].lower()))
        params = next(r for r in d.rows if r[0] == "Parameters")
        self.assertIn("STARTING ESTIMATES", params[2])
        self.assertIn("not qualified values", params[2])
        self.assertEqual([r[2] for r in d.rows if r[0] == "Machine"], [wps.TO_CONFIRM] * 4)
        self.assertTrue(all(r[2] == wps.TO_CONFIRM for r in d.rows if not r[4]))

    def test_no_thickness_means_the_material_and_parameter_fields_are_left_alone(self):
        d = self.build("draft a WPS for spot welding")
        self.assertTrue(any("No sheet thickness" in n for n in d.notes))
        self.assertEqual(next(r for r in d.rows if r[0] == "Parameters")[2], wps.TO_CONFIRM)

    def test_a_stated_grade_is_kept_beside_the_family(self):
        d = self.build("draft a WPS for spot welding 1.2 mm DP600 galvannealed")
        grades = next(r[2] for r in d.rows if r[1].lower() == "grades")
        self.assertIn("AHSS / UHSS", grades)
        self.assertIn("DP600", grades)
        self.assertEqual(next(r[2] for r in d.rows if r[1].lower() == "coatings"), "galvannealed")
        self.assertTrue(any("own procedure qualification" in n for n in d.notes))

    def test_other_processes_are_refused(self):
        d = self.build("draft a WPS for MIG welding 3 mm mild steel")
        self.assertIn("resistance spot welding", d.refused)
        self.assertEqual(d.rows, [])

    def test_the_word_document_says_draft_and_marks_the_blanks(self):
        import docx
        d = self.build("draft a WPS for spot welding 2 x 1.0 mm mild steel")
        doc = docx.Document(io.BytesIO(wps.to_docx(d, who="ann")))
        text = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("DRAFT. Not qualified.", text)
        self.assertIn("ISO 15614-12", text)
        self.assertIn("for ann", text)
        table = doc.tables[0]
        self.assertEqual(len(table.rows), len(d.rows) + 1)
        values = [r.cells[2].text for r in table.rows[1:]]
        self.assertEqual(values.count(wps.TO_CONFIRM), len(d.rows) - d.filled)

    def test_in_the_chat_the_draft_comes_with_a_download_that_works(self):
        ev = list(self.h.stream("Draft a WPS for spot welding 1.2 mm DP600", mode="verbatim"))
        self.assertEqual(ev[-1]["route"], "wps")
        self.assertEqual(ev[-1]["provenance"]["kind"], "assembled")
        link = next(e for e in ev if e["type"] == "download")
        self.assertTrue(link["url"].startswith("/api/agents/wps/"))
        name, data = self.h.drafts.get(link["url"].rsplit("/", 1)[1])
        self.assertTrue(name.endswith(".docx"))
        self.assertTrue(data.startswith(b"PK"))                                       # a zip, which is what a .docx is
        self.assertIn("not a qualified procedure", text_of(ev))

    def test_an_arc_wps_request_is_declined_in_the_chat(self):
        ev = list(self.h.stream("Draft a WPS for MIG welding 3 mm mild steel", mode="verbatim"))
        self.assertEqual(ev[-1]["route"], "wps_ask")
        self.assertFalse([e for e in ev if e["type"] == "download"])
        self.assertIsNone(ev[-1]["provenance"])


class Drafts(unittest.TestCase):
    def test_save_and_get(self):
        s = DraftStore()
        token = s.save(b"data", "a.docx")
        self.assertEqual(s.get(token), ("a.docx", b"data"))
        self.assertIsNone(s.get("nope"))
        self.assertNotEqual(token, s.save(b"x", "b.docx"))

    def test_expiry(self):
        t = [1000.0]
        s = DraftStore(clock=lambda: t[0])
        token = s.save(b"data", "a.docx")
        t[0] += TTL_SECONDS - 1
        self.assertIsNotNone(s.get(token))
        t[0] += 2
        self.assertIsNone(s.get(token))

    def test_the_store_does_not_grow_without_limit(self):
        t = [0.0]
        s = DraftStore(clock=lambda: t[0])
        tokens = []
        for _ in range(MAX_KEPT + 5):
            t[0] += 1
            tokens.append(s.save(b"x", "a.docx"))
        self.assertIsNone(s.get(tokens[0]))
        self.assertIsNotNone(s.get(tokens[-1]))
        self.assertEqual(len(s._items), MAX_KEPT)


if __name__ == "__main__":
    unittest.main()
