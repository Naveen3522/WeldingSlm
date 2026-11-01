"""Only one answering model stays loaded: the active one. The embedding model is kept, because the search needs it."""
import unittest
from unittest import mock

from welding_ai.retrain import ollama_admin


class OneModel(unittest.TestCase):
    def test_every_other_language_model_is_unloaded_and_the_embedding_model_stays(self):
        loaded = ["welding-slm-q8:latest", "welding-slm-v3-20261021-180915:latest", "nomic-embed-text:latest", "bge-m3:latest"]
        sent = []
        with mock.patch.object(ollama_admin, "running", return_value=loaded), mock.patch.object(ollama_admin, "_call", side_effect=lambda path, payload=None, **kw: sent.append(payload) or {}):
            gone = ollama_admin.keep_only("welding-slm-v3-20261021-180915")
        self.assertEqual(gone, ["welding-slm-q8:latest"])
        self.assertEqual(sent, [{"model": "welding-slm-q8:latest", "keep_alive": 0}])

    def test_nothing_is_unloaded_when_only_the_active_one_is_loaded(self):
        with mock.patch.object(ollama_admin, "running", return_value=["welding-slm-q8:latest"]), mock.patch.object(ollama_admin, "_call") as call:
            self.assertEqual(ollama_admin.keep_only("welding-slm-q8"), [])
        call.assert_not_called()

    def test_the_loaded_list_leaves_out_the_embedding_models(self):
        with mock.patch.object(ollama_admin, "running", return_value=["welding-slm-q8:latest", "nomic-embed-text:latest", "mxbai-embed-large:latest"]):
            self.assertEqual(ollama_admin.loaded_models(), ["welding-slm-q8:latest"])

    def test_a_model_that_spills_onto_the_processor_is_noticed(self):
        fits = {"models": [{"name": "welding-slm-v3:latest", "size": 1_520_000_000, "size_vram": 1_520_000_000}, {"name": "nomic-embed-text:latest", "size": 340_000_000, "size_vram": 0}]}
        spills = {"models": [{"name": "welding-slm-v3:latest", "size": 1_520_000_000, "size_vram": 900_000_000}]}
        with mock.patch.object(ollama_admin, "_call", return_value=fits):
            self.assertFalse(ollama_admin.partly_on_processor())                      # the embedding model is not counted
        with mock.patch.object(ollama_admin, "_call", return_value=spills):
            self.assertTrue(ollama_admin.partly_on_processor())


if __name__ == "__main__":
    unittest.main()
