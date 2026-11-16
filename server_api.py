"""Authenticated, read-only API served on loopback."""

from hmac import compare_digest

import discord
from aiohttp import web

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


def create_api(bridge, key):
    expected = f"Bearer {key}".encode("utf-8")

    @web.middleware
    async def require_access(request, handler):
        provided = request.headers.get("Authorization", "").encode("utf-8")
        if not compare_digest(provided, expected):
            raise web.HTTPUnauthorized(headers={"WWW-Authenticate": "Bearer"})
        if not bridge.is_ready():
            raise web.HTTPServiceUnavailable()
        try:
            response = await handler(request)
        except discord.Forbidden:
            raise web.HTTPForbidden() from None
        except discord.NotFound:
            raise web.HTTPNotFound() from None
        except discord.HTTPException:
            raise web.HTTPBadGateway() from None
        response.headers["Cache-Control"] = "no-store"
        return response

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

    app = web.Application(middlewares=[require_access])
    app.router.add_get("/api/v1/guilds", guilds)
    app.router.add_get(r"/api/v1/guilds/{guild_id:[0-9]{1,20}}/channels", channels)
    app.router.add_get(r"/api/v1/channels/{channel_id:[0-9]{1,20}}/messages", messages)
    return app


async def start_api(bridge, key):
    runner = web.AppRunner(create_api(bridge, key), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", 8765).start()
    except BaseException:
        await runner.cleanup()
        raise
    return runner
