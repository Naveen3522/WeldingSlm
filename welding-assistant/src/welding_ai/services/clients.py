"""Adapters that satisfy the `Planner` / `AnswerEngine` ports by calling a separate service (distributed mode)."""
from welding_ai.answering.evidence import Answer
from welding_ai.services import rpc
from welding_ai.understanding import planner as _local_planner


class LocalPlanner:
    """The in-process planner behind the same port."""

    # Delegate straight to the local understanding.planner module.
    @staticmethod
    def analyze(question):
        return _local_planner.analyze(question)


class RemotePlanner:
    def __init__(self, url, timeout=10):
        self.url, self.timeout = url, timeout

    # Call the remote understanding service's /analyze route.
    def analyze(self, question):
        return rpc.call(self.url, "analyze", {"question": question}, self.timeout)


class RemoteEngine:
    def __init__(self, url, timeout=30):
        self.url, self.timeout = url, timeout

    # Call a remote answering-service route and decode its Answer payload.
    def _answer(self, route, plan):
        d = rpc.call(self.url, route, {"plan": plan}, self.timeout)
        return Answer.from_dict(d) if d else None

    def answer(self, plan, tindex=None):
        return self._answer("answer", plan)

    def topic(self, plan):
        return self._answer("topic", plan)

    def clarify(self, plan):
        return rpc.call(self.url, "clarify", {"plan": plan}, self.timeout)

    def related(self, plan, k=3):
        return rpc.call(self.url, "related", {"plan": plan, "k": k}, self.timeout)
