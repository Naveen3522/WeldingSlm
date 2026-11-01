"""Forgotten passwords: the emailed code, its limits, the one-time token and the new password."""
import re
import smtplib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from welding_ai.accounts import mailer
from welding_ai.accounts.recovery import MAX_GUESSES, PER_HOUR, RESEND_AFTER, SENT, TOKEN_TTL, Recovery, RecoveryError, ttl_from_env
from welding_ai.accounts.service import AccountError, AccountService


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class Inbox:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, to, subject, body):
        if self.fail:
            raise mailer.MailError("server down")
        self.sent.append((to, subject, body))

    def code(self):
        return re.search(r"code is (\d{6})", self.sent[-1][2]).group(1)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.accounts = AccountService(":memory:", clock=self.clock)
        self.accounts.create_user("ann", "ann@example.com", "engineer", password="Ann password 123")
        self.inbox = Inbox()
        self.rec = Recovery(":memory:", self.accounts, self.inbox, ttl=120, clock=self.clock)

    def code(self, who="ann@example.com"):
        self.rec.request(who)
        return self.inbox.code()

    def error(self, fn, *args):
        with self.assertRaises(RecoveryError) as cm:
            fn(*args)
        return cm.exception.code

    # ------------------------------------------------------------------ asking
    def test_the_answer_is_the_same_whether_or_not_the_account_exists(self):
        known, unknown = self.rec.request("ann@example.com"), self.rec.request("nobody")
        self.assertEqual(known, unknown)
        self.assertEqual(known["message"], SENT)
        self.assertEqual(len(self.inbox.sent), 1)                                   # but only the real account got an email

    def test_the_email_goes_to_the_accounts_address_and_says_how_long_the_code_lasts(self):
        self.rec.request("ann@example.com")
        to, subject, body = self.inbox.sent[0]
        self.assertEqual(to, "ann@example.com")
        self.assertRegex(body, r"code is \d{6}")
        self.assertIn("2 minutes", body)
        self.assertIn("ignore this email", body)

    def test_the_email_address_finds_the_account_ignoring_case_and_a_username_does_not(self):
        for who in ("ann@example.com", "ANN@example.com", "Ann@Example.COM", "  ann@example.com  "):
            self.clock.now += RESEND_AFTER + 1
            before = len(self.inbox.sent)
            self.rec.request(who)
            self.assertEqual(len(self.inbox.sent), before + 1, who)
        self.clock.now += RESEND_AFTER + 1
        before = len(self.inbox.sent)
        self.rec.request("ann")                                                  # a username may be shared, so it names nobody
        self.assertEqual(len(self.inbox.sent), before)

    def test_a_disabled_account_gets_no_code(self):
        admin, _ = self.accounts.create_user("boss", "boss@example.com", "admin", password="Boss password 12")
        user, _ = self.accounts.create_user("bob", "bob@example.com", "engineer", password="Bob password 123")
        self.accounts.set_active(user["id"], False)
        self.rec.request("bob@example.com")
        self.assertEqual(self.inbox.sent, [])

    def test_asking_again_too_soon_sends_nothing_and_says_nothing(self):
        first = self.rec.request("ann@example.com")
        again = self.rec.request("ann@example.com")
        self.assertEqual(first, again)
        self.assertEqual(len(self.inbox.sent), 1)
        self.clock.now += RESEND_AFTER + 1
        self.rec.request("ann@example.com")
        self.assertEqual(len(self.inbox.sent), 2)

    def test_at_most_five_codes_an_hour(self):
        for _ in range(PER_HOUR + 3):
            self.clock.now += RESEND_AFTER + 1
            self.rec.request("ann@example.com")
        self.assertEqual(len(self.inbox.sent), PER_HOUR)
        self.clock.now += 3600
        self.rec.request("ann@example.com")
        self.assertEqual(len(self.inbox.sent), PER_HOUR + 1)

    def test_a_mail_server_problem_is_not_told_to_the_person(self):
        broken = Recovery(":memory:", self.accounts, Inbox(fail=True), clock=self.clock)
        self.assertEqual(broken.request("ann@example.com")["message"], SENT)

    # ------------------------------------------------------------------ the code
    def test_the_right_code_gives_a_token_once(self):
        code = self.code()
        token = self.rec.verify("ann@example.com", code)
        self.assertTrue(token)
        self.assertEqual(self.error(self.rec.verify, "ann@example.com", code), "bad_code")        # a code works once

    def test_wrong_codes_and_unknown_accounts_get_the_same_error(self):
        code = self.code()
        wrong = "000000" if code != "000000" else "111111"
        self.assertEqual(self.error(self.rec.verify, "ann@example.com", wrong), "bad_code")
        self.assertEqual(self.error(self.rec.verify, "nobody", code), "bad_code")
        for junk in ("", "12345", "1234567", "abcdef", "12 34"):
            self.assertEqual(self.error(self.rec.verify, "ann@example.com", junk), "bad_code")

    def test_five_wrong_guesses_cancel_the_code_even_for_the_right_one(self):
        code = self.code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(MAX_GUESSES):
            self.error(self.rec.verify, "ann@example.com", wrong)
        self.assertEqual(self.error(self.rec.verify, "ann@example.com", code), "bad_code")
        self.clock.now += RESEND_AFTER + 1
        fresh = self.code()
        self.assertTrue(self.rec.verify("ann@example.com", fresh))                              # a new request starts again

    def test_the_code_expires(self):
        code = self.code()
        self.clock.now += 121
        self.assertEqual(self.error(self.rec.verify, "ann@example.com", code), "bad_code")

    def test_just_inside_the_lifetime_it_still_works(self):
        code = self.code()
        self.clock.now += 119
        self.assertTrue(self.rec.verify("ann@example.com", code))

    def test_a_new_code_cancels_the_old_one(self):
        old = self.code()
        self.clock.now += RESEND_AFTER + 1
        with mock.patch("welding_ai.accounts.recovery.secrets.randbelow", return_value=424242):
            new = self.code()
        self.assertEqual(new, "424242")
        self.assertNotEqual(old, new)
        self.assertEqual(self.error(self.rec.verify, "ann@example.com", old), "bad_code")
        self.assertTrue(self.rec.verify("ann@example.com", new))

    def test_codes_are_stored_hashed_not_in_the_clear(self):
        code = self.code()
        rows = [dict(r) for r in self.rec._db.execute("SELECT * FROM password_resets")]
        self.assertNotIn(code, " ".join(str(v) for r in rows for v in r.values()))

    # ------------------------------------------------------------------ the new password
    def test_the_token_sets_a_new_password_that_works_and_the_old_one_does_not(self):
        _, temp = self.accounts.create_user("eve", "eve@example.com", "engineer")
        token = self.rec.verify("eve@example.com", self.code("eve@example.com"))
        self.assertEqual(self.rec.reset(token, "A brand new password 5"), "eve")
        self.accounts.login("eve@example.com", "A brand new password 5")
        with self.assertRaises(AccountError):
            self.accounts.login("eve@example.com", temp)

    def test_it_ends_every_session_and_clears_the_forced_change(self):
        _, temp = self.accounts.create_user("eve", "eve@example.com", "engineer")
        session, user = self.accounts.login("eve@example.com", temp)
        self.assertTrue(user["must_change"])
        token = self.rec.verify("eve@example.com", self.code("eve@example.com"))
        self.rec.reset(token, "A brand new password 5")
        self.assertIsNone(self.accounts.session_user(session))
        _, again = self.accounts.login("eve@example.com", "A brand new password 5")
        self.assertFalse(again["must_change"])

    def test_it_clears_a_lock_from_too_many_wrong_passwords(self):
        for _ in range(5):
            with self.assertRaises(AccountError):
                self.accounts.login("ann@example.com", "Wrong password 1")
        with self.assertRaises(AccountError) as cm:
            self.accounts.login("ann@example.com", "Ann password 123")
        self.assertEqual(cm.exception.code, "locked")
        self.rec.reset(self.rec.verify("ann@example.com", self.code()), "A brand new password 5")
        self.accounts.login("ann@example.com", "A brand new password 5")

    def test_a_token_works_once(self):
        token = self.rec.verify("ann@example.com", self.code())
        self.rec.reset(token, "A brand new password 5")
        self.assertEqual(self.error(self.rec.reset, token, "Another password 77"), "bad_token")

    def test_a_weak_password_is_refused_and_the_token_can_be_used_again(self):
        token = self.rec.verify("ann@example.com", self.code())
        self.assertEqual(self.error(self.rec.reset, token, "short"), "weak_password")
        self.assertEqual(self.error(self.rec.reset, token, "ann"), "weak_password")
        self.assertEqual(self.rec.reset(token, "A brand new password 5"), "ann")

    def test_a_token_expires_and_a_made_up_one_is_refused(self):
        token = self.rec.verify("ann@example.com", self.code())
        self.clock.now += TOKEN_TTL + 1
        self.assertEqual(self.error(self.rec.reset, token, "A brand new password 5"), "bad_token")
        for junk in ("", None, "x" * 43):
            self.assertEqual(self.error(self.rec.reset, junk, "A brand new password 5"), "bad_token")

    def test_resetting_cancels_any_other_code_or_token_for_the_person(self):
        first = self.rec.verify("ann@example.com", self.code())
        self.clock.now += RESEND_AFTER + 1
        spare = self.code()
        self.rec.reset(first, "A brand new password 5")
        self.assertEqual(self.error(self.rec.verify, "ann@example.com", spare), "bad_code")

    def test_the_secret_survives_a_restart(self):
        path = Path(tempfile.mkdtemp()) / "rec.db"
        a = Recovery(path, self.accounts, self.inbox, clock=self.clock)
        a.request("ann@example.com")
        code = self.inbox.code()
        b = Recovery(path, self.accounts, self.inbox, clock=self.clock)                # a new process reading the same database
        self.assertTrue(b.verify("ann@example.com", code))


class Mail(unittest.TestCase):
    def test_the_outbox_writes_a_readable_file(self):
        folder = Path(tempfile.mkdtemp()) / "outbox"
        path = mailer.OutboxMailer(folder).send("ann@example.com", "Your code", "Your code is 123456")
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn("To: ann@example.com", text)
        self.assertIn("Your code is 123456", text)
        self.assertEqual(mailer.OutboxMailer(folder).describe()["mode"], "outbox")

    def test_smtp_uses_starttls_logs_in_and_sends(self):
        with mock.patch("welding_ai.accounts.mailer.smtplib.SMTP") as smtp:
            server = smtp.return_value.__enter__.return_value
            mailer.SmtpMailer("mail.example.com", 587, "u", "p", "weld@example.com").send("ann@example.com", "S", "B")
        smtp.assert_called_once()
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("u", "p")
        message = server.send_message.call_args.args[0]
        self.assertEqual((message["To"], message["From"], message["Subject"]), ("ann@example.com", "weld@example.com", "S"))

    def test_smtp_over_ssl_and_a_failure_becomes_a_mail_error(self):
        with mock.patch("welding_ai.accounts.mailer.smtplib.SMTP_SSL") as smtp:
            mailer.SmtpMailer("mail.example.com", 465, security="ssl", sender="a@b.co").send("x@y.co", "S", "B")
        smtp.assert_called_once()
        with mock.patch("welding_ai.accounts.mailer.smtplib.SMTP", side_effect=smtplib.SMTPConnectError(421, "no")):
            with self.assertRaises(mailer.MailError):
                mailer.SmtpMailer("mail.example.com").send("x@y.co", "S", "B")

    def test_the_mailer_is_chosen_from_the_environment(self):
        folder = Path(tempfile.mkdtemp())
        self.assertEqual(mailer.from_env(folder, {}).mode, "outbox")
        chosen = mailer.from_env(folder, {"WELD_SMTP_HOST": "mail.example.com", "WELD_SMTP_PORT": "25", "WELD_SMTP_FROM": "w@x.co", "WELD_SMTP_SECURITY": "NONE"})
        self.assertEqual((chosen.mode, chosen.port, chosen.security, chosen.sender), ("smtp", 25, "none", "w@x.co"))
        self.assertIn("mail.example.com", chosen.describe()["detail"])

    def test_the_lifetime_setting(self):
        self.assertEqual(ttl_from_env({}), 120)
        self.assertEqual(ttl_from_env({"WELD_OTP_TTL": "300"}), 300)
        self.assertEqual(ttl_from_env({"WELD_OTP_TTL": "5"}), 30)                       # never shorter than 30 seconds
        self.assertEqual(ttl_from_env({"WELD_OTP_TTL": "abc"}), 120)


if __name__ == "__main__":
    unittest.main()


class RecoveryKeyTests(RecoveryTests):
    def test_key_resets_the_password_and_is_replaced(self):
        key = self.rec.issue_key(1)
        self.assertRegex(key, r"^[A-Z2-9]{4}(-[A-Z2-9]{4}){3}$")
        name, fresh = self.rec.reset_with_key("ann@example.com", key.lower().replace("-", " "), "A new password 456")
        self.assertEqual(name, "ann")
        self.assertNotEqual(fresh, key)
        self.accounts.login("ann@example.com", "A new password 456")
        self.assertEqual(self.error(self.rec.reset_with_key, "ann@example.com", key, "Another password 789"), "bad_key")        # the old key is dead
        self.rec.reset_with_key("ann@example.com", fresh, "Third password 123")                                   

    def test_wrong_key_and_unknown_user_look_the_same(self):
        self.rec.issue_key(1)
        with self.assertRaises(RecoveryError) as a:
            self.rec.reset_with_key("ann@example.com", "AAAA-AAAA-AAAA-AAAA", "A new password 456")
        with self.assertRaises(RecoveryError) as b:
            self.rec.reset_with_key("nobody", "AAAA-AAAA-AAAA-AAAA", "A new password 456")
        self.assertEqual(a.exception.message, b.exception.message)

    def test_five_wrong_keys_block_even_the_right_one(self):
        key = self.rec.issue_key(1)
        for _ in range(5):
            self.error(self.rec.reset_with_key, "ann@example.com", "AAAA-AAAA-AAAA-AAAA", "A new password 456")
        self.assertEqual(self.error(self.rec.reset_with_key, "ann@example.com", key, "A new password 456"), "bad_key")
        self.clock.now += 3601
        self.rec.reset_with_key("ann@example.com", key, "A new password 456")

    def test_weak_password_keeps_the_key(self):
        key = self.rec.issue_key(1)
        self.assertEqual(self.error(self.rec.reset_with_key, "ann@example.com", key, "short"), "weak_password")
        self.rec.reset_with_key("ann@example.com", key, "A new password 456")

    def test_an_admin_reset_ends_the_old_key_and_the_next_change_gives_a_new_one(self):
        key = self.rec.issue_key(1)
        self.rec.revoke_key(1)                                                       # what the Admin reset route does
        self.assertEqual(self.error(self.rec.reset_with_key, "ann@example.com", key, "A new password 456"), "bad_key")
        fresh = self.rec.issue_key(1)                                                # shown to the person after they choose their own password
        self.assertNotEqual(fresh, key)
        name, _ = self.rec.reset_with_key("ann@example.com", fresh, "A new password 456")
        self.assertEqual(name, "ann")

    def test_forgot_password_in_two_steps_check_the_key_then_choose_a_password(self):
        key = self.rec.issue_key(1)
        token = self.rec.verify_key("ann@example.com", key.lower())
        self.assertEqual(self.error(self.rec.reset_with_token, token, "weak"), "weak_password")          # a weak password leaves the token usable
        name, fresh = self.rec.reset_with_token(token, "A new password 456")
        self.assertEqual(name, "ann")
        self.assertNotEqual(fresh, key)
        self.accounts.login("ann@example.com", "A new password 456")
        self.assertEqual(self.error(self.rec.reset_with_token, token, "Another password 789"), "bad_token")   # the token works once
        self.assertEqual(self.error(self.rec.verify_key, "ann@example.com", key), "bad_key")                # the old key is dead
        self.assertTrue(self.rec.verify_key("ann@example.com", fresh))

    def test_step_one_gives_the_same_answer_for_a_wrong_key_and_an_unknown_email_and_blocks_after_five(self):
        self.rec.issue_key(1)
        a = self.error(self.rec.verify_key, "ann@example.com", "AAAA-BBBB-CCCC-DDDD")
        b = self.error(self.rec.verify_key, "nobody@example.com", "AAAA-BBBB-CCCC-DDDD")
        self.assertEqual((a, b), ("bad_key", "bad_key"))
        for _ in range(4):
            self.error(self.rec.verify_key, "ann@example.com", "AAAA-BBBB-CCCC-DDDD")
        right = self.rec.issue_key(1)
        self.assertEqual(self.error(self.rec.verify_key, "ann@example.com", right), "bad_key")             # blocked for an hour, even with the right key

    def test_login_by_email(self):
        token, user = self.accounts.login("ANN@example.com", "Ann password 123")
        self.assertEqual(user["username"], "ann")
