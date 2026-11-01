"""The optional separate services (understanding + answering) give the same answers as the in-process pipeline."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from welding_ai.answering.engine import Engine                      # noqa: E402
from welding_ai.orchestration.harness import Config, Harness       # noqa: E402
from welding_ai.retrieval import retrieval                         # noqa: E402
from welding_ai.services import answering as answer_service         # noqa: E402
from welding_ai.services import rpc, understanding as understanding_service   # noqa: E402

QUESTIONS = ["what are the 4 testing layers", "what is WPS what does it contain and what does it lack",
             "servo gun state shows Ready what does it mean", "choosing material for my welding", "hello"]


class Distributed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = retrieval.load()
        cls.s1, p1 = rpc.start_in_thread("understanding", understanding_service.ROUTES)
        cls.s2, p2 = rpc.start_in_thread("answering", answer_service.routes(Engine(cls.index)))
        cls.u1, cls.u2 = f"http://127.0.0.1:{p1}", f"http://127.0.0.1:{p2}"
        base = dict(policy="extractive", use_cache=False)
        cls.local = Harness(index=cls.index, cfg=Config(**base))
        cls.remote = Harness(index=cls.index, cfg=Config(mode="distributed", understanding_url=cls.u1, answer_url=cls.u2, **base))

    @classmethod
    def tearDownClass(cls):
        cls.s1.shutdown()
        cls.s2.shutdown()

    def test_health(self):
        self.assertEqual(rpc.health(self.u1)["service"], "understanding")
        self.assertIn("answer", rpc.health(self.u2)["routes"])

    def test_same_answers_as_local(self):
        for q in QUESTIONS:
            a, b = self.local.run(q, mode="verbatim"), self.remote.run(q, mode="verbatim")
            self.assertEqual((a["route"], a["answer"]), (b["route"], b["answer"]), q)

    def test_unreachable_service_is_reported(self):
        with self.assertRaises(rpc.RpcError):
            rpc.call("http://127.0.0.1:9", "analyze", {"question": "x"}, timeout=1)


if __name__ == "__main__":
    unittest.main()
