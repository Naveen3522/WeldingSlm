"""Sending the password-recovery email.

With a mail server configured (WELD_SMTP_HOST and the rest, from the company's IT) the code goes by email. Without one it is written to a text file in
an outbox folder instead, so the feature can be tried and an administrator can still pass a code on: that mode is announced to administrators in the
Users tab and never pretends to have sent an email.
"""
import os
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path


class MailError(Exception):
    pass


class OutboxMailer:
    mode = "outbox"

    def __init__(self, folder, clock=time.time):
        self.folder, self.clock = Path(folder), clock

    def describe(self):
        return {"mode": "outbox", "detail": f"No mail server is set up, so recovery emails are written as text files in {self.folder}."}

    def send(self, to, subject, body):
        self.folder.mkdir(parents=True, exist_ok=True)
        name = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(self.clock()))}-{re.sub(r'[^A-Za-z0-9.@_-]', '_', to)}.txt"
        path = self.folder / name
        path.write_text(f"To: {to}\nSubject: {subject}\n\n{body}\n", encoding="utf-8")
        return str(path)


class SmtpMailer:
    mode = "smtp"

    def __init__(self, host, port=587, user="", password="", sender="", security="starttls", timeout=15):
        self.host, self.port, self.user, self.password = host, int(port), user, password
        self.sender, self.security, self.timeout = sender or user, security, timeout

    def describe(self):
        return {"mode": "smtp", "detail": f"Recovery emails are sent through {self.host}:{self.port} from {self.sender}."}

    def send(self, to, subject, body):
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.sender, to, subject
        message.set_content(body)
        try:
            if self.security == "ssl":
                server = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            with server as connection:
                if self.security == "starttls":
                    connection.starttls(context=ssl.create_default_context())
                if self.user:
                    connection.login(self.user, self.password)
                connection.send_message(message)
        except (smtplib.SMTPException, OSError) as e:
            raise MailError(f"{type(e).__name__}: {e}") from e
        return "sent"


def from_env(outbox_folder, env=None):
    """The configured mailer: SMTP when WELD_SMTP_HOST is set, else the outbox."""
    env = os.environ if env is None else env
    if env.get("WELD_SMTP_HOST"):
        return SmtpMailer(env["WELD_SMTP_HOST"], env.get("WELD_SMTP_PORT", 587), env.get("WELD_SMTP_USER", ""), env.get("WELD_SMTP_PASSWORD", ""),
                          env.get("WELD_SMTP_FROM", ""), env.get("WELD_SMTP_SECURITY", "starttls").lower())
    return OutboxMailer(outbox_folder)
