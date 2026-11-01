"""The system scan and what it says a computer can run and retrain."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import system_routes  # noqa: E402
from welding_ai.llm.models import params_billions  # noqa: E402
from welding_ai.system import specs as sp  # noqa: E402
from welding_ai.training.queue import QaQueue  # noqa: E402


def machine(vram_gb=8, ram_free=14, disk=200):
    return {"gpu": {"name": "GPU", "total_mb": int(vram_gb * 1024), "free_mb": int(vram_gb * 1024)} if vram_gb else None, "ram_gb": 32, "ram_free_gb": ram_free,
            "threads": 16, "cpu": "CPU", "disk_free_gb": disk}


class Assess(unittest.TestCase):
    def test_the_model_in_use_fits_a_4_gb_card_and_can_be_retrained_there(self):
        a = sp.assess(machine(4), 1.24)
        self.assertEqual((a["verdict"], a["can_retrain"]), ("fits", True))

    def test_a_2b_model_on_a_4_gb_card_runs_but_cannot_be_retrained(self):
        a = sp.assess(machine(4), 2.0)
        self.assertEqual(a["verdict"], "fits")
        self.assertFalse(a["can_retrain"])
        self.assertIn("Cannot be retrained", a["lines"][1]["text"])

    def test_an_8_bit_8b_model_does_not_fit_an_8_gb_card(self):
        self.assertEqual(sp.assess(machine(8, ram_free=24), 8.0)["verdict"], "slow")

    def test_a_model_too_big_for_the_card_runs_slowly_from_memory(self):
        a = sp.assess(machine(4, ram_free=24), 8.0)
        self.assertEqual(a["verdict"], "slow")
        self.assertTrue(a["usable"])

    def test_nothing_fits_when_there_is_no_room_anywhere(self):
        a = sp.assess(machine(4, ram_free=4), 14.0)
        self.assertEqual((a["verdict"], a["usable"]), ("too_large", False))

    def test_no_graphics_card_means_slow_and_no_retraining(self):
        a = sp.assess(machine(0, ram_free=16), 1.24)
        self.assertEqual((a["verdict"], a["can_retrain"]), ("slow", False))

    def test_a_full_disk_blocks_an_otherwise_fitting_model(self):
        a = sp.assess(machine(8, disk=3), 1.24)
        self.assertFalse(a["usable"])
        self.assertEqual(a["lines"][-1]["state"], "bad")

    def test_advice_is_in_words_and_follows_the_card(self):
        self.assertIn("up to about 6 billion", sp.advice(machine(8)))
        self.assertIn("retrains up to about 5 billion", sp.advice(machine(8)))
        self.assertIn("cannot retrain", sp.advice(machine(0, ram_free=16)))
        self.assertIn("cannot run", sp.advice(machine(0, ram_free=1)))


class Scan(unittest.TestCase):
    def test_a_missing_nvidia_tool_means_no_card(self):
        def missing(*a, **k):
            raise FileNotFoundError
        self.assertIsNone(sp.scan(".", run=missing)["gpu"])

    def test_the_card_is_read_from_nvidia_smi(self):
        got = sp.scan(".", run=lambda *a, **k: SimpleNamespace(stdout="NVIDIA GeForce GTX 1650, 4096, 3500\n"))
        self.assertEqual(got["gpu"], {"name": "NVIDIA GeForce GTX 1650", "total_mb": 4096, "free_mb": 3500})
        self.assertTrue(got["ram_gb"] > 0 and got["threads"] >= 1 and got["disk_free_gb"] > 0)

    def test_parameter_sizes_from_ollama(self):
        self.assertEqual((params_billions("1.2B"), params_billions("970M"), params_billions("")), (1.2, 0.97, 0.0))


class Handler:
    def __init__(self, level):
        self.user, self.sent = {"level": level}, None

    def send_json(self, obj, code=200):
        self.sent = (code, obj)


class Routes(unittest.TestCase):
    def call(self, queue, method, path, level=10):
        h = Handler(level)
        system_routes.handle(h, queue, Path("."), method, path, None, scan=lambda folder: machine(8), clock=lambda: 123.0)
        return h.sent

    def test_nothing_is_known_until_the_first_check(self):
        self.assertEqual(self.call(QaQueue(":memory:"), "GET", "/api/admin/system")[1], {"specs": None, "checked": 0, "advice": ""})

    def test_a_scan_is_remembered(self):
        q = QaQueue(":memory:")
        code, got = self.call(q, "POST", "/api/admin/system/scan")
        self.assertEqual((code, got["checked"], got["specs"]["gpu"]["total_mb"]), (200, 123.0, 8192))
        self.assertEqual(self.call(q, "GET", "/api/admin/system")[1]["specs"]["threads"], 16)
        self.assertEqual(system_routes.saved(q)[0]["disk_free_gb"], 200)

    def test_people_below_weld_engineer_are_refused(self):
        self.assertEqual(self.call(QaQueue(":memory:"), "GET", "/api/admin/system", level=1)[0], 403)


if __name__ == "__main__":
    unittest.main()
