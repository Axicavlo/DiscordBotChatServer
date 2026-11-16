BEGIN;

CREATE TABLE IF NOT EXISTS bot_auth.app_control (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    maintenance boolean NOT NULL DEFAULT false,
    session_generation bigint NOT NULL DEFAULT 0
);

INSERT INTO bot_auth.app_control (singleton)
VALUES (true)
ON CONFLICT (singleton) DO NOTHING;

COMMIT;
