"""PostgreSQL persistence for completed Gomoku games."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Standing:
    games: int
    wins: int
    draws: int

    @property
    def losses(self):
        return self.games - self.wins - self.draws


class GomokuStore:
    def __init__(self, connect):
        self.connect = connect

    def record(self, result):
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO gomoku.record
                    (room_id, player_1, player_2, win, is_draw, game_mode)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (room_id) DO NOTHING
                """,
                (result.room_id, result.player_1, result.player_2,
                 result.winner, result.winner is None, result.mode),
            )

    def standing(self, member_id, mode):
        with self.connect() as connection:
            counts = connection.execute(
                """
                SELECT count(*), count(*) FILTER (WHERE win = %s),
                       count(*) FILTER (WHERE is_draw)
                FROM gomoku.record
                WHERE game_mode = %s AND (player_1 = %s OR player_2 = %s)
                """,
                (member_id, mode, member_id, member_id),
            ).fetchone()
        return Standing(*counts)
