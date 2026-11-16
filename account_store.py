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


class MaintenanceActive(Exception):
    pass


class Accounts:
    def __init__(self, settings):
        self.settings = settings

    def connect(self):
        return psycopg.connect(**self.settings)

    @staticmethod
    def read_control(connection, *, lock=False):
        query = "SELECT maintenance, session_generation FROM bot_auth.app_control WHERE singleton"
        row = connection.execute(query + (" FOR SHARE" if lock else "")).fetchone()
        if row is None:
            raise psycopg.DatabaseError("The app control row is missing")
        return row

    def control(self):
        with self.connect() as connection:
            return self.read_control(connection)

    @staticmethod
    def _end_sessions(connection, event_type, *, user_id=None, digest=None, source_ip=None, reason=None):
        result = connection.execute(
            """WITH target AS (
                   SELECT %s::bigint AS user_id, %s::bytea AS digest
               ), ended AS (
                   DELETE FROM bot_auth.app_sessions AS session
                   USING target
                   WHERE (target.user_id IS NULL OR session.user_id = target.user_id)
                     AND (target.digest IS NULL OR session.token_hash = target.digest)
                   RETURNING session.id, session.user_id, session.expires_at
               )
               INSERT INTO bot_log.app_session_log
                   (event_type, app_user_id, session_id, source_ip, reason)
               SELECT %s, user_id, id, %s::inet, %s
               FROM ended
               WHERE expires_at > now()""",
            (user_id, digest, event_type, source_ip, reason),
        )
        return result.rowcount

    def _revoke_all_sessions(self, connection, reason):
        # Lock the control row against login before deleting any sessions.
        result = connection.execute(
            "UPDATE bot_auth.app_control SET session_generation = session_generation + 1 WHERE singleton"
        )
        if result.rowcount != 1:
            raise psycopg.DatabaseError("The app control row is missing")
        return self._end_sessions(connection, "revoked", reason=reason)

    def revoke_all_sessions(self):
        with self.connect() as connection:
            return self._revoke_all_sessions(connection, "admin_revoke_all")

    def set_maintenance(self, enabled):
        with self.connect() as connection:
            result = connection.execute(
                "UPDATE bot_auth.app_control SET maintenance = %s WHERE singleton",
                (enabled,),
            )
            if result.rowcount != 1:
                raise psycopg.DatabaseError("The app control row is missing")
            return self._revoke_all_sessions(connection, "maintenance") if enabled else 0

    def login(self, username, password, source_ip=None):
        with self.connect() as connection:
            maintenance, _ = self.read_control(connection, lock=True)
            if maintenance:
                raise MaintenanceActive()
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
                """WITH started AS (
                       INSERT INTO bot_auth.app_sessions (user_id, token_hash, expires_at)
                       VALUES (%s, %s, clock_timestamp() + interval '1 hour')
                       RETURNING id, user_id
                   )
                   INSERT INTO bot_log.app_session_log
                       (event_type, app_user_id, session_id, source_ip)
                   SELECT 'login', user_id, id, %s::inet FROM started""",
                (row[0], token_hash(token), source_ip),
            )
            return token, row[0]

    def insert_api_request_logs(self, rows):
        with psycopg.connect(
            **(self.settings | {"connect_timeout": 3}), options="-c statement_timeout=3000",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO bot_log.api_request_log
                       (requested_at, request_id, method, route, status_code, duration_ms, app_user_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    rows,
                )

    def session(self, token):
        with self.connect() as connection:
            row = connection.execute(
                """SELECT user_id, expires_at FROM bot_auth.app_sessions
                   WHERE token_hash = %s AND expires_at > clock_timestamp()""",
                (token_hash(token),),
            ).fetchone()
            return row

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

    def logout(self, token, source_ip=None):
        with self.connect() as connection:
            self._end_sessions(connection, "logout", digest=token_hash(token), source_ip=source_ip)

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
            self._end_sessions(connection, "revoked", user_id=row[0], reason="password_reset")
            return True
