"""HTTP admission and WebSocket transport for the Gomoku room service."""

import asyncio
from collections import defaultdict, deque
from dataclasses import asdict
import logging
from secrets import token_urlsafe

from aiohttp import WSMsgType, web

from chatbot_core.gomoku_game import RoomError, SECOND, TIME_OPTIONS

logger = logging.getLogger(__name__)
ROOM_PATH = r"/gomoku/{room_id:[0-9a-f-]{36}}"
COOKIE = "gomoku_session"
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class GomokuWeb:
    def __init__(self, rooms, discord, store):
        self.rooms = rooms
        self.discord = discord
        self.store = store
        self.sessions = defaultdict(dict)
        self.sockets = defaultdict(dict)
        self.messages = defaultdict(lambda: deque(maxlen=100))
        self.standings = {}
        self.card_states = {}
        self.saved = set()
        self.last_chat = {}
        self.spectator_disconnects = {}
        self.jobs = {}

    def register(self, app):
        app.router.add_post(ROOM_PATH + "/admit", self.admit)
        app.router.add_get(ROOM_PATH + "/events", self.events, allow_head=False)
        app.cleanup_ctx.append(self.lifecycle)
        app.on_shutdown.append(self.shutdown)

    def _origin(self, request):
        if request.headers.get("Origin") != self.discord.public_url:
            raise web.HTTPForbidden(headers=HEADERS)

    def _room(self, request):
        room = self.rooms.rooms.get(request.match_info["room_id"])
        if room is None:
            raise web.HTTPNotFound(headers=HEADERS)
        room.tick(self.rooms.clock())
        if room.phase == "closed":
            raise web.HTTPGone(headers=HEADERS)
        return room

    async def admit(self, request):
        self._origin(request)
        room = self._room(request)
        try:
            data = await request.json()
            ticket = data["ticket"]
            if not isinstance(ticket, str) or len(ticket) != 43:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise web.HTTPBadRequest(headers=HEADERS)
        try:
            _, member = self.rooms.redeem(room.id, ticket)
        except RoomError as error:
            return web.json_response({"error": str(error)}, status=403, headers=HEADERS)

        sessions = self.sessions[room.id]
        for token, member_id in tuple(sessions.items()):
            if member_id == member.id:
                del sessions[token]
        session = token_urlsafe(32)
        sessions[session] = member.id
        response = web.json_response({"member": member.id}, headers=HEADERS)
        response.set_cookie(COOKIE, session, path=f"/gomoku/{room.id}",
                            secure=True, httponly=True, samesite="Strict")
        return response

    def _snapshot(self, room, now):
        players = []
        for player in room.players.values():
            data = asdict(player)
            data["ready"] = player.id in room.ready
            data["preference"] = room.preferences.get(player.id)
            data["color"] = next((color for color, member in room.colors.items() if member == player.id), None)
            data["present"] = any(member_id == player.id for member_id, _ in self.sockets[room.id].values())
            stats = self.standings.get((room.id, room.mode, player.id))
            data["standing"] = {**asdict(stats), "losses": stats.losses} if stats else None
            players.append(data)
        return {
            "type": "room",
            "phase": room.phase,
            "host": room.host.id,
            "mode": room.mode,
            "seconds": room.seconds,
            "time_options": TIME_OPTIONS,
            "players": players,
            "spectators": [asdict(member) for member in room.spectators.values()],
            "board": room.board,
            "turn": room.active_player if room.phase == "playing" else None,
            "clocks": {key: ns / 1_000_000 for key, ns in room.clocks(now).items()},
            "result": asdict(room.result) if room.result else None,
            "messages": list(self.messages[room.id]),
        }

    async def _broadcast(self, room):
        payload = self._snapshot(room, self.rooms.clock())
        connections = tuple(self.sockets[room.id].items())
        if not connections:
            return
        results = await asyncio.gather(
            *(socket.send_json(payload) for socket, _ in connections),
            return_exceptions=True,
        )
        for (socket, _), result in zip(connections, results):
            if isinstance(result, Exception):
                await socket.close()

    async def events(self, request):
        self._origin(request)
        room = self._room(request)
        token = request.cookies.get(COOKIE)
        member_id = self.sessions.get(room.id, {}).get(token)
        if member_id is None or member_id in room.departed:
            raise web.HTTPUnauthorized(headers=HEADERS)
        socket = web.WebSocketResponse(heartbeat=15, timeout=2, max_msg_size=8192)
        socket.headers.update(HEADERS)
        await socket.prepare(request)
        self.spectator_disconnects.pop((room.id, member_id), None)
        self.sockets[room.id][socket] = (member_id, token)
        try:
            await socket.send_json({"type": "identity", "member": member_id})
            await self._broadcast(room)
            async for message in socket:
                if message.type != WSMsgType.TEXT:
                    continue
                received_at = self.rooms.clock()
                if self.sessions.get(room.id, {}).get(token) != member_id:
                    break
                try:
                    data = message.json()
                    if not isinstance(data, dict):
                        raise ValueError
                    reply = self._act(room, member_id, data, received_at)
                except RoomError as error:
                    await socket.send_json({"type": "error", "message": str(error)})
                except (KeyError, ValueError, TypeError):
                    await socket.send_json({"type": "error", "message": "잘못된 요청입니다."})
                else:
                    if reply:
                        await socket.send_json(reply)
                    await self._broadcast(room)
                    if data.get("type") == "leave":
                        break
        finally:
            self.sockets[room.id].pop(socket, None)
            if member_id in room.spectators and not any(
                connected == member_id for connected, _ in self.sockets[room.id].values()
            ):
                self.spectator_disconnects[(room.id, member_id)] = self.rooms.clock() + 3 * SECOND
            if room.id in self.rooms.rooms:
                await self._broadcast(room)
        return socket

    def _act(self, room, member_id, data, now):
        room.tick(now)
        action = data.get("type")
        if room.phase == "closed" or member_id in room.departed:
            raise RoomError("종료된 방입니다.")
        if action == "preview":
            return {"type": "preview", "index": data["index"],
                    "status": room.preview(member_id, data["index"], now)}
        if action == "move":
            return {"type": "move", "index": data["index"],
                    "status": room.move(member_id, data["index"], now)}
        if action == "configure":
            room.configure(member_id, data["mode"], data["seconds"])
        elif action == "color":
            room.select_color(member_id, data["color"])
        elif action == "ready":
            room.toggle_ready(member_id, now)
        elif action == "close":
            room.close_waiting(member_id, now)
        elif action == "leave":
            room.leave(member_id, now)
            sessions = self.sessions[room.id]
            for token, owner in tuple(sessions.items()):
                if owner == member_id:
                    del sessions[token]
        elif action == "chat":
            self._chat(room, member_id, data["text"], now)
        else:
            raise RoomError("지원하지 않는 요청입니다.")
        return None

    def _chat(self, room, member_id, text, now):
        member = room.players.get(member_id) or room.spectators.get(member_id)
        if not member or not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise RoomError("메시지는 1~1,000자로 입력하세요.")
        key = (room.id, member_id)
        if now - self.last_chat.get(key, -SECOND) < SECOND // 2:
            raise RoomError("잠시 후에 메시지를 보내세요.")
        self.last_chat[key] = now
        self.messages[room.id].append({"member": asdict(member), "text": text.strip()})

    def _spawn(self, key, operation, *args):
        if key not in self.jobs:
            self.jobs[key] = asyncio.create_task(operation(*args))

    async def _save(self, room):
        await asyncio.to_thread(self.store.record, room.result)
        self.saved.add(room.id)
        for member_id in room.players:
            self.standings.pop((room.id, room.mode, member_id), None)

    async def _load_standing(self, key):
        _, mode, member_id = key
        self.standings[key] = await asyncio.to_thread(self.store.standing, member_id, mode)

    def _maintain(self):
        self.rooms.tick()
        now = self.rooms.clock()
        for key, job in tuple(self.jobs.items()):
            if job.done():
                del self.jobs[key]
                if job.exception():
                    logger.warning("Gomoku background job failed: %s", type(job.exception()).__name__)
        for (room_id, member), deadline in tuple(self.spectator_disconnects.items()):
            if now >= deadline:
                del self.spectator_disconnects[room_id, member]
                room = self.rooms.rooms.get(room_id)
                if room is not None:
                    room.spectators.pop(member, None)
                    for token, owner in tuple(self.sessions[room_id].items()):
                        if owner == member:
                            del self.sessions[room_id][token]
        for room in tuple(self.rooms.rooms.values()):
            self._maintain_room(room)

    def _maintain_room(self, room):
        fingerprint = (room.phase, room.mode, tuple(room.players.items()), len(room.spectators))
        card_job = ("card", room.id)
        if fingerprint != self.card_states.get(room.id) and card_job not in self.jobs:
            self.card_states[room.id] = fingerprint
            self._spawn(card_job, self.discord.refresh, room)
        if room.result is not None and room.id not in self.saved:
            self._spawn(("result", room.id), self._save, room)
        if room.phase != "closed":
            for member in room.players:
                key = (room.id, room.mode, member)
                if key not in self.standings:
                    self._spawn(key, self._load_standing, key)
        for socket, (member, token) in tuple(self.sockets[room.id].items()):
            if room.phase == "closed" or self.sessions[room.id].get(token) != member:
                self._spawn(("close", socket), self._close_socket, room, socket)
        if self.sockets[room.id]:
            self._spawn(("state", room.id), self._broadcast, room)
        elif (room.phase == "closed" and room.id not in self.discord.cards
              and (room.result is None or room.id in self.saved)
              and not any(room.id in key for key in self.jobs)):
            self._forget(room.id)

    async def _close_socket(self, room, socket):
        if room.phase == "closed":
            await socket.send_json({"type": "closed"})
        await socket.close()

    def _forget(self, room_id):
        self.rooms.rooms.pop(room_id, None)
        self.saved.discard(room_id)
        self.sessions.pop(room_id, None)
        self.sockets.pop(room_id, None)
        self.messages.pop(room_id, None)
        self.card_states.pop(room_id, None)
        self.standings = {key: value for key, value in self.standings.items() if key[0] != room_id}
        self.last_chat = {key: value for key, value in self.last_chat.items() if key[0] != room_id}

    async def lifecycle(self, app):
        async def run():
            while True:
                self._maintain()
                await asyncio.sleep(0.25)

        task = asyncio.create_task(run())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            for job in self.jobs.values():
                job.cancel()
            await asyncio.gather(*self.jobs.values(), return_exceptions=True)
            for room in self.rooms.rooms.values():
                if room.result is not None and room.id not in self.saved:
                    try:
                        await self._save(room)
                    except Exception as error:
                        logger.warning("Could not save Gomoku result during shutdown: %s", type(error).__name__)

    async def shutdown(self, app):
        await asyncio.gather(
            *(socket.close(code=1001) for connections in self.sockets.values() for socket in connections),
            return_exceptions=True,
        )
