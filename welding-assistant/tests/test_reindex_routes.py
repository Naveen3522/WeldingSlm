"""Reindex: uploads and deletes only save; Reindex is refused until a document is waiting; the state says how many pairs written by hand it would delete."""
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import docx  # noqa: E402

import document_routes  # noqa: E402
from welding_ai.ingestion.manifest import Manifest  # noqa: E402
from welding_ai.training.queue import QaQueue  # noqa: E402


def word(text="A weld is inspected after it cools, and a record is kept of every repair."):
    d = docx.Document()
    d.add_heading("Title", 1)
    d.add_paragraph(text * 8)
    buffer = io.BytesIO()
    d.save(buffer)
    return buffer.getvalue()


def multipart(name, data):
    boundary = "XBOUNDARYX"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", body


class Job:
    def __init__(self):
        self.state, self.started = "idle", 0

    def status(self):
        return {"state": self.state}

    def start(self):
        if self.state == "running":
            return False
        self.state, self.started = "running", self.started + 1
        return True

    def cancel(self):
        pass


class Handler:
    def __init__(self, level=10, content_type="", raw=b""):
        self.user, self.sent = {"level": level}, None
        self.headers, self.raw_body = {"Content-Type": content_type}, raw

    def send_json(self, obj, code=200):
        self.sent = (code, obj)


class Routes(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.docs, self.trash = self.dir / "docs", self.dir / "trash"
        self.docs.mkdir()
        (self.docs / "01_a.docx").write_bytes(word("Alpha text about gaps. "))
        self.manifest = Manifest(":memory:", self.docs)
        self.manifest.seed_if_empty()
        self.job, self.queue, self.requested, self.running = Job(), QaQueue(":memory:"), [], False

    def call(self, method, path, body=None, level=10, content_type="", raw=b""):
        h = Handler(level, content_type, raw)
        document_routes.handle(h, self.docs, self.trash, self.manifest, self.job, method, path, body, lambda: self.requested.append(1) or True,
                               retraining=lambda: self.running, hand_pairs=lambda: len(self.queue.by_kind("manual")))
        return h.sent

    def upload(self, name="02_b.docx"):
        content_type, raw = multipart(name, word("Beta text about forces. "))
        return self.call("POST", "/api/admin/documents/upload", content_type=content_type, raw=raw)

    def test_an_upload_only_saves_it_and_starts_no_indexing(self):
        code, got = self.upload()
        self.assertEqual(code, 200)
        self.assertEqual((got["new"], got["pending"], self.job.started, self.requested), (["02_b.docx"], True, 0, []))

    def test_a_delete_only_moves_the_file_and_starts_no_indexing(self):
        code, got = self.call("POST", "/api/admin/documents/delete", {"names": ["01_a.docx"]})
        self.assertEqual((code, got["removed"], self.job.started, self.requested), (200, ["01_a.docx"], 0, []))

    def test_reindex_is_refused_while_every_document_is_indexed(self):
        code, got = self.call("POST", "/api/admin/documents/refresh")
        self.assertEqual(code, 409)
        self.assertIn("already indexed", got["error"])
        self.assertEqual(self.job.started, 0)

    def test_reindex_starts_once_a_document_is_waiting_and_not_twice(self):
        self.upload()
        self.assertEqual(self.call("POST", "/api/admin/documents/refresh")[0], 202)
        self.assertEqual(self.call("POST", "/api/admin/documents/refresh")[0], 409)
        self.assertEqual(self.job.started, 1)

    def test_a_changed_document_also_turns_reindex_on(self):
        (self.docs / "01_a.docx").write_bytes(word("Alpha text, now corrected. "))
        self.assertTrue(self.call("GET", "/api/admin/documents")[1]["pending"])
        self.assertEqual(self.call("POST", "/api/admin/documents/refresh")[0], 202)

    def test_reindex_is_refused_while_a_retrain_runs(self):
        self.upload()
        self.running = True
        code, got = self.call("POST", "/api/admin/documents/refresh")
        self.assertEqual(code, 409)
        self.assertIn("retrain is running", got["error"])

    def test_the_state_says_how_many_pairs_written_by_hand_would_be_deleted(self):
        self.assertEqual(self.call("GET", "/api/admin/documents")[1]["hand_pairs"], 0)
        self.queue.add_many([{"doc": "Added by hand", "section": "S", "question": "How wide is the gap?", "answer": "0.8 mm", "evidence": "0.8 mm", "kind": "manual"}])
        self.assertEqual(self.call("GET", "/api/admin/documents")[1]["hand_pairs"], 1)

    def test_people_who_may_not_manage_the_library_are_refused(self):
        for method, path in (("GET", "/api/admin/documents"), ("POST", "/api/admin/documents/refresh")):
            self.assertEqual(self.call(method, path, level=1)[0], 403)

    def test_a_reindex_deletes_the_pairs_written_by_hand_and_keeps_the_others(self):
        self.queue.add_many([{"doc": "Added by hand", "section": "S", "question": "How wide is the gap?", "answer": "0.8 mm", "evidence": "0.8 mm", "kind": "manual"},
                             {"doc": "A.docx", "section": "S", "question": "Force?", "answer": "2 kN", "evidence": "2 kN", "kind": "table"}])
        self.assertEqual(self.queue.delete_kind("manual"), 1)
        self.assertEqual([p["question"] for p in self.queue.page()["rows"]], ["Force?"])


if __name__ == "__main__":
    unittest.main()
