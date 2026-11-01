"""Permissions v2: screens and controls with read and write levels, the rules the file must keep, and how a level is compared."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from welding_ai.accounts import permissions as P


def tree_with(**changes):
    """The built-in tree with some levels changed: tree_with(**{"4": (1, 1), "4.5": (10, 10)})."""
    tree = P.defaults()
    for s in tree:
        if s["id"] in changes:
            s["read"], s["write"] = changes[s["id"]]
        for c in s["controls"]:
            if c["id"] in changes:
                c["read"], c["write"] = changes[c["id"]]
    return tree


class Checks(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "permissions.xml"
        self.p = P.Permissions(self.path)

    def test_a_control_needs_its_own_level_and_its_screens(self):
        self.assertTrue(self.p.can(1, "4.1", "write"))                    # a User makes a draft
        self.assertFalse(self.p.can(1, "4.5", "read"))                    # but never sees Delete a draft
        self.assertTrue(self.p.can(10, "4.5", "write"))
        self.assertFalse(self.p.can(1, "5.1", "read"))                    # Templates is a Weld Engineer screen
        self.assertTrue(self.p.can(5, "5.1", "write"))
        self.assertTrue(self.p.can(5, "6", "read"))                                        # Models: a Weld Engineer views ...
        self.assertFalse(self.p.can(5, "6.2", "write") or self.p.can(5, "6.4", "write"))   # ... and only an Admin uses, renames, deletes or trains
        self.assertTrue(self.p.can(5, "6.1", "read") and not self.p.can(5, "6.4", "read"))

    def test_an_entry_the_file_does_not_know_is_admin_only(self):
        self.assertFalse(self.p.can(9, "99"))
        self.assertFalse(self.p.can(9, "4.99", "write"))
        self.assertTrue(self.p.can(10, "99"))

    def test_the_named_rights_the_routes_still_use(self):
        a = self.p.allowed
        for right in ("ask", "summary", "history", "agents", "suggest_formula"):
            self.assertTrue(a(1, right), right)
        self.assertFalse(a(1, "edit_formula") or a(1, "library") or a(1, "models") or a(1, "users"))
        self.assertTrue(a(5, "edit_formula") and a(5, "library") and a(5, "view_models") and a(5, "training_pairs"))
        self.assertFalse(a(5, "models") or a(5, "users") or a(5, "activity") or a(5, "admin"))
        self.assertTrue(all(a(10, right) for right in P.RIGHTS))

    def test_resolve_lists_what_a_level_may_see_and_do(self):
        user = self.p.resolve(1)
        self.assertTrue(user["controls"]["4.1"]["write"])
        self.assertFalse(user["controls"]["4.5"]["read"])
        self.assertFalse(user["screens"]["11"]["read"])
        self.assertTrue(all(v["read"] and v["write"] for v in self.p.resolve(10)["controls"].values()))


class TheFile(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "permissions.xml"
        self.p = P.Permissions(self.path)
        self.p.ensure_file()

    def test_ensure_file_writes_the_built_in_levels_and_they_read_back(self):
        text = self.path.read_text(encoding="utf-8")
        self.assertIn('<Permissions version="2">', text)
        self.assertIn('<Screen id="4" name="Drafts" read="1" write="1">', text)
        self.assertEqual(P.parse(text), P.defaults())

    def test_reindex_is_for_weld_engineers_and_admins_and_an_admin_can_change_that(self):
        self.assertFalse(self.p.allowed(1, "reindex"))
        self.assertTrue(self.p.allowed(5, "reindex") and self.p.allowed(10, "reindex"))
        self.p.save(tree_with(**{"2.4": (10, 10)}))
        self.assertFalse(self.p.allowed(5, "reindex"))
        self.assertTrue(self.p.allowed(10, "reindex"))
        self.p.save(tree_with(**{"2.4": (3, 3)}))
        self.assertTrue(self.p.allowed(3, "reindex"))

    def test_a_file_from_an_older_version_gets_the_new_control_and_keeps_what_was_set(self):
        old = tree_with(**{"4.5": (1, 1)})
        for s in old:
            s["controls"] = [c for c in s["controls"] if c["id"] != "2.4"]
        self.path.write_text(P.render(old), encoding="utf-8")
        p = P.Permissions(self.path)
        self.assertFalse(p.allowed(5, "reindex"))                        # missing from the file: Admin only
        p.ensure_file()
        self.assertTrue(p.allowed(5, "reindex") and not p.allowed(1, "reindex"))
        self.assertTrue(p.can(1, "4.5", "write"))                        # a level an administrator changed is untouched
        self.assertTrue(self.path.with_suffix(".xml.bak").exists())
        self.assertIn('id="2.4"', self.path.read_text(encoding="utf-8"))

    def test_only_an_admin_can_see_or_save_the_permissions(self):
        for right in ("permissions", "permissions_save"):
            self.assertFalse(self.p.allowed(9, right), right)
            self.assertTrue(self.p.allowed(10, right), right)
        with self.assertRaises(P.PermissionsError):
            self.p.save(tree_with(**{"10": (5, 5)}))

    def test_a_hand_edit_applies_without_a_restart(self):
        self.assertFalse(self.p.can(1, "4.5", "read"))
        self.path.write_text(P.render(tree_with(**{"4.5": (1, 1)})), encoding="utf-8")
        self.assertTrue(self.p.can(1, "4.5", "write"))

    def test_a_file_that_breaks_a_rule_is_refused_and_the_last_good_levels_stay(self):
        good = self.path.read_text(encoding="utf-8")
        for broken, why in ((good.replace('version="2"', 'version="1"'), "version"), ("<Permissions", "XML"),
                            (good.replace('<Screen id="4" name="Drafts" read="1" write="1">', '<Screen id="4" name="Drafts" read="1" write="x">'), "whole number"),
                            (good.replace('read="1" write="1">', 'read="3" write="2">', 1), "write level"),
                            (good.replace('<Screen id="4"', '<Screen id="44"'), "not a screen"),
                            (good.replace('<Control id="4.1" name="New draft (+)" read="1" write="1" />', '<Control id="4.1" name="New draft (+)" read="1" write="1" /><Control id="4.1" name="x" read="1" write="1" />'), "twice")):
            self.path.write_text(broken, encoding="utf-8")
            self.p._stamp = None
            self.p.reload()
            self.assertIn(why, self.p.problem, why)
            self.assertTrue(self.p.can(1, "4.1", "write"), why)                     # still the good levels
        self.path.write_text(good, encoding="utf-8")
        self.p._stamp = None
        self.p.reload()
        self.assertEqual(self.p.problem, "")

    def test_a_control_cannot_be_below_its_screen_and_the_users_screens_stay_at_10(self):
        with self.assertRaises(P.PermissionsError) as e:
            P.validate(tree_with(**{"5": (5, 5), "5.1": (2, 5)}))
        self.assertIn("below its screen", str(e.exception))
        with self.assertRaises(P.PermissionsError) as e:
            P.validate(tree_with(**{"8": (5, 10)}))
        self.assertIn("Admin only", str(e.exception))
        with self.assertRaises(P.PermissionsError):
            P.validate(tree_with(**{"4": (11, 11)}))
        with self.assertRaises(P.PermissionsError):
            P.validate(tree_with(**{"4.2": (0, 1)}))

    def test_save_checks_everything_first_keeps_a_backup_and_applies_at_once(self):
        with self.assertRaises(P.PermissionsError):
            self.p.save(tree_with(**{"4.5": (5, 3)}))
        self.assertFalse(self.p.can(5, "4.5", "read"))                              # nothing was written
        saved = self.p.save(tree_with(**{"4.5": (5, 5)}))
        self.assertTrue(self.p.can(5, "4.5", "write"))
        self.assertTrue(self.path.with_suffix(".xml.bak").exists())
        self.assertEqual(next(c for s in saved for c in s["controls"] if c["id"] == "4.5")["read"], 5)
        self.assertEqual(P.parse(self.path.read_text(encoding="utf-8")), saved)


if __name__ == "__main__":
    unittest.main()
