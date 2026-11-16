BEGIN;

LOCK TABLE bot_auth.app_users, bot_auth.app_sessions,
    bot_log.api_request_log, bot_log.app_session_log IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM bot_log.api_request_log AS log
        WHERE log.app_user_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM bot_auth.app_users AS account WHERE account.id = log.app_user_id
        )
    ) OR EXISTS (
        SELECT 1 FROM bot_log.app_session_log AS log
        WHERE NOT EXISTS (
            SELECT 1 FROM bot_auth.app_users AS account WHERE account.id = log.app_user_id
        )
    ) THEN
        RAISE EXCEPTION 'Account ID migration stopped: log entries refer to missing accounts';
    END IF;
END;
$$;

ALTER TABLE bot_auth.app_sessions DROP CONSTRAINT app_sessions_user_id_fkey;

UPDATE bot_auth.app_sessions AS session
SET user_id = account.user_code::bigint
FROM bot_auth.app_users AS account
WHERE session.user_id = account.id;

UPDATE bot_log.api_request_log AS log
SET app_user_id = account.user_code::bigint
FROM bot_auth.app_users AS account
WHERE log.app_user_id = account.id;

UPDATE bot_log.app_session_log AS log
SET app_user_id = account.user_code::bigint
FROM bot_auth.app_users AS account
WHERE log.app_user_id = account.id;

ALTER TABLE bot_auth.app_users ALTER COLUMN id DROP IDENTITY;
UPDATE bot_auth.app_users SET id = user_code::bigint;

ALTER TABLE bot_auth.app_users
    DROP COLUMN user_code,
    ADD CONSTRAINT app_users_id_format CHECK (
        id BETWEEN 100000000 AND 999999999
        AND (id / 1000) % 100 BETWEEN 1 AND 12
        AND id % 1000 BETWEEN 1 AND 999
    );

ALTER TABLE bot_auth.app_sessions
    ADD CONSTRAINT app_sessions_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES bot_auth.app_users(id) ON DELETE CASCADE;

CREATE OR REPLACE FUNCTION bot_auth.assign_app_user_code()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    creation_month text;
    next_number integer;
BEGIN
    creation_month := to_char(NEW.created_at AT TIME ZONE 'Asia/Seoul', 'YYYYMM');

    INSERT INTO bot_auth.app_user_monthly_counters AS counter (month_key, last_number)
    VALUES (creation_month, 1)
    ON CONFLICT (month_key) DO UPDATE
        SET last_number = counter.last_number + 1
        WHERE counter.last_number < 999
    RETURNING last_number INTO next_number;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0999', MESSAGE = 'Monthly user code limit reached';
    END IF;

    NEW.id := (creation_month || to_char(next_number, 'FM000'))::bigint;
    RETURN NEW;
END;
$$;

COMMIT;
