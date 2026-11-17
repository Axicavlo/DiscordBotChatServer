import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

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
            public_url=self.origin, cards={}, refresh=AsyncMock(), emojis_for=lambda *_: [],
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
