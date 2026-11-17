"""Discord commands and room cards for the Gomoku web game."""

import logging
from urllib.parse import urlsplit

import discord
from discord import app_commands

from .gomoku_game import Member, RoomError

logger = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()
REASONS = {
    "five": "오목",
    "timeout": "시간패",
    "resignation": "기권",
    "no-legal-move": "합법 착수 없음",
}


def _member(user):
    return Member(str(user.id), user.display_name, str(user.display_avatar.url))


def _name(member):
    return discord.utils.escape_markdown(member.name)


class RoomCard(discord.ui.View):
    def __init__(self, service, room):
        super().__init__(timeout=None)
        self.service = service
        self.room = room
        if room.phase != "waiting":
            self.remove_item(self.close_room)

    @discord.ui.button(label="참가", style=discord.ButtonStyle.primary, custom_id="gomoku:player")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.service.enter(interaction, self.room, "player")

    @discord.ui.button(label="관전", style=discord.ButtonStyle.secondary, custom_id="gomoku:spectator")
    async def watch(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.service.enter(interaction, self.room, "spectator")

    @discord.ui.button(label="방 종료", style=discord.ButtonStyle.danger, custom_id="gomoku:close")
    async def close_room(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.service.close(interaction, self.room)


class GomokuDiscord:
    def __init__(self, rooms, public_url):
        parsed = urlsplit(public_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Gomoku requires an HTTPS public origin")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Gomoku public URL must be an origin")
        self.public_url = public_url.rstrip("/")
        self.rooms = rooms
        self.cards = {}
        group = app_commands.Group(name="오목", description="웹 오목", guild_only=True)
        group.add_command(app_commands.Command(
            name="방만들기", description="웹 오목 방을 만듭니다.", callback=self.create
        ))
        self.command = group

    def register(self, tree):
        tree.add_command(self.command)

    def _card(self, room):
        embed = discord.Embed(title="오목", colour=0x257A3E)
        embed.description = {
            "waiting": "대국자 대기 중",
            "playing": "대국 진행 중",
            "finished": "대국 종료",
            "closed": "방 종료",
        }[room.phase]
        names = [_name(player) for player in room.players.values()]
        if len(names) == 1:
            names.append("대기 중")
        embed.add_field(name="대국자", value="  vs  ".join(names), inline=False)
        embed.add_field(name="관전", value=f"관전 {len(room.spectators)}명")
        embed.add_field(name="모드", value="일반" if room.mode == 1 else "블리츠")
        if room.result is not None:
            winner = room.players.get(room.result.winner)
            label = f"{_name(winner)} 승리" if winner else "무승부"
            reason = REASONS[room.result.reason]
            embed.add_field(name="결과", value=f"{label} · {reason}", inline=False)
        return embed

    async def create(self, interaction: discord.Interaction):
        if interaction.guild_id is None or interaction.channel is None:
            await interaction.response.send_message("서버 채널에서 사용하세요.", ephemeral=True)
            return
        try:
            room = self.rooms.create(str(interaction.guild_id), _member(interaction.user))
        except RoomError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        view = RoomCard(self, room)
        try:
            await interaction.response.defer(ephemeral=True)
            message = await interaction.channel.send(
                embed=self._card(room), view=view, allowed_mentions=NO_MENTIONS
            )
        except discord.HTTPException:
            view.stop()
            room.phase = "closed"
            raise

        self.cards[room.id] = (message, view)
        await self.enter(interaction, room, "player")

    async def enter(self, interaction, room, role):
        try:
            ticket = self.rooms.issue_ticket(
                room.id, str(interaction.guild_id), _member(interaction.user), role
            )
        except RoomError as error:
            await self._private(interaction, str(error))
            return
        link = discord.ui.View()
        link.add_item(discord.ui.Button(
            label="웹 입장",
            url=f"{self.public_url}/gomoku/{room.id}#ticket={ticket}",
        ))
        await self._private(interaction, "30초 안에 웹 입장 버튼을 누르세요.", view=link)

    async def close(self, interaction, room):
        if str(interaction.guild_id) != room.guild_id:
            await self._private(interaction, "이 서버의 방이 아닙니다.")
            return
        try:
            room.close_waiting(str(interaction.user.id), self.rooms.clock())
        except RoomError as error:
            await self._private(interaction, str(error))
            return
        await self._private(interaction, "방을 종료했습니다.")
        await self.refresh(room)

    @staticmethod
    async def _private(interaction, text, **kwargs):
        send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await send(text, ephemeral=True, allowed_mentions=NO_MENTIONS, **kwargs)

    async def refresh(self, room):
        previous = self.cards.get(room.id)
        if previous is None:
            return
        message, card = previous
        if room.phase != "waiting":
            card.remove_item(card.close_room)
        view = card if room.phase in ("waiting", "playing") else None
        try:
            await message.edit(embed=self._card(room), view=view, allowed_mentions=NO_MENTIONS)
        except discord.NotFound:
            self.cards.pop(room.id, None)
            card.stop()
            return
        except discord.HTTPException:
            logger.warning("Could not update Gomoku card for room %s", room.id)
            return
        if view is None:
            card.stop()
        if room.phase == "closed":
            self.cards.pop(room.id, None)
