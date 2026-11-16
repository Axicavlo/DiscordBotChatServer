BEGIN;

ALTER TABLE bot_auth.app_users ADD COLUMN user_code text;

WITH ranked AS (
    SELECT id,
           to_char(created_at AT TIME ZONE 'Asia/Seoul', 'YYYYMM') AS month_key,
           row_number() OVER (
               PARTITION BY date_trunc('month', created_at AT TIME ZONE 'Asia/Seoul')
               ORDER BY created_at, id
           ) AS month_number
    FROM bot_auth.app_users
)
UPDATE bot_auth.app_users AS app_user
SET user_code = ranked.month_key || CASE WHEN ranked.month_number <= 999 THEN to_char(ranked.month_number, 'FM000') END
FROM ranked
WHERE app_user.id = ranked.id;

ALTER TABLE bot_auth.app_users
    ALTER COLUMN user_code SET NOT NULL,
    ADD CONSTRAINT app_users_user_code_format CHECK (user_code ~ '^[0-9]{6}[0-9]{3}$' AND right(user_code, 3) <> '000'),
    ADD CONSTRAINT app_users_user_code_key UNIQUE (user_code);

CREATE TABLE bot_auth.app_user_monthly_counters (
    month_key text PRIMARY KEY,
    last_number integer NOT NULL CHECK (last_number BETWEEN 1 AND 999)
);

INSERT INTO bot_auth.app_user_monthly_counters (month_key, last_number)
SELECT left(user_code, 6), max(right(user_code, 3)::integer)
FROM bot_auth.app_users
GROUP BY left(user_code, 6);

CREATE FUNCTION bot_auth.assign_app_user_code()
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

    NEW.user_code := creation_month || to_char(next_number, 'FM000');
    RETURN NEW;
END;
$$;

CREATE TRIGGER app_users_assign_user_code
BEFORE INSERT ON bot_auth.app_users
FOR EACH ROW
EXECUTE FUNCTION bot_auth.assign_app_user_code();

GRANT SELECT, INSERT, UPDATE ON bot_auth.app_user_monthly_counters TO discordbot_app;
GRANT SELECT (user_code) ON bot_auth.app_users TO discordbot_app;

COMMIT;
