"""The named agents on the Agents page: a question that belongs to another agent gets one line saying so; a fitting one carries on through the ordinary steps."""
import unittest
from types import SimpleNamespace

from welding_ai.agents import topics
from welding_ai.orchestration.steps import AgentTopicStep, Ctx


class Redirect(unittest.TestCase):
    def test_a_standards_question_to_fault_finder_names_procedure_advisor(self):
        self.assertEqual(topics.redirect("fault-finder", ["Standards"]), "Fault Finder can't answer this part. This is a standards question. Use Procedure Advisor.")

    def test_a_safety_question_to_procedure_advisor_names_fault_finder(self):
        self.assertEqual(topics.redirect("procedure-advisor", ["Safety"]), "Procedure Advisor can't answer this part. This is a safety question. Use Fault Finder.")

    def test_parameters_belong_to_the_chat(self):
        self.assertTrue(topics.redirect("fault-finder", ["Parameters"]).endswith("Use Chat."))

    def test_an_own_task_or_no_task_is_answered(self):
        self.assertIsNone(topics.redirect("fault-finder", ["Standards", "Troubleshooting"]))
        self.assertIsNone(topics.redirect("fault-finder", []))
        self.assertIsNone(topics.redirect("", ["Standards"]))


class Step(unittest.TestCase):
    def ctx(self, via, tasks):
        facts = SimpleNamespace(understand=lambda q, history=None: SimpleNamespace(tasks=tasks))
        return Ctx(h=SimpleNamespace(facts=facts), question="q", history=[], model="m", mode="answer", k=5, q="q", via=via)

    def test_a_wrong_agent_ends_the_request_with_one_line(self):
        out = AgentTopicStep().run(self.ctx("fault-finder", ["Standards"]))
        self.assertEqual(out[-1]["route"], "wrong_agent")
        self.assertIn("Use Procedure Advisor.", [e for e in out if e["type"] == "token"][0]["t"])

    def test_a_fitting_question_goes_on_and_the_agent_choice_is_cleared(self):
        ctx = self.ctx("procedure-advisor", ["Procedure"])
        self.assertIsNone(AgentTopicStep().run(ctx))
        self.assertEqual(ctx.via, "")

    def test_other_via_values_are_left_alone(self):
        ctx = self.ctx("formula", ["Standards"])
        self.assertIsNone(AgentTopicStep().run(ctx))
        self.assertEqual(ctx.via, "formula")


if __name__ == "__main__":
    unittest.main()
