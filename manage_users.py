"""Create app accounts and reset passwords using the server's DB settings."""

import argparse
from getpass import getpass
import sys

from psycopg import Error
from psycopg.errors import UniqueViolation

from account_store import Accounts, database_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("create", "reset-password"))
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    username = args.username.strip()
    if not 1 <= len(username) <= 128:
        parser.error("Enter a username between 1 and 128 characters")
    if not sys.stdin.isatty():
        parser.error("Run this command in an interactive terminal")
    try:
        accounts = Accounts(database_settings())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    password = getpass("New app password: ")
    if not 12 <= len(password) <= 256:
        parser.error("Enter a password between 12 and 256 characters")
    if password != getpass("Confirm app password: "):
        parser.error("Passwords do not match")
    try:
        if args.action == "create":
            accounts.create(username, password)
            print("App account created.")
        elif accounts.reset_password(username, password):
            print("Password changed. Existing sessions were revoked.")
        else:
            print("App account not found.", file=sys.stderr)
            return 1
    except UniqueViolation:
        print("App account already exists.", file=sys.stderr)
        return 1
    except Error:
        print("Database operation failed. Check the DB settings and schema.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
