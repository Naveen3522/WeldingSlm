"""Templates and drafts: Word tables in and out (with the checks on an uploaded file), the template store, and the draft rules (created on first save, fixed cells,
who may change the shape, progress, paging, search)."""
import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docx import Document

from welding_ai.forms.docx_table import MAX_BYTES, DocxProblem, OLE_MAGIC, read_table, write_docx
from welding_ai.forms.store import FormError, FormStore, check_grid, progress

HEADS = ["Field", "Value", "Remarks"]
ROWS = [["Process", "", ""], ["Material", "", ""], ["Joint design", "", ""], ["Standard", "ISO 15614", "fixed text"]]


def make_docx(heads, rows, extra_paragraph=True):
    d = Document()
    if extra_paragraph:
        d.add_paragraph("A heading line before the table")
    t = d.add_table(rows=1, cols=len(heads))
    for i, h in enumerate(heads):
        t.rows[0].cells[i].text = h
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = v
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


class WordTables(unittest.TestCase):
    def test_a_table_round_trips_and_empty_cells_stay_empty(self):
        back = read_table(write_docx("Spot welding WPS", HEADS, ROWS, note="Draft only."))
        self.assertEqual((back["heads"], back["rows"]), (HEADS, ROWS))
        self.assertIn("4 fields", back["report"]["found"])
        self.assertEqual(back["report"]["warnings"], [])

    def test_the_table_is_found_after_other_text_and_the_biggest_one_is_used(self):
        d = Document()
        d.add_paragraph("intro")
        small = d.add_table(rows=2, cols=2)
        small.rows[0].cells[0].text = "x"
        big = d.add_table(rows=1, cols=3)
        for i, h in enumerate(HEADS):
            big.rows[0].cells[i].text = h
        for r in ROWS:
            cells = big.add_row().cells
            for i, v in enumerate(r):
                cells[i].text = v
        out = io.BytesIO()
        d.save(out)
        self.assertEqual(read_table(out.getvalue())["heads"], HEADS)

    def test_rows_with_no_field_name_are_skipped_with_a_warning(self):
        data = make_docx(HEADS, ROWS + [["", "stray", ""], ["", "", ""]])
        got = read_table(data)
        self.assertEqual(len(got["rows"]), 4)
        self.assertEqual(got["report"]["warnings"], ["2 rows have no field name. They will be skipped."])

    def test_files_that_cannot_be_used_are_refused_with_the_agreed_messages(self):
        cases = [(b"x" * (MAX_BYTES + 1), "too large. The limit is 5 MB."), (OLE_MAGIC + b"\x00" * 100, "password protected"),
                 (b"just text renamed", "not a valid Word file"), (b"PK\x03\x04garbage", "not a valid Word file")]
        for data, expect in cases:
            with self.assertRaises(DocxProblem) as e:
                read_table(data, "report.docx")
            self.assertIn(expect, e.exception.message)
            self.assertIn("report.docx", e.exception.message)
        zip_only = io.BytesIO()
        with zipfile.ZipFile(zip_only, "w") as z:
            z.writestr("hello.txt", "a zip that is not a Word file")
        with self.assertRaises(DocxProblem) as e:
            read_table(zip_only.getvalue())
        self.assertIn("not a valid Word file", e.exception.message)
        d = Document()
        d.add_paragraph("no table here")
        out = io.BytesIO()
        d.save(out)
        with self.assertRaises(DocxProblem) as e:
            read_table(out.getvalue(), "notes.docx")
        self.assertIn("has no table", e.exception.message)


class Grid(unittest.TestCase):
    def bad(self, heads, rows):
        with self.assertRaises(FormError) as e:
            check_grid(heads, rows)
        return e.exception.message

    def test_what_makes_a_grid_valid(self):
        self.assertEqual(check_grid(["Field", " Value "], [[" A ", " "]]), (["Field", "Value"], [["A", ""]]))
        self.assertIn("2 to", self.bad(["Field"], [["A"]]))
        self.assertIn("title", self.bad(["Field", ""], [["A", ""]]))
        self.assertIn("same title", self.bad(["Field", "field"], [["A", ""]]))
        self.assertIn("1 to", self.bad(HEADS, []))
        self.assertIn("cell for every column", self.bad(HEADS, [["A", ""]]))
        self.assertIn("field name", self.bad(HEADS, [["", "x", ""]]))
        self.assertIn("used twice", self.bad(HEADS, [["A", "", ""], ["a", "", ""]]))
        self.assertIn("at most", self.bad(HEADS, [["A", "x" * 501, ""]]))

    def test_progress_counts_only_the_cells_the_template_left_empty(self):
        rows = [["Process", "spot", ""], ["Material", "", "note"], ["Standard", "ISO 15614", "fixed text"]]
        base = [["Process", "", ""], ["Material", "", ""], ["Standard", "ISO 15614", "fixed text"]]
        self.assertEqual(progress(rows, base), (2, 6 - 2))


class Store(unittest.TestCase):
    def setUp(self):
        self.store = FormStore(":memory:", clock=iter(range(1000, 100000)).__next__)
        self.template = self.store.save_template(None, "Company WPS form", HEADS, ROWS, "ravi", "ravi@example.com")

    def code(self, fn, *a, **kw):
        with self.assertRaises(FormError) as e:
            fn(*a, **kw)
        return e.exception.code

    def test_templates_list_save_and_delete(self):
        self.assertEqual([(t["name"], t["fields"], t["columns"]) for t in self.store.list_templates()], [("Company WPS form", 4, 3)])
        self.assertEqual(self.code(self.store.save_template, None, "company wps FORM", HEADS, ROWS), "exists")
        again = self.store.save_template(self.template["id"], "Company WPS form v2", HEADS, ROWS + [["Position", "", ""]])
        self.assertEqual((again["name"], len(again["rows"])), ("Company WPS form v2", 5))
        self.assertEqual(self.code(self.store.save_template, None, " ", HEADS, ROWS), "bad_name")
        self.store.delete_template(again["id"])
        self.assertEqual(self.store.list_templates(), [])
        self.assertEqual(self.code(self.store.get_template, again["id"]), "not_found")

    def draft(self, name="Bracket", **kw):
        rows = kw.pop("rows", None) or [["Process", "spot", ""], ["Material", "mild steel", ""], ["Joint design", "", ""], ["Standard", "ISO 15614", "fixed text"]]
        return self.store.create_draft(name, self.template["id"], kw.pop("heads", HEADS), rows, "mina", "mina@example.com", **kw)

    def test_a_draft_is_made_from_a_template_when_saved_and_progress_is_counted(self):
        d = self.draft()
        self.assertEqual((d["filled"], d["total"], d["complete"]), (2, 6, False))
        self.assertEqual((d["template"], d["by"], d["email"]), ("Company WPS form", "mina", "mina@example.com"))
        self.assertEqual(self.store.get_draft(d["id"])["rows"][0], ["Process", "spot", ""])

    def test_a_person_without_the_right_cannot_change_the_shape_or_a_fixed_cell(self):
        wrong_rows = [["Process", "spot", ""], ["Material", "", ""], ["Joint design", "", ""], ["Standard", "ISO 15614", "fixed text"], ["Extra", "", ""]]
        self.assertEqual(self.code(self.draft, rows=wrong_rows), "no_restructure")
        self.assertEqual(self.code(self.draft, heads=["Field", "Value", "Notes"]), "no_restructure")
        changed_fixed = [["Process", "", ""], ["Material", "", ""], ["Joint design", "", ""], ["Standard", "ISO 9999", "fixed text"]]
        self.assertEqual(self.code(self.draft, rows=changed_fixed), "fixed_cell")
        d = self.draft()
        self.assertEqual(self.code(self.store.save_draft, d["id"], "Bracket", HEADS, wrong_rows), "no_restructure")

    def test_a_person_with_the_right_may_add_rows_and_columns_and_the_fixed_text_follows(self):
        heads = ["Field", "Value", "Remarks", "Checked by"]
        rows = [["Process", "spot", "", ""], ["Material", "mild steel", "", "ravi"], ["Standard", "ISO 15614", "fixed text", ""], ["Position", "flat", "", ""]]
        d = self.draft(heads=heads, rows=rows, can_restructure=True)
        self.assertEqual(len(d["rows"]), 4)
        self.assertEqual(d["base"][2], ["Standard", "ISO 15614", "fixed text", ""])           # the fixed cells kept; the new column and row start empty
        self.assertEqual(d["base"][3], ["Position", "", "", ""])
        self.assertEqual((d["filled"], d["total"]), (4, 10))                                  # what was typed in the new places counts as filled in
        saved = self.store.save_draft(d["id"], "Bracket 2", heads, rows[:3], can_restructure=True)
        self.assertEqual((saved["name"], len(saved["rows"])), ("Bracket 2", 3))

    def test_a_column_title_of_a_draft_is_never_renamed_even_by_a_person_who_may_restructure(self):
        d = self.draft(can_restructure=True)
        renamed = ["Field", "Renamed"] + list(d["heads"][2:])
        self.assertEqual(self.code(self.store.save_draft, d["id"], "Bracket", renamed, d["rows"], can_restructure=True), "no_rename")

    def test_save_replaces_the_values_and_a_later_template_change_does_not_disturb_the_draft(self):
        d = self.draft()
        rows = [list(r) for r in d["rows"]]
        rows[2][1] = "lap joint"
        saved = self.store.save_draft(d["id"], "Bracket", HEADS, rows)
        self.assertEqual(saved["filled"], 3)
        self.store.save_template(self.template["id"], "Company WPS form", HEADS, ROWS + [["New field", "", ""]])
        self.assertEqual(len(self.store.get_draft(d["id"])["rows"]), 4)
        self.store.delete_template(self.template["id"])
        gone = self.store.get_draft(d["id"])
        self.assertEqual((gone["template_id"], gone["template"]), (None, "Company WPS form"))

    def test_everyone_sees_every_draft_newest_first_with_paging_and_search(self):
        for i in range(30):
            self.draft(name=f"Draft {i:02d}")
        first = self.store.list_drafts(0, 25)
        self.assertEqual((first["total"], len(first["drafts"]), first["drafts"][0]["name"]), (30, 25, "Draft 29"))
        self.assertEqual(len(self.store.list_drafts(25, 25)["drafts"]), 5)
        self.assertEqual(self.store.list_drafts(0, 25, "draft 07")["total"], 1)
        self.assertEqual(self.store.list_drafts(0, 25, "mina@example")["total"], 30)
        self.assertEqual(self.store.list_drafts(0, 25, "100%")["total"], 0)                   # a percent sign is searched as text, not as a wildcard

    def test_delete_draft(self):
        d = self.draft()
        self.store.delete_draft(d["id"])
        self.assertEqual(self.code(self.store.get_draft, d["id"]), "not_found")
        self.assertEqual(self.code(self.store.delete_draft, d["id"]), "not_found")


if __name__ == "__main__":
    unittest.main()
