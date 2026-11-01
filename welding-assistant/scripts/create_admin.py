"""Create an administrator account (run once on the server computer; everyone else is added from the page).

    python scripts/create_admin.py                 the default administrator: SystemAdmin, admin@example.com, password Weld@Assist
    python scripts/create_admin.py --ask           type the name, email and password yourself
    python scripts/create_admin.py --username naveen --email naveen@example.com
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from welding_ai import paths
from welding_ai.accounts.admin_file import AdminFile
from welding_ai.accounts.recovery import Recovery
from welding_ai.accounts.service import AccountError, AccountService


DEFAULT_USERNAME, DEFAULT_EMAIL, DEFAULT_PASSWORD = "SystemAdmin", "admin@example.com", "Weld@Assist"      # change the password after the first sign-in


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--username")
    ap.add_argument("--email")
    ap.add_argument("--ask", action="store_true", help="type the details instead of using the default administrator")
    args = ap.parse_args()
    default = not (args.ask or args.username or args.email)

    vault = AdminFile(paths.ADMIN_FILE)
    accounts = AccountService(paths.DB_PATH, vault=vault)
    if default:
        username, email, password = DEFAULT_USERNAME, DEFAULT_EMAIL, DEFAULT_PASSWORD
    else:
        username = args.username or input("Username: ").strip()
        email = args.email or input("Email: ").strip()
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat password: "):
            sys.exit("The passwords do not match.")
    try:
        user, _ = accounts.create_user(username, email, "admin", password=password)
    except AccountError as e:
        sys.exit(e.message)
    Recovery(paths.DB_PATH, accounts, None, vault=vault).issue_key(user["id"])      # the key goes into the file too
    print(f"Created administrator '{user['username']}' in {paths.DB_PATH}")
    print(f"Username, email, password and recovery key are in {paths.ADMIN_FILE} (kept current when the password changes)")


if __name__ == "__main__":
    main()
