"""Manage serverbot accounts, sessions, and maintenance."""

import argparse
from getpass import getpass
import sys

from psycopg import Error
from psycopg.errors import UniqueViolation

from account_store import Accounts, database_settings


def main():
    parser = argparse.ArgumentParser(description="Manage serverbot accounts, sessions, and maintenance.")
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("create", help="Create an app account")
    reset = commands.add_parser("reset-password", help="Change a password and revoke that user's sessions")
    for command in (create, reset):
        command.add_argument("--username", required=True, help="Windows app login name")
    commands.add_parser("revoke-all-sessions", help="Revoke all app sessions; allow new logins")
    maintenance = commands.add_parser("maintenance", help="Inspect or change app maintenance mode")
    maintenance.add_argument(
        "mode", choices=("on", "off", "status"),
        help="on: block access and revoke all sessions; off: allow logins; status: inspect",
    )
    args = parser.parse_args()
    try:
        accounts = Accounts(database_settings())
        if args.action == "revoke-all-sessions":
            print(f"All app sessions revoked: {accounts.revoke_all_sessions()}.")
            return 0
        if args.action == "maintenance":
            if args.mode == "status":
                enabled, _ = accounts.control()
                print(f"Maintenance is {'on' if enabled else 'off'}.")
            elif args.mode == "on":
                print(f"Maintenance enabled. App sessions revoked: {accounts.set_maintenance(True)}.")
            else:
                accounts.set_maintenance(False)
                print("Maintenance disabled. New logins are allowed.")
            return 0

        username = args.username.strip()
        if not 1 <= len(username) <= 128:
            parser.error("Enter a username between 1 and 128 characters")
        if not sys.stdin.isatty():
            parser.error("Run this command in an interactive terminal")
        password = getpass("New app password: ")
        if not 12 <= len(password) <= 256:
            parser.error("Enter a password between 12 and 256 characters")
        if password != getpass("Confirm app password: "):
            parser.error("Passwords do not match")
        if args.action == "create":
            user_id = accounts.create(username, password)
            print(f"App account created. User ID: {user_id}")
        elif accounts.reset_password(username, password):
            print("Password changed. Existing sessions were revoked.")
        else:
            print("App account not found.", file=sys.stderr)
            return 1
    except ValueError:
        print("Check DB_HOST, DB_PORT, DB_NAME, DB_USER and DB_PASSWORD.", file=sys.stderr)
        return 1
    except UniqueViolation:
        print("App account already exists.", file=sys.stderr)
        return 1
    except Error as error:
        if error.sqlstate == "P0999":
            print("Monthly account limit reached (999). Create the account next month.", file=sys.stderr)
        else:
            print("Database operation failed. Check the DB settings and schema.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
