"""PostgreSQL-backed app accounts and opaque sessions."""

from hashlib import sha256
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
import psycopg


PASSWORDS = PasswordHasher()
SETTINGS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))


def database_settings():
    if not all(os.environ.get(name) for name in SETTINGS):
        raise ValueError("Set DB_HOST, DB_PORT, DB_NAME, DB_USER and DB_PASSWORD")
    return dict(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=4,
    )


def token_hash(token):
    return sha256(token.encode("utf-8")).digest()


class Accounts:
    def __init__(self, settings):
        self.settings = settings

    def connect(self):
        return psycopg.connect(**self.settings)

    def login(self, username, password):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, password_hash FROM bot_auth.app_users WHERE username = %s FOR SHARE",
                (username,),
            ).fetchone()
            try:
                PASSWORDS.verify(row[1] if row else DUMMY_HASH, password)
            except (VerificationError, InvalidHashError):
                return None
            if row is None:
                return None
            token = secrets.token_urlsafe(32)
            connection.execute(
                """INSERT INTO bot_auth.app_sessions (user_id, token_hash, expires_at)
                   VALUES (%s, %s, clock_timestamp() + interval '1 hour')""",
                (row[0], token_hash(token)),
            )
            return token

    def session(self, token):
        with self.connect() as connection:
            return connection.execute(
                """SELECT user_id, expires_at FROM bot_auth.app_sessions
                   WHERE token_hash = %s AND expires_at > clock_timestamp()""",
                (token_hash(token),),
            ).fetchone()

    def valid(self, token):
        return self.session(token) is not None

    def extend(self, token):
        with self.connect() as connection:
            row = connection.execute(
                """UPDATE bot_auth.app_sessions
                   SET expires_at = GREATEST(expires_at, clock_timestamp() + interval '1 hour')
                   WHERE token_hash = %s AND expires_at > clock_timestamp()
                   RETURNING expires_at""",
                (token_hash(token),),
            ).fetchone()
            return row[0] if row is not None else None

    def logout(self, token):
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM bot_auth.app_sessions WHERE token_hash = %s",
                (token_hash(token),),
            )

    def create(self, username, password):
        digest = PASSWORDS.hash(password)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO bot_auth.app_users (username, password_hash) VALUES (%s, %s)",
                (username, digest),
            )

    def reset_password(self, username, password):
        digest = PASSWORDS.hash(password)
        with self.connect() as connection:
            row = connection.execute(
                """UPDATE bot_auth.app_users SET password_hash = %s
                   WHERE username = %s RETURNING id""",
                (digest, username),
            ).fetchone()
            if row is None:
                return False
            connection.execute("DELETE FROM bot_auth.app_sessions WHERE user_id = %s", (row[0],))
            return True
