"""Model files (.weldmodel): what they hold, what is refused, how a computer checks one before adding it, and how a model is added from one."""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import package_routes  # noqa: E402
from welding_ai.llm import package  # noqa: E402
from welding_ai.llm.install import Installer  # noqa: E402
from welding_ai.llm.registry import ModelRegistry  # noqa: E402
from welding_ai.training.queue import QaQueue  # noqa: E402


def machine(vram_gb=4, ram_free=11, disk=600):
    return {"gpu": {"name": "GPU", "total_mb": int(vram_gb * 1024), "free_mb": 3000} if vram_gb else None, "ram_gb": 24, "ram_free_gb": ram_free, "threads": 12, "cpu": "CPU", "disk_free_gb": disk}


def pair(doc="A.docx", q="Gap?", a="0.8 mm", kind="table"):
    return {"doc": doc, "section": "S", "question": q, "answer": a, "evidence": a, "kind": kind}


def make_adapter(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "adapter_config.json").write_text("{}")
    (folder / "adapter_model.safetensors").write_bytes(b"x" * 2000)
    (folder / "tokenizer.json").write_text("{}")
    return folder


class Format(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.adapter = make_adapter(Path(self.dir.name) / "adapter")

    def tearDown(self):
        self.dir.cleanup()

    def test_a_model_file_round_trips_with_its_adapter_pairs_and_scores(self):
        data = package.make_model(self.adapter, "Weld v3", notes="n", gate={"passed": True, "pairs": 5}, specs=machine(), pairs=[pair()])
        pkg = package.read(data)
        self.assertEqual((pkg.kind, pkg.manifest["name"], pkg.manifest["pairs"], pkg.manifest["gate"]["passed"]), ("model", "Weld v3", 1, True))
        out = pkg.extract(Path(self.dir.name) / "out")
        self.assertEqual({p.name for p in out.iterdir()}, {"adapter_config.json", "adapter_model.safetensors", "tokenizer.json"})
        self.assertEqual(pkg.pairs()[0]["question"], "Gap?")

    def test_a_kit_has_pairs_and_no_adapter(self):
        pkg = package.read(package.make_kit("Kit", [pair(), pair(q="Force?", a="2 kN")]))
        self.assertEqual((pkg.kind, len(pkg.pairs())), ("kit", 2))
        self.assertFalse(any(n.startswith("adapter/") for n in pkg.names))

    def test_a_model_without_its_adapter_cannot_be_made(self):
        (self.adapter / "adapter_model.safetensors").unlink()
        with self.assertRaises(package.PackageError):
            package.make_model(self.adapter, "x")

    def test_things_that_are_not_model_files_are_refused(self):
        for data in (b"not a zip", package._zip([("readme.txt", "hi")]), package._zip([("manifest.json", "{bad")]),
                     package._zip([("manifest.json", json.dumps({"format": 99, "kind": "model", "base": {"id": "b"}, "name": "n"}))])):
            with self.assertRaises(package.PackageError):
                package.read(data)

    def test_unsafe_file_names_are_refused(self):
        good = json.loads(zipfile.ZipFile(io.BytesIO(package.make_kit("k", [pair()]))).read("manifest.json"))
        for bad in ("../evil.txt", "/abs.txt", "adapter/../../x", "C:/x.txt"):
            data = package._zip([("manifest.json", json.dumps(good)), ("pairs.json", "[]"), (bad, "x")])
            with self.assertRaises(package.PackageError):
                package.read(data)

    def test_a_model_manifest_with_no_adapter_files_is_refused(self):
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(package.make_model(self.adapter, "n"))).read("manifest.json"))
        with self.assertRaises(package.PackageError):
            package.read(package._zip([("manifest.json", json.dumps(manifest))]))

    def test_extract_writes_only_under_the_adapter_folder(self):
        data = package.make_model(self.adapter, "n")
        z = zipfile.ZipFile(io.BytesIO(data))
        extra = package._zip([(i.filename, z.read(i)) for i in z.infolist()] + [("pairs.json", "[]"), ("other/file.txt", "x")])
        out = package.read(extra).extract(Path(self.dir.name) / "o")
        self.assertFalse((out / "file.txt").exists() or (out / "pairs.json").exists())


class Checking(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.adapter = make_adapter(Path(self.dir.name) / "adapter")

    def tearDown(self):
        self.dir.cleanup()

    def model(self, base=None, params=None):
        pkg = package.read(package.make_model(self.adapter, "Weld v3"))
        if base:
            pkg.manifest["base"] = {"id": base, "name": "Other 8B", "params_b": params or 8.0}
        return pkg

    def test_the_same_base_on_a_computer_with_room_can_be_added(self):
        got = package_routes.check(self.model(), machine(), set())
        self.assertTrue(got["can_add"])
        self.assertEqual(got["fit"]["verdict"], "fits")

    def test_a_different_base_cannot_be_added_and_the_reason_is_given(self):
        got = package_routes.check(self.model("some/other-8b"), machine(), set())
        self.assertFalse(got["can_add"])
        self.assertIn("training kit", got["lines"][0]["text"])

    def test_a_computer_without_room_is_refused(self):
        self.assertFalse(package_routes.check(self.model(), machine(disk=2), set())["can_add"])

    def test_a_kit_says_which_documents_are_missing(self):
        pkg = package.read(package.make_kit("k", [pair("A.docx"), pair("B.docx", q="Q2?", a="2")]))
        got = package_routes.check(pkg, machine(), {"A.docx"})
        self.assertTrue(got["can_add"])
        self.assertTrue(any("not in this library" in l["text"] for l in got["lines"]))


class Handler:
    def __init__(self, level=10, raw=b""):
        self.user, self.sent, self.raw_body, self.data = {"level": level, "username": "asha"}, None, raw, None

    def send_json(self, obj, code=200):
        self.sent = (code, obj)

    def send_bytes(self, code, data, kind, headers=None):
        self.sent, self.data = (code, kind, headers), data


class FakeProcess:
    def __init__(self, lines, code=0):
        self.stdout, self.code = iter(lines), code

    def wait(self):
        return self.code


class Routes(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.registry, self.queue = ModelRegistry(":memory:"), QaQueue(":memory:")
        self.adapter = make_adapter(self.dir / "runs" / "v9" / "adapter")
        self.registry.record_training("welding-slm-v9", "v9", {"passed": True, "pairs": 7, "documents": 2})
        self.queue.add_many([pair(), pair(q="Hand?", a="yes", kind="manual")])
        self.queue.decide([p["id"] for p in self.queue.by_kind("manual")], "approved", "t")
        self.lines = []
        self.installer = Installer(self.dir / "runs", self.dir, self.registry, popen=lambda *a, **k: FakeProcess(self.lines), remove=lambda name: True)
        self.imports = package_routes.Imports(self.dir / "imports")
        self.running = False
        package_routes.paths.MODEL_DIR, self.saved_dir = self.dir, package_routes.paths.MODEL_DIR

    def tearDown(self):
        package_routes.paths.MODEL_DIR = self.saved_dir

    def call(self, method, path, body=None, raw=b"", level=10, specs=None):
        h = Handler(level, raw)
        package_routes.handle(h, self.registry, self.queue, self.installer, self.imports, lambda: ["A.docx"], lambda: self.running, method, path, body,
                              scan=lambda folder: specs or machine(), folder=self.dir)
        return h

    def test_export_a_model_carries_its_adapter_scores_and_pairs_without_the_hand_written_ones(self):
        h = self.call("POST", "/api/admin/models/welding-slm-v9/export", {"kind": "model", "pairs": True})
        pkg = package.read(h.data)
        self.assertEqual((pkg.kind, pkg.manifest["gate"]["pairs"], [p["question"] for p in pkg.pairs()]), ("model", 7, ["Gap?"]))
        self.assertIn(".weldmodel", h.sent[2]["Content-Disposition"])

    def test_the_hand_written_pairs_travel_only_when_asked(self):
        h = self.call("POST", "/api/admin/models/welding-slm-v9/export", {"kind": "kit", "hand": True})
        self.assertEqual(sorted(p["question"] for p in package.read(h.data).pairs()), ["Gap?", "Hand?"])

    def test_a_model_with_no_adapter_cannot_be_exported_but_a_kit_can(self):
        self.assertEqual(self.call("POST", "/api/admin/models/welding-slm-q8/export", {"kind": "model"}).sent[0], 400)
        self.assertEqual(self.call("POST", "/api/admin/models/welding-slm-q8/export", {"kind": "kit"}).sent[0], 200)

    def test_only_admins_may_export_check_or_add(self):
        for method, path in (("POST", "/api/admin/models/welding-slm-v9/export"), ("POST", "/api/admin/models/package/check"), ("POST", "/api/admin/models/package/add")):
            self.assertEqual(self.call(method, path, {}, level=5).sent[0], 403)

    def test_checking_a_file_says_what_it_is_and_keeps_it_for_adding(self):
        data = self.call("POST", "/api/admin/models/welding-slm-v9/export", {"kind": "model"}).data
        got = self.call("POST", "/api/admin/models/package/check", raw=data).sent[1]
        self.assertTrue(got["can_add"] and got["token"])
        self.assertEqual(self.imports.get(got["token"]), data)

    def test_a_bad_file_is_refused_with_a_message(self):
        code, got = self.call("POST", "/api/admin/models/package/check", raw=b"junk").sent
        self.assertEqual(code, 400)
        self.assertIn("not a Weld Assist model file", got["error"])

    def test_adding_runs_the_check_again_and_refuses_a_file_that_does_not_fit(self):
        data = self.call("POST", "/api/admin/models/welding-slm-v9/export", {"kind": "model"}).data
        token = self.call("POST", "/api/admin/models/package/check", raw=data).sent[1]["token"]
        code, got = self.call("POST", "/api/admin/models/package/add", {"token": token}, specs=machine(disk=1)).sent
        self.assertEqual(code, 409)
        self.assertEqual(self.installer.status()["state"], "idle")

    def test_adding_a_model_installs_it_unused_with_the_name_from_the_file(self):
        self.lines[:] = ['STAGE {"name": "merging"}\n', 'STAGE {"name": "registering"}\n', 'DONE {"model": "x"}\n']
        data = self.call("POST", "/api/admin/models/welding-slm-v9/export", {"kind": "model"}).data
        token = self.call("POST", "/api/admin/models/package/check", raw=data).sent[1]["token"]
        code, _ = self.call("POST", "/api/admin/models/package/add", {"token": token}).sent
        self.assertEqual(code, 202)
        self.installer.thread.join(5)
        state = self.installer.status()
        self.assertEqual(state["state"], "done")
        entry = self.registry.entry(state["model_id"])
        self.assertTrue(entry["dataset"].startswith("import-"))
        self.assertTrue((self.dir / "runs" / entry["dataset"] / "adapter" / "adapter_model.safetensors").is_file())
        self.assertIsNone(self.registry.active())
        self.assertEqual(json.loads(entry["gate"])["imported"], True)

    def test_a_failed_export_adds_nothing_and_cleans_up(self):
        self.lines[:] = ['STAGE {"name": "merging"}\n', 'FAILED {"error": "out of memory"}\n']
        data = self.call("POST", "/api/admin/models/welding-slm-v9/export", {"kind": "model"}).data
        token = self.call("POST", "/api/admin/models/package/check", raw=data).sent[1]["token"]
        self.call("POST", "/api/admin/models/package/add", {"token": token})
        self.installer.thread.join(5)
        state = self.installer.status()
        self.assertEqual(state["state"], "failed")
        self.assertIn("out of memory", state["message"])
        self.assertEqual(self.registry.entry(state["model_id"])["dataset"], "")
        self.assertEqual([p.name for p in (self.dir / "runs").iterdir()], ["v9"])

    def test_a_kit_adds_its_pairs_and_skips_the_ones_already_there(self):
        data = self.call("POST", "/api/admin/models/welding-slm-v9/export", {"kind": "kit"}).data
        token = self.call("POST", "/api/admin/models/package/check", raw=data).sent[1]["token"]
        self.assertEqual(self.call("POST", "/api/admin/models/package/add", {"token": token}).sent[1], {"kind": "kit", "added": 0, "skipped": 1})

    def test_no_model_is_added_while_a_retrain_runs(self):
        data = self.call("POST", "/api/admin/models/welding-slm-v9/export", {"kind": "model"}).data
        token = self.call("POST", "/api/admin/models/package/check", raw=data).sent[1]["token"]
        self.running = True
        self.assertEqual(self.call("POST", "/api/admin/models/package/add", {"token": token}).sent[0], 409)

    def test_a_made_up_token_finds_nothing(self):
        self.assertEqual(self.call("POST", "/api/admin/models/package/add", {"token": "../../etc/passwd"}).sent[0], 404)


if __name__ == "__main__":
    unittest.main()
