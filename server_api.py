"""Authenticated, read-only API served on loopback."""

import asyncio
from hmac import compare_digest
import re

import discord
from aiohttp import web
from psycopg import Error as DatabaseError

from chatbot_core import asset_url, avatar_url, message_view, server_display_name


def message_row(bridge, message):
    guild = message.guild
    names = bridge.member_names
    return {
        "id": str(message.id),
        "created_at": message.created_at.isoformat(),
        "author_name": server_display_name(message.author, guild, names),
        "avatar": avatar_url(message.author, guild, bridge.member_avatars),
        "clean_content": message.clean_content,
        "own": message.author.id == bridge.user.id,
        "visible": message_view(message, names, visible=True),
        "hidden": message_view(message, names, visible=False),
    }


def create_api(bridge, key="", accounts=None):
    expected = f"Bearer {key}".encode("utf-8")
    login_slots = asyncio.Semaphore(2)

    def unauthorized():
        return web.HTTPUnauthorized(headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})

    @web.middleware
    async def require_access(request, handler):
        authorization = request.headers.get("Authorization", "")
        token = ""
        path = request.path
        method = request.method
        logout_request = path == "/api/v1/auth/logout" and method == "POST"
        login_request = path == "/api/v1/auth/login" and method == "POST"

        try:
            if not login_request:
                if not logout_request and key and compare_digest(authorization.encode("utf-8"), expected):
                    pass
                elif accounts is not None and re.fullmatch(r"Bearer [A-Za-z0-9_-]{43}", authorization):
                    token = authorization[7:]
                    if not await asyncio.to_thread(accounts.valid, token):
                        raise unauthorized()
                    request["session_token"] = token
                else:
                    raise unauthorized()

            if not login_request and not logout_request and not bridge.is_ready():
                raise web.HTTPServiceUnavailable()

            response = await handler(request)
            if (token and method == "GET" and response.status == 200
                    and request.headers.get("X-User-Activity") == "1"):
                await asyncio.to_thread(accounts.extend, token)
        except DatabaseError:
            raise web.HTTPServiceUnavailable() from None
        except discord.Forbidden:
            raise web.HTTPForbidden() from None
        except discord.NotFound:
            raise web.HTTPNotFound() from None
        except discord.HTTPException:
            raise web.HTTPBadGateway() from None
        response.headers["Cache-Control"] = "no-store"
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
            token = await asyncio.to_thread(accounts.login, username.strip(), password)
        if token is None:
            raise unauthorized()
        return web.json_response({"token": token})

    async def logout(request):
        await asyncio.to_thread(accounts.logout, request["session_token"])
        return web.Response(status=204)

    async def guilds(request):
        return web.json_response({
            "bot_name": str(bridge.user),
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

    async def messages(request):
        channel = bridge.get_channel(int(request.match_info["channel_id"]))
        if not isinstance(channel, discord.TextChannel) or channel.guild.me is None:
            raise web.HTTPNotFound()
        permissions = channel.permissions_for(channel.guild.me)
        if not permissions.view_channel or not permissions.read_message_history:
            raise web.HTTPForbidden()
        return web.json_response([
            message_row(bridge, message) for message in await bridge.recent_messages(channel)
        ])

    app = web.Application(middlewares=[require_access], client_max_size=16 * 1024)
    app.router.add_post("/api/v1/auth/login", login)
    app.router.add_post("/api/v1/auth/logout", logout)
    app.router.add_get("/api/v1/guilds", guilds)
    app.router.add_get(r"/api/v1/guilds/{guild_id:[0-9]{1,20}}/channels", channels)
    app.router.add_get(r"/api/v1/channels/{channel_id:[0-9]{1,20}}/messages", messages)
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
