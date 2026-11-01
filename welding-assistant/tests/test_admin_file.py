"""The administrator credentials file: written for administrators only, and kept current whenever a password or recovery key changes."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from welding_ai.accounts.admin_file import AdminFile
from welding_ai.accounts.recovery import Recovery
from welding_ai.accounts.service import AccountService

GOOD, BETTER, BEST = "Weld@Assist1", "Arc@Welding2", "Bead@Pass3x"


class AdminFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "admin-credentials.txt"
        self.vault = AdminFile(self.path)
        self.accounts = AccountService(":memory:", vault=self.vault)
        self.rec = Recovery(":memory:", self.accounts, None, vault=self.vault)
        self.admin, _ = self.accounts.create_user("SystemAdmin", "admin@example.com", "admin", password=GOOD)

    def tearDown(self):
        self.dir.cleanup()

    def text(self):
        return self.path.read_text(encoding="utf-8")

    def test_new_admin_is_written(self):
        t = self.text()
        for part in ("SystemAdmin", "admin@example.com", GOOD):
            self.assertIn(part, t)

    def test_password_change_updates_the_file(self):
        self.accounts.change_password(self.admin["id"], GOOD, BETTER)
        self.assertIn(BETTER, self.text())
        self.assertNotIn(GOOD, self.text())

    def test_recovery_key_and_reset_by_key(self):
        key = self.rec.issue_key(self.admin["id"])
        self.assertIn(key, self.text())
        self.assertIn(GOOD, self.text())                                  # issuing a key keeps the password line
        _, new_key = self.rec.reset_with_key("admin@example.com", key, BEST)
        self.assertIn(BEST, self.text())
        self.assertIn(new_key, self.text())
        self.assertNotIn(key, self.text())

    def test_admin_reset_writes_the_one_time_password(self):
        temp = self.accounts.admin_reset(self.admin["id"])
        self.assertIn(temp, self.text())

    def test_people_who_are_not_administrators_are_never_written(self):
        user, temp = self.accounts.create_user("Sam", "sam@example.com")
        self.accounts.change_password(user["id"], temp, BETTER)
        self.assertNotIn("sam@example.com", self.text())
        self.assertNotIn(BETTER, self.text())

    def test_two_administrators_and_deleting_one(self):
        other, _ = self.accounts.create_user("Second", "second@example.com", "admin", password=BETTER)
        self.assertEqual(self.text().count("Email"), 2)
        self.accounts.delete_user(other["id"], self.admin["id"])
        self.assertNotIn("second@example.com", self.text())
        self.assertIn("admin@example.com", self.text())

    def test_a_file_problem_never_stops_the_change(self):
        self.path.parent.joinpath("blocked").mkdir()
        self.vault.path = self.path.parent / "blocked"                    # a folder where the file should be: writing fails
        self.accounts.change_password(self.admin["id"], GOOD, BETTER)     # still succeeds
        self.assertTrue(self.accounts.login("admin@example.com", BETTER))


if __name__ == "__main__":
    unittest.main()
