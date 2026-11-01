"""History, feedback and the audit log (in-memory SQLite, fake clock)."""
import csv
import io
import unittest

from welding_ai.activity.service import ActivityError, ActivityLog, safe_cell, to_csv


class Clock:
    now = 1_000_000.0

    def __call__(self):
        Clock.now += 1
        return Clock.now


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.log = ActivityLog(":memory:", clock=Clock())

    def add(self, user, question, **kw):
        return self.log.record(user, question, kw.pop("answer", "an answer"), route=kw.pop("route", "composed"), label=kw.pop("label", "Quoted"), **kw)

    def test_history_is_per_user_distinct_and_newest_first(self):
        for q in ("what is porosity", "what is a nugget", "what is porosity"):
            self.add("ann", q)
        self.add("bob", "bob's private question")
        self.assertEqual(self.log.history("ann"), ["what is porosity", "what is a nugget"])
        self.assertEqual(self.log.history("bob"), ["bob's private question"])
        self.assertEqual(self.log.history("nobody"), [])

    def test_history_is_capped(self):
        for i in range(60):
            self.add("ann", f"question {i}")
        self.assertEqual(len(self.log.history("ann")), 40)
        self.assertEqual(self.log.history("ann")[0], "question 59")

    def test_clearing_history_hides_it_but_keeps_the_audit_log(self):
        self.add("ann", "q1")
        self.add("bob", "q2")
        self.log.clear_history("ann")
        self.assertEqual(self.log.history("ann"), [])
        self.assertEqual(self.log.history("bob"), ["q2"])
        self.assertEqual(self.log.audit_page()["total"], 2)             # nothing is lost

    def test_a_question_asked_again_after_clearing_shows_again(self):
        self.add("ann", "q1")
        self.log.clear_history("ann")
        self.add("ann", "q1")
        self.assertEqual(self.log.history("ann"), ["q1"])

    def test_feedback_only_on_your_own_answer_and_replaces_the_earlier_one(self):
        mine = self.add("ann", "q1")
        with self.assertRaises(ActivityError) as cm:
            self.log.add_feedback("bob", mine, 1)
        self.assertEqual(cm.exception.code, "not_found")
        with self.assertRaises(ActivityError) as cm:
            self.log.add_feedback("ann", 9999, 1)
        self.assertEqual(cm.exception.code, "not_found")
        with self.assertRaises(ActivityError) as cm:
            self.log.add_feedback("ann", mine, 5)
        self.assertEqual(cm.exception.code, "bad_score")
        self.log.add_feedback("ann", mine, 1)
        self.log.add_feedback("ann", mine, -1, "  the unit was wrong  ")
        row = self.log.audit_page()["rows"][0]
        self.assertEqual((row["score"], row["comment"]), (-1, "the unit was wrong"))
        self.assertEqual(self.log.usage(), {"questions": 1, "people": 1, "helpful": 0, "not_helpful": 1})

    def test_audit_filters(self):
        a = self.add("ann", "porosity in GMAW", answer="gas shield")
        self.add("bob", "nugget size", answer="five root t")
        self.log.add_feedback("ann", a, -1, "wrong")
        self.assertEqual(self.log.audit_page(user="bob")["total"], 1)
        self.assertEqual(self.log.audit_page(text="gas")["total"], 1)
        self.assertEqual(self.log.audit_page(verdict="bad")["total"], 1)
        self.assertEqual(self.log.audit_page(verdict="none")["rows"][0]["username"], "bob")
        self.assertEqual(self.log.audit_page(text="100%")["total"], 0)  # % is not a wildcard
        self.assertEqual(self.log.audit_page(text="_")["total"], 0)     # nor is _

    def test_audit_pages_newest_first(self):
        for i in range(5):
            self.add("ann", f"q{i}")
        page = self.log.audit_page(offset=1, limit=2)
        self.assertEqual(page["total"], 5)
        self.assertEqual([r["question"] for r in page["rows"]], ["q3", "q2"])

    def test_long_text_is_cut_not_rejected(self):
        i = self.log.record("ann", "q" * 5000, "a" * 20000)
        row = self.log.audit_page()["rows"][0]
        self.assertEqual((row["id"], len(row["question"]), len(row["answer"])), (i, 1000, 8000))

    def test_csv_neutralises_spreadsheet_formulas(self):
        self.assertEqual(safe_cell("=1+1"), "'=1+1")
        self.assertEqual(safe_cell("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(safe_cell("-2+3"), "'-2+3")
        self.assertEqual(safe_cell("plain"), "plain")
        self.assertEqual(safe_cell(None), "")
        self.add("ann", '=HYPERLINK("http://evil","click")', answer="+cmd")
        rows = list(csv.reader(io.StringIO(to_csv(self.log.export_rows()).lstrip("﻿"))))
        self.assertEqual(rows[0][0:5], ["ID", "Time", "User", "Email", "Answered by"])
        self.assertTrue(rows[1][5].startswith("'="))
        self.assertTrue(rows[1][6].startswith("'+"))

    def test_csv_keeps_commas_quotes_and_newlines(self):
        self.add("ann", 'a, "b"\nc')
        rows = list(csv.reader(io.StringIO(to_csv(self.log.export_rows()).lstrip("﻿"))))
        self.assertEqual(rows[1][5], 'a, "b"\nc')

    # ------------------------------------------------------------------ people are told apart by email, because usernames may be shared
    def test_two_people_with_the_same_username_keep_separate_histories(self):
        self.add("ravi", "ravi one's question", email="ravi@example.com")
        self.add("ravi", "ravi two's question", email="ravi.k@example.com")
        self.assertEqual(self.log.history("ravi", "ravi@example.com"), ["ravi one's question"])
        self.assertEqual(self.log.history("ravi", "RAVI.K@example.com"), ["ravi two's question"])
        self.log.clear_history("ravi", "ravi@example.com")
        self.assertEqual(self.log.history("ravi", "ravi@example.com"), [])
        self.assertEqual(self.log.history("ravi", "ravi.k@example.com"), ["ravi two's question"])         # the other person's list is untouched
        self.assertEqual(self.log.audit_page()["total"], 2)                                               # and the audit log keeps both

    def test_feedback_is_only_on_your_own_answer_even_with_a_shared_name(self):
        mine = self.add("ravi", "q", email="ravi@example.com")
        with self.assertRaises(ActivityError):
            self.log.add_feedback("ravi", mine, 1, "", "ravi.k@example.com")
        self.log.add_feedback("ravi", mine, 1, "", "ravi@example.com")

    def test_history_can_be_narrowed_to_what_one_agent_answered(self):
        self.add("ann", "porosity?", email="ann@example.com", area="Fault Finder")
        self.add("ann", "which standard?", email="ann@example.com", area="Procedure Advisor")
        self.add("ann", "anything", email="ann@example.com", area="General")
        self.assertEqual(self.log.history("ann", "ann@example.com", "Fault Finder"), ["porosity?"])
        self.assertEqual(len(self.log.history("ann", "ann@example.com")), 3)

    def test_the_audit_filters_by_person_email_and_by_area(self):
        self.add("ravi", "one", email="ravi@example.com", area="Formulas")
        self.add("ravi", "two", email="ravi.k@example.com", area="General")
        by_email = self.log.audit_page(user="ravi.k@example.com")
        self.assertEqual([r["question"] for r in by_email["rows"]], ["two"])
        self.assertEqual(by_email["rows"][0]["email"], "ravi.k@example.com")
        self.assertEqual([r["question"] for r in self.log.audit_page(area="Formulas")["rows"]], ["one"])
        self.assertEqual(self.log.usage()["people"], 2)                                                   # two people, though one name

    def test_an_older_log_learns_the_email_of_each_person_from_the_accounts_table(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        path = Path(tempfile.mkdtemp()) / "old.db"
        db = sqlite3.connect(path)
        db.executescript("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, email TEXT);"
                         "INSERT INTO users (username, email) VALUES ('ann', 'ann@example.com'), ('twin', 'a@example.com'), ('twin', 'b@example.com');"
                         "CREATE TABLE chat_log (id INTEGER PRIMARY KEY, username TEXT NOT NULL, asked REAL NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL, route TEXT NOT NULL,"
                         " label TEXT NOT NULL, mode TEXT NOT NULL, model TEXT NOT NULL, match TEXT NOT NULL, documents TEXT NOT NULL, ms INTEGER NOT NULL, hidden INTEGER NOT NULL DEFAULT 0);"
                         "INSERT INTO chat_log (username, asked, question, answer, route, label, mode, model, match, documents, ms) VALUES ('ann', 1, 'q1', 'a', '', '', '', '', '', '', 0),"
                         " ('twin', 2, 'q2', 'a', '', '', '', '', '', '', 0);")
        db.commit()
        db.close()
        log = ActivityLog(path)
        rows = {r["question"]: r["email"] for r in log.audit_page()["rows"]}
        self.assertEqual(rows, {"q1": "ann@example.com", "q2": ""})        # a name two people share cannot be traced to one, so that row stays with the name
        self.assertEqual(log.history("ann", "ann@example.com"), ["q1"])


if __name__ == "__main__":
    unittest.main()
