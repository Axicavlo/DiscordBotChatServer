BEGIN;

CREATE SCHEMA gomoku;

CREATE TABLE gomoku.game_mode (
    id smallint PRIMARY KEY,
    name text NOT NULL UNIQUE
);

INSERT INTO gomoku.game_mode (id, name)
VALUES (1, '일반'), (2, '블리츠');

CREATE TABLE gomoku.record (
    room_id uuid PRIMARY KEY,
    player_1 text NOT NULL,
    player_2 text NOT NULL,
    win text,
    is_draw boolean NOT NULL,
    game_mode smallint NOT NULL REFERENCES gomoku.game_mode (id),
    CHECK (player_1 ~ '^[0-9]{1,20}$' AND player_2 ~ '^[0-9]{1,20}$'),
    CHECK (player_1 <> player_2),
    CHECK (
        (is_draw AND win IS NULL)
        OR (NOT is_draw AND win IS NOT NULL AND win IN (player_1, player_2))
    )
);

CREATE INDEX gomoku_record_player_1_mode_idx ON gomoku.record (player_1, game_mode);
CREATE INDEX gomoku_record_player_2_mode_idx ON gomoku.record (player_2, game_mode);

COMMIT;
