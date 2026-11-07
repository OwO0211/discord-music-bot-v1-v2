"""Slash commands for music playback."""

from __future__ import annotations

import logging
from dataclasses import replace

import discord
from discord import app_commands
from discord.ext import commands

from config import FFMPEG_PATH
from core.library import song_count
from core.player import GuildPlayer, MusicManager
from core.randomplay import collect_random_playable
from core.youtube import (
    Track,
    format_duration,
    resolve_track,
    search_tracks,
)

logger = logging.getLogger(__name__)


async def _silent_respond(interaction: discord.Interaction, *args, **kwargs) -> None:
    """Send an interaction response without push notifications."""
    kwargs["silent"] = True
    await interaction.response.send_message(*args, **kwargs)


async def _silent_followup(interaction: discord.Interaction, *args, **kwargs) -> None:
    """Send a follow-up without push notifications."""
    kwargs["silent"] = True
    await interaction.followup.send(*args, **kwargs)


class MusicError(Exception):
    """A user-facing music error."""


class SearchSelect(discord.ui.Select):
    def __init__(self, tracks: list[Track], player: GuildPlayer) -> None:
        self.tracks = tracks
        self.player = player
        options = [
            discord.SelectOption(
                label=track.title[:100],
                description=format_duration(track.duration),
                value=str(index),
            )
            for index, track in enumerate(tracks)
        ]
        super().__init__(placeholder="選擇一首歌", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        index = int(self.values[0])
        track = replace(
            self.tracks[index],
            requester_id=interaction.user.id,
            requester_name=interaction.user.display_name,
        )
        self.player.enqueue(track)
        self.player.start()
        await _silent_respond(interaction, 
            f"已加入隊列：**{track.title}**", ephemeral=True
        )


class SearchView(discord.ui.View):
    def __init__(self, tracks: list[Track], player: GuildPlayer) -> None:
        super().__init__(timeout=60)
        self.add_item(SearchSelect(tracks, player))


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.music: MusicManager = bot.music  # type: ignore[attr-defined]

    @staticmethod
    def _voice_channel(interaction: discord.Interaction) -> discord.VoiceChannel:
        if interaction.guild is None:
            raise MusicError("這個指令只能在伺服器裡使用。")
        member = interaction.user
        if not isinstance(member, discord.Member) or member.voice is None:
            raise MusicError("請先加入一個語音頻道。")
        if member.voice.channel is None:
            raise MusicError("請先加入一個語音頻道。")
        return member.voice.channel

    async def _prepare(
        self, interaction: discord.Interaction, query: str
    ) -> tuple[GuildPlayer, Track]:
        if not FFMPEG_PATH:
            raise MusicError(
                "找不到 FFmpeg。請安裝 FFmpeg，或在 `.env` 設定 `FFMPEG_PATH`。"
            )
        channel = self._voice_channel(interaction)
        player = self.music.get(interaction.guild, interaction.channel)  # type: ignore[arg-type]
        await player.connect(channel)
        track = await resolve_track(
            query, interaction.user.id, interaction.user.display_name
        )
        return player, track

    @app_commands.command(name="play", description="播放 YouTube 歌曲或搜尋關鍵字")
    @app_commands.describe(query="YouTube 網址或歌曲關鍵字")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(thinking=True)
        try:
            player, track = await self._prepare(interaction, query)
            position = player.enqueue(track)
            player.start()
            if player.current is None and position == 1:
                message = f"開始播放：**{track.title}**"
            else:
                message = f"已加入隊列（第 {position} 首）：**{track.title}**"
            await _silent_followup(interaction, message)
        except MusicError as exc:
            await _silent_followup(interaction, str(exc))
        except Exception as exc:
            logger.exception("play 指令失敗")
            await _silent_followup(interaction, f"搜尋或播放失敗：{exc}")

    @app_commands.command(name="search", description="搜尋 YouTube 並選擇歌曲")
    @app_commands.describe(query="歌曲關鍵字")
    async def search(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(thinking=True)
        try:
            self._voice_channel(interaction)
            if not FFMPEG_PATH:
                raise MusicError(
                    "找不到 FFmpeg。請安裝 FFmpeg，或在 `.env` 設定 `FFMPEG_PATH`。"
                )
            results = await search_tracks(
                query, interaction.user.id, interaction.user.display_name
            )
            if not results:
                raise MusicError("找不到符合的歌曲。")
            player = self.music.get(interaction.guild, interaction.channel)  # type: ignore[arg-type]
            await player.connect(self._voice_channel(interaction))
            view = SearchView(results, player)
            await _silent_followup(interaction, 
                "選一首歌加入隊列：", view=view, ephemeral=True
            )
        except MusicError as exc:
            await _silent_followup(interaction, str(exc))
        except Exception as exc:
            logger.exception("search 指令失敗")
            await _silent_followup(interaction, f"搜尋失敗：{exc}")

    @app_commands.command(
        name="randomplay", description="從舊歌庫隨機播放 1～5 首歌曲"
    )
    @app_commands.describe(count="要隨機播放幾首（1～5）")
    async def randomplay(self, interaction: discord.Interaction, count: int = 1) -> None:
        await interaction.response.defer(thinking=True)
        try:
            if count < 1 or count > 5:
                raise MusicError("請指定 1 到 5 首。")
            if not FFMPEG_PATH:
                raise MusicError(
                    "找不到 FFmpeg。請安裝 FFmpeg，或在 `.env` 設定 `FFMPEG_PATH`。"
                )
            channel = self._voice_channel(interaction)
            player = self.music.get(interaction.guild, interaction.channel)  # type: ignore[arg-type]
            await player.connect(channel)
            tracks = await collect_random_playable(
                count, interaction.user.id, interaction.user.display_name
            )
            if not tracks:
                raise MusicError("歌庫裡目前沒有可播放的歌曲。")
            for track in tracks:
                player.enqueue(track)
            player.start()
            description = "\n".join(f"🎵 {track.title}" for track in tracks)
            embed = discord.Embed(
                title=f"隨機播放 {len(tracks)} 首",
                description=description,
                color=discord.Color.blurple(),
            )
            embed.set_footer(text=f"由 {interaction.user.display_name} 觸發")
            await _silent_followup(interaction, embed=embed)
        except MusicError as exc:
            await _silent_followup(interaction, str(exc))
        except Exception as exc:
            logger.exception("randomplay 指令失敗")
            await _silent_followup(interaction, f"隨機播放失敗：{exc}")

    @app_commands.command(name="library", description="查看歌庫目前有幾首歌")
    async def library(self, interaction: discord.Interaction) -> None:
        try:
            count = await song_count()
            await _silent_respond(interaction, 
                f"歌庫目前有 **{count}** 首歌。", ephemeral=True
            )
        except Exception as exc:
            logger.exception("library 指令失敗")
            await _silent_respond(interaction, f"讀取歌庫失敗：{exc}", ephemeral=True)

    @app_commands.command(name="skip", description="跳過目前播放的歌曲")
    async def skip(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if player is None or not await player.skip():
            await _silent_respond(interaction, "目前沒有正在播放的歌曲。", ephemeral=True)
            return
        await player.refresh_panel()
        await _silent_respond(interaction, "已跳過這首。")

    @app_commands.command(name="pause", description="暫停播放")
    async def pause(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if player is None or not player.pause():
            await _silent_respond(interaction, "目前沒有正在播放的歌曲。", ephemeral=True)
            return
        await player.refresh_panel()
        await _silent_respond(interaction, "已暫停。")

    @app_commands.command(name="resume", description="繼續播放")
    async def resume(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if player is None or not player.resume():
            await _silent_respond(interaction, "目前沒有暫停中的歌曲。", ephemeral=True)
            return
        await player.refresh_panel()
        await _silent_respond(interaction, "繼續播放。")

    @app_commands.command(name="stop", description="停止播放並離開語音頻道")
    async def stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await _silent_respond(interaction, "只能在伺服器使用。", ephemeral=True)
            return
        await self.music.remove(interaction.guild.id)
        await _silent_respond(interaction, "已停止播放並離開語音頻道。")

    @app_commands.command(name="queue", description="查看目前播放隊列")
    async def queue(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if player is None:
            await _silent_respond(interaction, "目前沒有播放隊列。", ephemeral=True)
            return
        await _silent_respond(interaction, embed=player.queue_embed())

    @app_commands.command(name="nowplaying", description="查看目前播放的歌曲")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if player is None:
            await _silent_respond(interaction, "目前沒有播放中的歌曲。", ephemeral=True)
            return
        await _silent_respond(interaction, embed=player.now_playing_embed())

    @app_commands.command(name="volume", description="調整音量（0～200）")
    @app_commands.describe(percent="音量百分比，例如 100")
    async def volume(self, interaction: discord.Interaction, percent: int) -> None:
        player = self._player(interaction)
        if player is None:
            await _silent_respond(interaction, "目前沒有播放器。", ephemeral=True)
            return
        value = player.set_volume(percent / 100)
        await player.refresh_panel()
        await _silent_respond(interaction, f"音量已設為 {int(value * 100)}%。")

    @app_commands.command(name="join", description="讓機器人加入你目前的語音頻道")
    async def join(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        try:
            channel = self._voice_channel(interaction)
            player = self.music.get(interaction.guild, interaction.channel)  # type: ignore[arg-type]
            await player.connect(channel)
            await _silent_followup(interaction, f"已加入 **{channel.name}**。")
        except MusicError as exc:
            await _silent_followup(interaction, str(exc))

    def _player(self, interaction: discord.Interaction) -> GuildPlayer | None:
        if interaction.guild is None:
            return None
        return self.music.players.get(interaction.guild.id)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if self.bot.user is None or member.id != self.bot.user.id:
            return
        if after.channel is not None:
            return
        if member.guild is None:
            return
        player = self.music.players.get(member.guild.id)
        if player is not None:
            await self.music.remove(member.guild.id)

    async def cog_unload(self) -> None:
        await self.music.destroy_all()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
