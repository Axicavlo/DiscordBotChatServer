import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from chatbot_core.gomoku_discord import GomokuDiscord
from chatbot_core.gomoku_game import Member, RoomError, Rooms
from gomoku_store import Standing
from gomoku_web import COOKIE, DEPLOY_STATUS_PATH, GomokuWeb
from server_api import create_api


class GomokuTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.draining = False
        self.rooms = Rooms(draining=lambda: self.draining)
        self.host = Member("11", "Host")
        self.room = self.rooms.create("123", self.host)
        self.origin = "https://example.test"
        discord = SimpleNamespace(
            public_url=self.origin, activity_id='', activity_origin='', cards={}, refresh=AsyncMock(), emojis_for=lambda *_: [],
        )
        store = SimpleNamespace(standing=lambda *_: Standing(0, 0, 0))
        gomoku = GomokuWeb(self.rooms, discord, store)
        bridge = SimpleNamespace(is_ready=lambda: True)
        self.client = TestClient(TestServer(create_api(bridge, "legacy-key", gomoku=gomoku)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.path = f"/gomoku/{self.room.id}"

    async def test_deployment_gate_preserves_existing_room(self):
        self.draining = True
        with self.assertRaises(RoomError):
            self.rooms.create("123", Member("22", "Guest"))
        response = await self.client.get(DEPLOY_STATUS_PATH)
        self.assertEqual(await response.json(), {"ready": True, "draining": True, "rooms": 1})
        self.room.close_waiting(self.host.id, self.rooms.clock())
        response = await self.client.get(DEPLOY_STATUS_PATH)
        self.assertEqual((await response.json())["rooms"], 0)

    async def test_ticket_and_room_websocket(self):
        ticket = self.rooms.issue_ticket(self.room.id, "123", self.host, "player")
        headers = {"Origin": self.origin}
        payload = {"ticket": ticket}
        response = await self.client.post(self.path + "/admit", headers=headers, json=payload)
        self.assertEqual(response.status, 200)
        cookie = response.cookies[COOKIE]
        self.assertTrue(cookie["secure"])
        replay = await self.client.post(self.path + "/admit", headers=headers, json=payload)
        self.assertEqual(replay.status, 403)
        headers["Cookie"] = f"{COOKIE}={cookie.value}"
        async with self.client.ws_connect(self.path + "/events", headers=headers) as socket:
            self.assertEqual(await socket.receive_json(), {"type": "identity", "member": self.host.id})
            state = await socket.receive_json()
            self.assertEqual(state["phase"], "waiting")
            self.assertIsNone(state["last_move"])

    async def test_asset_and_legacy_auth(self):
        response = await self.client.get("/gomoku/assets/room.js")
        self.assertEqual(response.status, 200)
        response = await self.client.get("/api/v1/guilds")
        self.assertEqual(response.status, 401)


class GomokuActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_button_and_oauth_admit_without_web_message(self):
        from contextlib import asynccontextmanager

        rooms = Rooms()
        room = rooms.create("123", Member("11", "Host"))
        discord = GomokuDiscord(rooms, "https://example.test", "456", "test-secret")
        launch = AsyncMock(return_value=SimpleNamespace(activity_id="instance", resource=None))
        send = AsyncMock()
        user = SimpleNamespace(id=22, display_name="Guest", display_avatar=SimpleNamespace(url=""))
        await discord.enter(SimpleNamespace(
            guild_id=123, user=user, response=SimpleNamespace(launch_activity=launch, send_message=send),
        ), room, "spectator")
        launch.assert_awaited_once()
        send.assert_not_awaited()

        @asynccontextmanager
        async def response(payload):
            yield SimpleNamespace(status=200, json=AsyncMock(return_value=payload))

        @asynccontextmanager
        async def oauth_client(**kwargs):
            yield SimpleNamespace(
                post=lambda *args, **kwargs: response({"access_token": "token"}),
                get=lambda *args, **kwargs: response({"id": "22"}),
            )

        store = SimpleNamespace(standing=lambda *_: Standing(0, 0, 0))
        app = create_api(SimpleNamespace(is_ready=lambda: True), "legacy-key",
                         gomoku=GomokuWeb(rooms, discord, store))
        async with TestClient(TestServer(app)) as client:
            headers = {"Origin": discord.activity_origin}
            payload = {"code": "code", "instance": "instance"}
            self.assertEqual((await client.get("/gomoku/activity")).status, 200)
            self.assertEqual((await client.get("/gomoku/activity.js")).status, 200)
            self.assertEqual((await client.post("/gomoku/activity/admit", json=payload)).status, 403)
            with patch("gomoku_web.ClientSession", oauth_client):
                entry = await client.post("/gomoku/activity/admit", json=payload, headers=headers)
                self.assertEqual(entry.status, 200)
                data = await entry.json()
                self.assertEqual(data["room"], room.id)
                replay = await client.post("/gomoku/activity/admit", json=payload, headers=headers)
                self.assertEqual(replay.status, 403)
            admit = await client.post(f"/gomoku/{room.id}/admit", headers=headers,
                                      json={"ticket": data["ticket"]})
            self.assertEqual(admit.status, 200)
            self.assertIn("Partitioned", admit.headers["Set-Cookie"])
            self.assertIn("22", room.spectators)
