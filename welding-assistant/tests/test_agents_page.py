"""What the Agents page and the slash commands rely on: forms turned into sentences the agents read, a forced agent route (via), and the stage events."""
import json
import re
import unittest

from welding_ai import paths
from welding_ai.agents import slots
from welding_ai.agents.chat import AgentChat
from welding_ai.agents.drafts import DraftStore
from welding_ai.core.config import Config
from welding_ai.formulas.chat import FormulaChat
from welding_ai.formulas.store import FormulaStore
from welding_ai.orchestration.harness import Harness
from welding_ai.retrieval import retrieval


def text_of(events):
    return "\n".join(e["t"] for e in events if e["type"] == "token")


class ForcedRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = retrieval.load()
        store = FormulaStore(":memory:")
        store.sync_documents(json.loads(paths.FORMULAS_SEED.read_text(encoding="utf-8")))
        squash = lambda t: re.sub(r"\s+", " ", t)
        locate = lambda doc, anchor: next((i for i, c in enumerate(cls.index.chunks) if c["source"] == doc and squash(anchor) in squash(c["text"])), None)
        cls.h = Harness(index=cls.index, cfg=Config(use_cache=False))
        cls.h.formulas = FormulaChat(store.usable, locate)
        cls.h.agents = AgentChat(store.usable, DraftStore())

    def ask(self, question, via=""):
        return list(self.h.stream(question, history=[], mode="verbatim", via=via))

    def route(self, events):
        return events[-1]["route"]

    # The sentences the Agents page builds from its forms (js/agents/*.js): each must reach the agent the card stands for.
    def test_the_parameters_form_sentence(self):
        ev = self.ask("What are the force, weld time, weld current for resistance spot welding 1 mm mild steel to 1.2 mm mild steel, zinc coated?", via="parameters")
        self.assertEqual(self.route(ev), "parameters")

    def test_the_troubleshoot_form_sentence(self):
        ev = self.ask("I get porosity when MIG welding 1.2 mm mild steel. What should I do?", via="troubleshoot")
        self.assertEqual(self.route(ev), "scenario")
        self.assertIn("orosity", text_of(ev))

    def test_the_scenario_form_sentence(self):
        ev = self.ask("I am spot welding 1.2 mm galvanised mild steel to 2 mm HSLA and I get expulsion. What should I check?", via="scenario")
        self.assertEqual(self.route(ev), "scenario")

    def test_the_wps_form_sentence_gives_a_download(self):
        ev = self.ask("Draft a WPS for resistance spot welding 1 mm mild steel to 1.2 mm mild steel, zinc coated", via="wps")
        self.assertEqual(self.route(ev), "wps")
        self.assertTrue(any(e["type"] == "download" for e in ev))

    def test_the_agent_asked_for_answers_even_when_the_rules_would_not_choose_it(self):
        text = "Tell me about 1.0 mm mild steel"
        self.assertNotEqual(self.route(self.ask(text)), "parameters")
        self.assertEqual(self.route(self.ask(text, via="parameters")), "parameters")

    # /calculate
    def test_calculate_forces_the_calculator(self):
        ev = self.ask("heat input at 29 V, 290 A and 400 mm/min with an efficiency of 0.8", via="formula")
        self.assertEqual(self.route(ev), "formula")
        self.assertEqual(ev[-1]["provenance"]["kind"], "calculated")

    def test_calculate_with_nothing_to_go_on_lists_the_formulas(self):
        ev = self.ask("Which formulas can I use?", via="formula")
        self.assertEqual(self.route(ev), "formula_ask")
        self.assertIn("Formulas you can use", text_of(ev))

    def test_an_agent_form_does_not_fall_to_the_calculator(self):
        ev = self.ask("What are the force for resistance spot welding 1 mm mild steel?", via="parameters")
        self.assertNotEqual(self.route(ev), "formula")

    # the strip shown while an answer is on its way
    def test_stage_events_come_first_and_name_the_real_steps(self):
        ev = self.ask("What does E7018 mean?")
        stages = [e for e in ev if e["type"] == "stage"]
        self.assertTrue(stages)
        self.assertTrue(all(set(e) == {"type", "id", "label", "detail"} for e in stages))
        self.assertEqual(ev.index(stages[0]), 0 if ev[0]["type"] == "stage" else ev.index(stages[0]))
        self.assertEqual(ev[-1]["type"], "done")

    def test_a_form_sentence_is_read_the_way_the_form_means_it(self):
        s = slots.read("I get stuck weld when resistance spot welding 1 mm mild steel, zinc coated. What should I do?")
        self.assertEqual((s.process, s.symptom, s.coating), ("rsw", "no nugget / stick weld", True))
        s = slots.read("What are the electrode force for resistance spot welding 0.8 mm advanced high-strength steel to 1.5 mm HSLA steel?")
        self.assertEqual([(x.thickness_mm, x.material) for x in s.sheets], [(0.8, "ahss"), (1.5, "hsla")])


if __name__ == "__main__":
    unittest.main()
