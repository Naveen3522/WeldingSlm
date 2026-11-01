"""Adding documents: noticing new and changed files, accepting uploads safely, and rebuilding the index without touching the live one until it checks out."""
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

import docx

from welding_ai.ingestion import rebuild, uploads
from welding_ai.ingestion.manifest import Manifest, scan


def word(title, body, table=None):
    d = docx.Document()
    d.add_heading(title, 1)
    d.add_paragraph(body)
    if table:
        t = d.add_table(rows=len(table), cols=len(table[0]))
        for i, row in enumerate(table):
            for j, cell in enumerate(row):
                t.cell(i, j).text = cell
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


LONG = ("The weld bead is inspected after cooling. Surface porosity larger than the stated limit is rejected, and the weld is repaired by grinding out the "
        "defect and re-welding with the qualified procedure. A record is kept of every repair so that repeated faults can be traced to their cause.")


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "01_a.docx").write_bytes(b"alpha")
        (self.dir / "02_b.docx").write_bytes(b"beta")
        self.m = Manifest(":memory:", self.dir)

    def test_first_use_assumes_the_index_matches_the_folder(self):
        self.assertTrue(self.m.seed_if_empty())
        d = self.m.detect()
        self.assertFalse(d["pending"])
        self.assertEqual([x["status"] for x in d["documents"]], ["current", "current"])
        self.assertFalse(self.m.seed_if_empty())                         # a second call changes nothing

    def test_new_changed_and_removed_files_are_noticed(self):
        self.m.seed_if_empty()
        (self.dir / "03_c.docx").write_bytes(b"gamma")
        (self.dir / "01_a.docx").write_bytes(b"alpha changed")
        (self.dir / "02_b.docx").unlink()
        d = self.m.detect()
        self.assertEqual((d["new"], d["changed"], d["removed"]), (["03_c.docx"], ["01_a.docx"], ["02_b.docx"]))
        self.assertTrue(d["pending"])
        self.assertEqual({x["name"]: x["status"] for x in d["documents"]}, {"01_a.docx": "changed", "03_c.docx": "new"})

    def test_marking_current_clears_the_pending_state(self):
        self.m.seed_if_empty()
        (self.dir / "03_c.docx").write_bytes(b"gamma")
        self.assertTrue(self.m.detect()["pending"])
        self.m.mark_current()
        self.assertFalse(self.m.detect()["pending"])

    def test_the_same_content_under_a_new_name_is_new_and_a_rewrite_with_the_same_bytes_is_not_a_change(self):
        self.m.seed_if_empty()
        (self.dir / "01_a.docx").write_bytes(b"alpha")
        self.assertFalse(self.m.detect()["pending"])
        (self.dir / "copy.docx").write_bytes(b"alpha")
        self.assertEqual(self.m.detect()["new"], ["copy.docx"])

    def test_word_temporary_files_and_other_types_are_ignored(self):
        (self.dir / "~$01_a.docx").write_bytes(b"lock")
        (self.dir / "notes.txt").write_bytes(b"x")
        self.assertEqual(sorted(scan(self.dir)), ["01_a.docx", "02_b.docx"])


def multipart(parts, boundary="XBOUNDARYX"):
    body = b""
    for field, name, data in parts:
        disposition = f'form-data; name="{field}"' + (f'; filename="{name}"' if name is not None else "")
        body += f"--{boundary}\r\nContent-Disposition: {disposition}\r\n".encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n" if name is not None else b"\r\n"
        body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", body


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.good = word("Repair", LONG)

    def test_the_form_is_read_in_order_and_plain_fields_are_ignored(self):
        ctype, body = multipart([("replace", None, b"1"), ("files", "a.docx", self.good), ("files", "b.docx", b"\x00\x01binary\r\nwith lines")])
        got = uploads.parse_multipart(ctype, body)
        self.assertEqual([n for n, _ in got], ["a.docx", "b.docx"])
        self.assertEqual(got[0][1], self.good)
        self.assertEqual(got[1][1], b"\x00\x01binary\r\nwith lines")

    def test_something_that_is_not_a_form_is_refused(self):
        for ctype in (None, "application/json", "multipart/form-data"):
            with self.assertRaises(uploads.UploadError):
                uploads.parse_multipart(ctype, b"{}")

    def test_a_real_document_is_accepted(self):
        self.assertGreater(uploads.check_document(self.good), 80)

    def test_documents_that_are_not_documents_are_refused_with_a_reason(self):
        fake = io.BytesIO()
        with zipfile.ZipFile(fake, "w") as z:
            z.writestr("hello.txt", "hi")
        cases = {b"": "empty", b"plain text, not a zip": "not a valid", fake.getvalue(): "not a Word document", word("T", "too short"): "almost no text"}
        for data, reason in cases.items():
            with self.assertRaises(uploads.UploadError) as cm:
                uploads.check_document(data)
            self.assertIn(reason, cm.exception.message)

    def test_a_huge_file_is_refused(self):
        with self.assertRaises(uploads.UploadError) as cm:
            uploads.check_document(b"x" * (uploads.MAX_FILE + 1))
        self.assertIn("larger than", cm.exception.message)

    def test_file_names(self):
        self.assertEqual(uploads.clean_name("80_New Chapter (draft).docx"), "80_New Chapter (draft).docx")
        self.assertEqual(uploads.clean_name("C:\\Users\\x\\Desktop\\a.docx"), "a.docx")           # a folder part is dropped, not trusted
        self.assertEqual(uploads.clean_name("../../etc/b.docx"), "b.docx")
        for bad in ("", "a.txt", "a.docx.exe", "~$a.docx", "a;b.docx", "..docx", ".docx", "a" * 200 + ".docx", "con..docx"):
            with self.assertRaises(uploads.UploadError, msg=bad):
                uploads.clean_name(bad)

    def test_saving(self):
        out = uploads.save_all(self.dir, [("80_one.docx", self.good), ("notes.txt", b"x"), ("81_bad.docx", b"junk")])
        self.assertEqual([s["name"] for s in out["saved"]], ["80_one.docx"])
        self.assertEqual([r["name"] for r in out["rejected"]], ["notes.txt", "81_bad.docx"])      # one bad file does not stop the others
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["80_one.docx"])             # and no half-written file is left behind

    def test_an_existing_document_is_never_overwritten_by_accident(self):
        uploads.save_all(self.dir, [("a.docx", self.good)])
        again = uploads.save_all(self.dir, [("a.docx", word("Other", LONG + " More."))])
        self.assertEqual(again["saved"], [])
        self.assertIn("already exists", again["rejected"][0]["reason"])
        self.assertEqual((self.dir / "a.docx").read_bytes(), self.good)
        done = uploads.save_all(self.dir, [("a.docx", word("Other", LONG + " More."))], replace=True)
        self.assertTrue(done["saved"][0]["replaced"])
        self.assertNotEqual((self.dir / "a.docx").read_bytes(), self.good)

    def test_too_many_files(self):
        with self.assertRaises(uploads.UploadError):
            uploads.save_all(self.dir, [(f"{i}.docx", self.good) for i in range(uploads.MAX_FILES + 1)])


class RebuildTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.docs, self.index = self.root / "docs", self.root / "index"
        self.docs.mkdir()
        self.index.mkdir()
        for name, title in (("01_first.docx", "Repair"), ("02_second.docx", "Inspection")):
            (self.docs / name).write_bytes(word(title, LONG, [["Defect", "Action"], ["Porosity", "Grind out and re-weld"]]))

    def read(self, name):
        return json.loads((self.index / name).read_text(encoding="utf-8"))

    def test_a_rebuild_produces_the_index_and_keeps_the_old_one(self):
        (self.index / "chunks.json").write_text('["old"]', encoding="utf-8")
        stages = []
        report = rebuild.rebuild(self.docs, self.index, progress=stages.append, faqs=False)
        self.assertEqual((report["documents"], report["tables"]), (2, 2))
        self.assertEqual({c["source"] for c in self.read("chunks.json")}, {"01_first.docx", "02_second.docx"})
        self.assertTrue(self.read("sections.json"))
        self.assertEqual(json.loads((self.root / "index_prev" / "chunks.json").read_text(encoding="utf-8")), ["old"])
        self.assertEqual(stages[0], "Reading the documents")
        self.assertIn("Switching to the new index", stages)
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith("weld_index_")], [])      # the scratch folder is gone

    def test_a_new_document_appears_in_the_rebuilt_index(self):
        rebuild.rebuild(self.docs, self.index, faqs=False)
        before = {c["source"] for c in self.read("chunks.json")}
        (self.docs / "03_third.docx").write_bytes(word("Storage", LONG.replace("weld bead", "electrode wire")))
        rebuild.rebuild(self.docs, self.index, faqs=False)
        after = {c["source"] for c in self.read("chunks.json")}
        self.assertEqual(after - before, {"03_third.docx"})

    def test_a_document_with_no_searchable_text_leaves_the_live_index_alone(self):
        rebuild.rebuild(self.docs, self.index, faqs=False)
        live = (self.index / "chunks.json").read_bytes()
        d = docx.Document()
        d.add_paragraph("short")
        buf = io.BytesIO()
        d.save(buf)
        (self.docs / "03_empty.docx").write_bytes(buf.getvalue())
        with self.assertRaises(rebuild.RebuildError) as cm:
            rebuild.rebuild(self.docs, self.index, faqs=False)
        self.assertIn("03_empty.docx", cm.exception.message)
        self.assertEqual((self.index / "chunks.json").read_bytes(), live)

    def test_rollback_restores_the_previous_index(self):
        rebuild.rebuild(self.docs, self.index, faqs=False)
        first = (self.index / "chunks.json").read_bytes()
        (self.docs / "03_third.docx").write_bytes(word("Storage", LONG.replace("weld bead", "electrode wire")))
        rebuild.rebuild(self.docs, self.index, faqs=False)
        self.assertNotEqual((self.index / "chunks.json").read_bytes(), first)
        self.assertTrue(rebuild.rollback(self.index))
        self.assertEqual((self.index / "chunks.json").read_bytes(), first)
        self.assertFalse(rebuild.rollback(self.root / "nowhere"))

    def test_cancelling_stops_the_rebuild_and_changes_nothing(self):
        (self.index / "chunks.json").write_text('["old"]', encoding="utf-8")
        stop = threading.Event()
        stop.set()
        with self.assertRaises(rebuild.Cancelled):
            rebuild.rebuild(self.docs, self.index, cancel=stop, faqs=False)
        self.assertEqual((self.index / "chunks.json").read_text(encoding="utf-8"), '["old"]')
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith("weld_index_")], [])


if __name__ == "__main__":
    unittest.main()
