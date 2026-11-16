BEGIN;

CREATE TABLE IF NOT EXISTS bot_log.app_session_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    event_type text NOT NULL CHECK (event_type IN ('login', 'logout', 'revoked')),
    app_user_id bigint NOT NULL,
    session_id bigint NOT NULL,
    source_ip inet,
    reason text,
    CHECK (
        (event_type = 'revoked' AND reason IS NOT NULL
         AND reason IN ('admin_revoke_all', 'maintenance', 'password_reset'))
        OR (event_type IN ('login', 'logout') AND reason IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS app_session_log_occurred_at_idx
    ON bot_log.app_session_log (occurred_at DESC);
CREATE INDEX IF NOT EXISTS app_session_log_user_occurred_at_idx
    ON bot_log.app_session_log (app_user_id, occurred_at DESC);

GRANT USAGE ON SCHEMA bot_log TO discordbot_app;
GRANT INSERT ON bot_log.app_session_log TO discordbot_app;

COMMIT;
