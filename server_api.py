"""Authenticated chat API and event stream served on loopback."""

import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timezone
from ipaddress import ip_address
from math import ceil
from time import monotonic
from hmac import compare_digest
import logging
import re
from uuid import uuid4

import discord
from aiohttp import web
from psycopg import Error as DatabaseError

from account_store import MaintenanceActive
from chatbot_core import asset_url, avatar_url, message_view, server_display_name


logger = logging.getLogger(__name__)


def user_row(bridge, user, guild):
    return {"id": str(user.id), "name": getattr(user, "name", ""),
            "display_name": server_display_name(user, guild, bridge.member_names)}


def message_row(bridge, message):
    guild = message.guild
    names = bridge.member_names
    return {
        "id": str(message.id),
        "created_at": message.created_at.isoformat(),
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "author_name": server_display_name(message.author, guild, names),
        "author": user_row(bridge, message.author, guild),
        "mentions": [user_row(bridge, user, guild) for user in message.mentions],
        "content": message.content,
        "avatar": avatar_url(message.author, guild, bridge.member_avatars),
        "clean_content": message.clean_content,
        "own": message.author.id == bridge.user.id,
        "visible": message_view(message, names, visible=True),
        "hidden": message_view(message, names, visible=False),
    }


class RequestLimits:
    window = 60
    limits = {"read": 120, "write": 30, "stream": 10}

    def __init__(self):
        self.requests = OrderedDict()

    def check(self, identity, category):
        now = monotonic()
        cutoff = now - self.window
        while self.requests:
            oldest = next(iter(self.requests.values()))
            if oldest[-1] > cutoff:
                break
            self.requests.popitem(last=False)

        bucket = (identity, category)
        accepted = self.requests.setdefault(bucket, deque())
        while accepted and accepted[0] <= cutoff:
            accepted.popleft()
        if len(accepted) >= self.limits[category]:
            retry_after = ceil(accepted[0] + self.window - now)
            raise web.HTTPTooManyRequests(headers={
                "Retry-After": str(max(1, retry_after)), "Cache-Control": "no-store",
            })

        accepted.append(now)
        self.requests.move_to_end(bucket)


def client_ip(request):
    peer = request.remote
    if peer in ("127.0.0.1", "::1"):
        # Caddy is the local proxy. The rightmost address is its immediate client.
        forwarded = request.headers.get("X-Forwarded-For", "").rsplit(",", 1)[-1].strip()
        try:
            return str(ip_address(forwarded))
        except ValueError:
            pass
    return peer


class ApiRequestLog:
    def __init__(self, accounts):
        self.accounts = accounts
        self.queue = asyncio.Queue(maxsize=1024)
        self.rejected = 0
        self.dropped = 0

    @web.middleware
    async def record(self, request, handler):
        requested_at = datetime.now(timezone.utc)
        started = monotonic()
        request_id = uuid4()
        status = None
        try:
            response = await handler(request)
            status = response.status
            return response
        except web.HTTPException as error:
            status = error.status
            raise
        except Exception:
            status = 500
            raise
        finally:
            status = request.get("upgraded_status", status)
            if status == 429:
                self.rejected += 1
            elif status is not None:
                resource = request.match_info.route.resource
                route = resource.canonical if resource is not None else "<unmatched>"
                try:
                    self.queue.put_nowait((
                        requested_at, request_id, request.method, route, status,
                        int((monotonic() - started) * 1000), request.get("app_user_id"),
                    ))
                except asyncio.QueueFull:
                    self.dropped += 1

    def report_counts(self):
        if self.rejected or self.dropped:
            logger.warning(
                "API request log counts: HTTP 429=%d, dropped=%d",
                self.rejected, self.dropped,
            )
            self.rejected = self.dropped = 0

    async def write(self):
        report_at = monotonic() + 10
        stopping = False
        while not stopping:
            try:
                row = await asyncio.wait_for(self.queue.get(), timeout=10)
            except TimeoutError:
                row = ()
            batch = []
            while row is not None and row:
                batch.append(row)
                if self.queue.empty() or len(batch) >= 128:
                    break
                row = self.queue.get_nowait()
            stopping = row is None
            if batch:
                try:
                    await asyncio.to_thread(self.accounts.insert_api_request_logs, batch)
                except DatabaseError:
                    self.dropped += len(batch)
            if stopping or monotonic() >= report_at:
                self.report_counts()
                report_at = monotonic() + 10

    async def lifecycle(self, _app):
        writer = asyncio.create_task(self.write())
        yield
        await self.queue.put(None)
        await writer


def create_api(bridge, key="", accounts=None):
    expected = f"Bearer {key}".encode("utf-8")
    login_slots = asyncio.Semaphore(2)
    request_limits = RequestLimits()

    def unauthorized():
        return web.HTTPUnauthorized(headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})

    def maintenance_response():
        return web.HTTPServiceUnavailable(
            headers={"X-Maintenance-Mode": "on", "Cache-Control": "no-store"}
        )

    @web.middleware
    async def require_access(request, handler):
        authorization = request.headers.get("Authorization", "")
        token = ""
        expires_at = None
        path = request.path
        method = request.method
        logout_request = path == "/api/v1/auth/logout" and method == "POST"
        login_request = path == "/api/v1/auth/login" and method == "POST"
        stream_request = path == "/api/v1/events" and method == "GET"
        legacy_get = method == "GET" and not stream_request

        try:
            if accounts is not None:
                maintenance, generation = await asyncio.to_thread(accounts.control)
                if maintenance:
                    raise maintenance_response()
                request["session_generation"] = generation
            if not login_request:
                if legacy_get and key and compare_digest(authorization.encode("utf-8"), expected):
                    identity = ("ip", client_ip(request))
                elif accounts is not None and re.fullmatch(r"Bearer [A-Za-z0-9_-]{43}", authorization):
                    token = authorization[7:]
                    session = await asyncio.to_thread(accounts.session, token)
                    if session is None:
                        raise unauthorized()
                    user_id, expires_at = session
                    identity = ("account", user_id)
                    request["session_token"] = token
                    request["app_user_id"] = user_id
                else:
                    raise unauthorized()

            if not login_request and not logout_request:
                if stream_request:
                    request_limits.check(identity, "stream")
                elif method in ("GET", "HEAD"):
                    request_limits.check(identity, "read")
                elif method in ("POST", "PATCH", "DELETE"):
                    request_limits.check(identity, "write")

                if not bridge.is_ready():
                    raise web.HTTPServiceUnavailable()

            response = await handler(request)
            if (token and not stream_request and not logout_request and response.status in (200, 201, 204)
                    and request.headers.get("X-User-Activity") == "1"):
                try:
                    expires_at = await asyncio.to_thread(accounts.extend, token) or expires_at
                except DatabaseError:
                    logger.warning("Unable to renew an active app session")
        except MaintenanceActive:
            raise maintenance_response() from None
        except DatabaseError:
            raise web.HTTPServiceUnavailable() from None
        except discord.Forbidden:
            raise web.HTTPForbidden() from None
        except discord.NotFound:
            raise web.HTTPNotFound() from None
        except discord.HTTPException:
            raise web.HTTPBadGateway() from None
        if not response.prepared:
            response.headers["Cache-Control"] = "no-store"
            if expires_at is not None and not logout_request:
                response.headers["X-Session-Expires-At"] = expires_at.isoformat()
        return response

    async def login(request):
        if accounts is None:
            raise web.HTTPServiceUnavailable()
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest() from None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest()
        username = body.get("username")
        password = body.get("password")
        if (not isinstance(username, str) or not 1 <= len(username.strip()) <= 128
                or not isinstance(password, str) or not 1 <= len(password) <= 256
                or chr(0) in username or chr(0) in password):
            raise web.HTTPBadRequest()
        if login_slots.locked():
            raise web.HTTPTooManyRequests()
        async with login_slots:
            login_result = await asyncio.to_thread(accounts.login, username.strip(), password)
        if login_result is None:
            raise unauthorized()
        token, user_id = login_result
        request["app_user_id"] = user_id
        return web.json_response({"token": token})

    async def logout(request):
        await asyncio.to_thread(accounts.logout, request["session_token"])
        return web.Response(status=204)

    async def guilds(request):
        return web.json_response({
            "bot_name": str(bridge.user),
            "can_write": "session_token" in request,
            "event_stream": "session_token" in request,
            "guilds": [
                {"id": str(guild.id), "name": guild.name, "icon": asset_url(guild.icon)}
                for guild in sorted(bridge.guilds, key=lambda guild: guild.name)
            ],
        })

    async def channels(request):
        guild = bridge.get_guild(int(request.match_info["guild_id"]))
        if guild is None:
            raise web.HTTPNotFound()
        if guild.me is None:
            raise web.HTTPServiceUnavailable()
        return web.json_response([
            {"id": str(channel_id), "name": name}
            for channel_id, name in bridge.visible_channels(guild)
        ])

    def text_channel(channel_id, *, send=False):
        channel = bridge.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.me is None:
            raise web.HTTPNotFound()
        permissions = channel.permissions_for(channel.guild.me)
        if (not permissions.view_channel or not permissions.read_message_history
                or (send and not permissions.send_messages)):
            raise web.HTTPForbidden()
        return channel

    async def message_input(request):
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest() from None
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > 2000 or chr(0) in text:
            raise web.HTTPBadRequest()
        return body

    async def messages(request):
        channel = text_channel(int(request.match_info["channel_id"]))
        return web.json_response([
            message_row(bridge, message) for message in await bridge.recent_messages(channel)
        ])

    async def send_message(request):
        channel_id = int(request.match_info["channel_id"])
        text_channel(channel_id, send=True)
        body = await message_input(request)
        reply_id = body.get("reply_id")
        if reply_id is not None and (not isinstance(reply_id, str) or not re.fullmatch(r"[0-9]{1,20}", reply_id)):
            raise web.HTTPBadRequest()
        message = await bridge.send_text(channel_id, body["text"], int(reply_id) if reply_id else None)
        return web.json_response(message_row(bridge, message), status=201)

    async def edit_message(request):
        channel_id = int(request.match_info["channel_id"])
        text_channel(channel_id)
        body = await message_input(request)
        try:
            updated = await bridge.edit_text(channel_id, int(request.match_info["message_id"]), body["text"])
        except RuntimeError:
            raise web.HTTPForbidden() from None
        return web.json_response(message_row(bridge, updated))

    async def delete_message(request):
        channel_id = int(request.match_info["channel_id"])
        text_channel(channel_id)
        try:
            await bridge.delete_message(channel_id, int(request.match_info["message_id"]))
        except RuntimeError:
            raise web.HTTPForbidden() from None
        return web.Response(status=204)

    sockets = {}

    async def stream(request):
        token = request["session_token"]
        socket = web.WebSocketResponse(heartbeat=30, max_msg_size=1024, timeout=3)
        await socket.prepare(request)
        request["upgraded_status"] = socket.status
        sockets[socket] = request['session_generation']
        queue = bridge.events.subscribe()

        async def publish():
            try:
                while not socket.closed:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        event = None
                    if not await asyncio.to_thread(accounts.valid, token):
                        maintenance, _ = await asyncio.to_thread(accounts.control)
                        await socket.close(code=4002 if maintenance else 4001)
                        return
                    if event is None:
                        continue
                    kind, channel_id, payload = event
                    if kind in ("message", "deleted"):
                        try:
                            text_channel(channel_id)
                        except (web.HTTPNotFound, web.HTTPForbidden):
                            continue
                        payload = message_row(bridge, payload) if kind == "message" else str(payload)
                    await socket.send_json({"type": kind, "channel_id": str(channel_id) if channel_id else None,
                                            "data": payload})
            except (DatabaseError, ConnectionError):
                await socket.close(code=1013)
            except Exception:
                logger.exception("Unable to send Discord event")
                await socket.close(code=1011)

        sender = asyncio.create_task(publish())
        try:
            async for _ in socket:
                pass
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            bridge.events.unsubscribe(queue)
            sockets.pop(socket, None)
        return socket

    async def watch_control(app):
        async def watch():
            while True:
                await asyncio.sleep(1)
                if not sockets:
                    continue
                try:
                    maintenance, generation = await asyncio.to_thread(accounts.control)
                except DatabaseError:
                    continue
                if maintenance:
                    targets = tuple(sockets)
                    code = 4002
                else:
                    targets = tuple(socket for socket, accepted in sockets.items() if accepted < generation)
                    code = 4001
                await asyncio.gather(*(socket.close(code=code) for socket in targets), return_exceptions=True)

        task = asyncio.create_task(watch())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close_streams(app):
        await asyncio.gather(*(socket.close(code=1001) for socket in tuple(sockets)), return_exceptions=True)

    middlewares = [require_access]
    request_log = ApiRequestLog(accounts) if accounts is not None else None
    if request_log is not None:
        middlewares.insert(0, request_log.record)
    app = web.Application(middlewares=middlewares, client_max_size=16 * 1024)
    if request_log is not None:
        app.cleanup_ctx.append(request_log.lifecycle)
        app.cleanup_ctx.append(watch_control)
    app.router.add_post("/api/v1/auth/login", login)
    app.router.add_post("/api/v1/auth/logout", logout)
    app.router.add_get("/api/v1/guilds", guilds)
    app.router.add_get(r"/api/v1/guilds/{guild_id:[0-9]{1,20}}/channels", channels)
    message_path = r"/api/v1/channels/{channel_id:[0-9]{1,20}}/messages"
    item_path = message_path + r"/{message_id:[0-9]{1,20}}"
    app.router.add_get(message_path, messages)
    app.router.add_post(message_path, send_message)
    app.router.add_patch(item_path, edit_message)
    app.router.add_delete(item_path, delete_message)
    app.router.add_get("/api/v1/events", stream, allow_head=False)
    app.on_shutdown.append(close_streams)
    return app


async def start_api(bridge, key, accounts):
    runner = web.AppRunner(create_api(bridge, key, accounts), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", 8765).start()
    except BaseException:
        await runner.cleanup()
        raise
    return runner
