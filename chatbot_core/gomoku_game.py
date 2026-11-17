"""In-memory Gomoku rooms. Mutations run synchronously on the server event loop."""

from dataclasses import dataclass, field
from secrets import choice, token_urlsafe
from time import monotonic_ns
from uuid import uuid4

from .gomoku_rules import BLACK, EMPTY_BOARD, WHITE, has_legal_move, judge, play

SECOND = 1_000_000_000
NORMAL, BLITZ = 1, 2
TIME_OPTIONS = {NORMAL: (15, 30, 45, 60), BLITZ: (60, 120, 180, 240, 300)}
DEFAULT_TIME = {NORMAL: 30, BLITZ: 180}


class RoomError(ValueError):
    """A rejected room operation suitable for display to callers."""


@dataclass(frozen=True)
class Member:
    id: str
    name: str
    avatar: str = ""


@dataclass(frozen=True)
class Result:
    room_id: str
    player_1: str
    player_2: str
    winner: str | None
    mode: int
    reason: str


@dataclass
class Room:
    guild_id: str
    host: Member
    created_at: int
    id: str = field(default_factory=lambda: str(uuid4()))
    phase: str = "waiting"
    mode: int = NORMAL
    seconds: int = 30
    players: dict[str, Member] = field(init=False)
    spectators: dict[str, Member] = field(default_factory=dict)
    preferences: dict[str, int] = field(default_factory=dict)
    ready: set[str] = field(default_factory=set)
    departed: set[str] = field(default_factory=set)
    colors: dict[int, str] = field(default_factory=dict)
    remaining: dict[str, int] = field(default_factory=dict)
    board: tuple[int, ...] = EMPTY_BOARD
    turn: int = BLACK
    deadline: int = 0
    ended_at: int = 0
    result: Result | None = None

    def __post_init__(self):
        self.players = {self.host.id: self.host}

    @property
    def active_player(self):
        return self.colors[self.turn]

    def configure(self, member_id, mode, seconds):
        if self.phase != "waiting" or member_id != self.host.id:
            raise RoomError("방장만 대기실 설정을 변경할 수 있습니다.")
        if type(mode) is not int or mode not in TIME_OPTIONS:
            raise RoomError("지원하지 않는 모드입니다.")
        if type(seconds) is not int or seconds not in TIME_OPTIONS[mode]:
            raise RoomError("지원하지 않는 제한시간입니다.")
        if (mode, seconds) != (self.mode, self.seconds):
            self.mode, self.seconds = mode, seconds
            self.ready.clear()

    def select_color(self, member_id, color):
        self._require_waiting_player(member_id)
        if type(color) is not int or color not in (BLACK, WHITE):
            raise RoomError("흑 또는 백을 선택하세요.")
        if self.preferences.get(member_id) != color:
            self.preferences[member_id] = color
            self.ready.discard(member_id)

    def _require_waiting_player(self, member_id):
        if self.phase != "waiting" or member_id not in self.players:
            raise RoomError("대기 중인 대국자만 사용할 수 있습니다.")

    def toggle_ready(self, member_id, now):
        self.tick(now)
        self._require_waiting_player(member_id)
        if member_id in self.ready:
            self.ready.remove(member_id)
            return
        if member_id not in self.preferences:
            raise RoomError("흑 또는 백을 먼저 선택하세요.")
        self.ready.add(member_id)
        if len(self.ready) == 2:
            first, second = self.players
            p1, p2 = self.preferences[first], self.preferences[second]
            black = choice((first, second)) if p1 == p2 else first if p1 == BLACK else second
            self.colors = {BLACK: black, WHITE: second if black == first else first}
            self.remaining = dict.fromkeys(self.players, self.seconds * SECOND)
            self.deadline = now + self.remaining[black]
            self.phase = "playing"

    def preview(self, member_id, index, now):
        self.tick(now)
        if self.phase != "playing" or member_id != self.active_player:
            raise RoomError("현재 차례가 아닙니다.")
        try:
            return judge(self.board, index, self.turn)
        except ValueError as error:
            raise RoomError("유효하지 않은 교차점입니다.") from error

    def move(self, member_id, index, now):
        status = self.preview(member_id, index, now)
        if status not in ("legal", "win"):
            return status
        self.board, _ = play(self.board, index, self.turn)
        self.remaining[member_id] = self.deadline - now
        if status == "win":
            self._finish(member_id, "five", now)
        else:
            self.turn = WHITE if self.turn == BLACK else BLACK
            if not has_legal_move(self.board, self.turn):
                self._finish(None, "no-legal-move", now)
            else:
                if self.mode == NORMAL:
                    self.remaining[self.active_player] = self.seconds * SECOND
                self.deadline = now + self.remaining[self.active_player]
        return status

    def clocks(self, now):
        remaining = self.remaining.copy()
        if self.phase == "playing":
            remaining[self.active_player] = max(0, self.deadline - now)
        return remaining

    def tick(self, now):
        if self.phase == "waiting" and now >= self.created_at + 300 * SECOND:
            self.phase = "closed"
        elif self.phase == "playing" and now >= self.deadline:
            loser = self.active_player
            self.remaining[loser] = 0
            winner = next(member_id for member_id in self.players if member_id != loser)
            self._finish(winner, "timeout", self.deadline)
        if self.phase == "finished" and now >= self.ended_at + 60 * SECOND:
            self.phase = "closed"

    def _finish(self, winner, reason, now):
        self.phase, self.ended_at = "finished", now
        first, second = self.players
        self.result = Result(self.id, first, second, winner, self.mode, reason)

    def close_waiting(self, member_id, now):
        self.tick(now)
        if self.phase != "waiting" or member_id != self.host.id:
            raise RoomError("방장만 대기실을 종료할 수 있습니다.")
        self.phase = "closed"

    def leave(self, member_id, now):
        self.tick(now)
        self.spectators.pop(member_id, None)
        if member_id not in self.players:
            return
        if self.phase == "waiting":
            if member_id == self.host.id:
                self.phase = "closed"
            else:
                self.players.pop(member_id)
                self.ready.discard(member_id)
                self.preferences.pop(member_id, None)
        elif self.phase in ("playing", "finished"):
            self.departed.add(member_id)
            if self.phase == "playing":
                active = self.active_player
                self.remaining[active] = self.deadline - now
                winner = next(player for player in self.players if player != member_id)
                self._finish(winner, "resignation", now)
            if self.departed == self.players.keys():
                self.phase = "closed"


class Rooms:
    """Owns room capacity and consumes Discord-issued tickets atomically."""

    def __init__(self, clock=monotonic_ns):
        self.clock = clock
        self.rooms = {}
        self.tickets = {}

    def create(self, guild_id, host):
        now = self.clock()
        self._check_player_room(host.id, now)
        room = Room(guild_id, host, now)
        self.rooms[room.id] = room
        return room

    def _check_player_room(self, member_id, now, allowed_room=None):
        for room in self.rooms.values():
            room.tick(now)
            if room is not allowed_room and room.phase in ("waiting", "playing") and member_id in room.players:
                raise RoomError("이미 다른 방의 대국에 참가 중입니다.")

    def _check_entry(self, room, guild_id, member_id, role, now):
        room.tick(now)
        if room.guild_id != guild_id or room.phase == "closed" or member_id in room.departed:
            raise RoomError("이 방에 입장할 수 없습니다.")
        in_room = member_id in room.players or member_id in room.spectators
        if room.phase == "finished" and not in_room:
            raise RoomError("대국이 종료되었습니다.")
        if role == "player" and member_id not in room.players:
            if room.phase != "waiting" or len(room.players) >= 2:
                raise RoomError("대국자 자리가 가득 찼습니다.")
            self._check_player_room(member_id, now, allowed_room=room)
        if role == "spectator" and not in_room and len(room.spectators) >= 10:
            raise RoomError("관전자가 가득 찼습니다.")

    def issue_ticket(self, room_id, guild_id, member, role):
        now = self.clock()
        room = self.rooms.get(room_id)
        if room is None or role not in ("player", "spectator"):
            raise RoomError("이 방에 입장할 수 없습니다.")
        self._check_entry(room, guild_id, member.id, role, now)
        token = token_urlsafe(32)
        self.tickets[token] = (now + 30 * SECOND, room_id, guild_id, member, role)
        return token

    def redeem(self, room_id, token):
        now = self.clock()
        ticket = self.tickets.get(token)
        if ticket is None or ticket[0] <= now or ticket[1] != room_id:
            raise RoomError("입장권이 만료되었거나 유효하지 않습니다.")
        _, _, guild_id, member, role = self.tickets.pop(token)
        room = self.rooms.get(room_id)
        if room is None:
            raise RoomError("종료된 방입니다.")
        self._check_entry(room, guild_id, member.id, role, now)
        if role == "player":
            room.spectators.pop(member.id, None)
            room.players[member.id] = member
        elif member.id not in room.players:
            room.spectators[member.id] = member
        return room, member

    def tick(self):
        now = self.clock()
        for room in self.rooms.values():
            room.tick(now)
        self.tickets = {token: ticket for token, ticket in self.tickets.items() if ticket[0] > now}
