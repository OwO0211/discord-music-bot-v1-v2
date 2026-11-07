"""Per-guild music queue and playback lifecycle."""

from __future__ import annotations

import asyncio
import logging

import discord

from core.youtube import Track, YTDLSource, format_duration
from core.panel import MusicPanel
from config import IDLE_DISCONNECT_SECONDS

logger = logging.getLogger(__name__)


class GuildPlayer:
    def __init__(
        self, bot: discord.Client, guild: discord.Guild, text_channel: discord.abc.Messageable
    ) -> None:
        self.bot = bot
        self.guild = guild
        self.text_channel = text_channel
        self.queue: asyncio.Queue[Track] = asyncio.Queue()
        self.current: Track | None = None
        self.voice: discord.VoiceClient | None = None
        self.voice_channel: discord.VoiceChannel | None = None
        self.volume = 0.5
        self.idle_timeout = IDLE_DISCONNECT_SECONDS
        self._next = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._closed = False
        self.panel: MusicPanel | None = None

    async def connect(self, channel: discord.VoiceChannel) -> None:
        current = self.voice
        if current is not None and current.is_connected():
            if current.channel != channel:
                await current.move_to(channel)
            self.voice_channel = channel
            return

        existing = self.guild.voice_client
        if existing is not None and existing.is_connected():
            if existing.channel != channel:
                await existing.move_to(channel)
            self.voice = existing
            self.voice_channel = channel
            return

        for stale in (self.voice, existing):
            if stale is not None:
                try:
                    await stale.disconnect(force=True)
                except Exception:
                    pass
        self.voice = None

        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                self.voice = await channel.connect(self_deaf=True, timeout=12)
                self.voice_channel = channel
                logger.info(
                    "已連接語音頻道 %s（第 %s 次嘗試）", channel.id, attempt
                )
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "連接語音頻道 %s 失敗（第 %s/3 次）：%r",
                    channel.id,
                    attempt,
                    exc,
                )
                await asyncio.sleep(1)

        raise RuntimeError(
            "Discord 語音連線逾時，請稍後再試一次；如果持續失敗，請重新加入語音頻道。"
        ) from last_error

    def enqueue(self, track: Track) -> int:
        self.queue.put_nowait(track)
        return self.queue.qsize()

    def start(self) -> None:
        self._closed = False
        if self._task is None or self._task.done():
            self._task = self.bot.loop.create_task(self._loop())

    async def _loop(self) -> None:
        while not self._closed:
            try:
                track = await asyncio.wait_for(
                    self.queue.get(), timeout=self.idle_timeout
                )
            except asyncio.TimeoutError:
                await self._idle_disconnect()
                music = getattr(self.bot, "music", None)
                if music is not None:
                    music.players.pop(self.guild.id, None)
                break

            if track is None:
                break

            if self.voice is None or not self.voice.is_connected():
                if self.voice_channel is not None:
                    try:
                        await self.connect(self.voice_channel)
                    except Exception as exc:
                        logger.warning("重新連接語音頻道失敗：%s", exc)
                if self.voice is None or not self.voice.is_connected():
                    await self._send(
                        "語音連線已中斷，請重新使用 `/play`；我會等你下一次點歌。"
                    )
                    break

            self.current = track
            try:
                source = await asyncio.wait_for(
                    YTDLSource.create(track, self.volume), timeout=60
                )
                if track.duration is None:
                    track.duration = source.data.get("duration")
                if self.voice is None or not self.voice.is_connected():
                    break
                self._next.clear()
                self.voice.play(source, after=self._after_play)
                logger.info(
                    "Guild %s 開始播放：%s（duration=%s）",
                    self.guild.id,
                    track.title,
                    track.duration,
                )
                await self._show_panel(track)
                timeout = (
                    max(30, int(track.duration) + 30)
                    if track.duration
                    else 900
                )
                try:
                    await asyncio.wait_for(self._next.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Guild %s 播放 %s 超過 %s 秒仍未結束，強制跳過",
                        self.guild.id,
                        track.title,
                        timeout,
                    )
                    if self.voice and (
                        self.voice.is_playing() or self.voice.is_paused()
                    ):
                        self.voice.stop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("播放失敗：%s", exc)
                error_text = str(exc).lower()
                if any(
                    marker in error_text
                    for marker in (
                        "private video",
                        "video unavailable",
                        "has been removed",
                        "deleted",
                        "copyright",
                    )
                ):
                    reason = "私人影片、已刪除或來源失效"
                else:
                    reason = "來源暫時無法播放"
                await self._send(f"`{track.title}` 無法播放（{reason}），已跳過。")
            finally:
                self.current = None

        logger.info("Guild %s 的播放器已停止", self.guild.id)

    async def _idle_disconnect(self) -> None:
        self._closed = True
        if self.voice is not None:
            try:
                await self.voice.disconnect(force=True)
            except Exception:
                logger.exception("閒置時離開語音頻道失敗")
        self.voice = None
        self.current = None
        if self.panel is not None:
            await self.panel.close("隊列空了，已離開語音頻道。")
        else:
            await self._send(
                "隊列空了，我先離開語音頻道；要再聽歌直接 `/play` 就可以。"
            )

    async def _show_panel(self, track: Track) -> None:
        if self.panel is None:
            self.panel = MusicPanel(self)
        await self.panel.show(track)

    async def refresh_panel(self) -> None:
        if self.panel is not None:
            await self.panel.refresh()

    def _after_play(self, error: Exception | None) -> None:
        if error:
            logger.error("FFmpeg 播放錯誤：%s", error)
        if not self.bot.loop.is_closed():
            self.bot.loop.call_soon_threadsafe(self._next.set)

    async def skip(self) -> bool:
        if self.voice and self.voice.is_playing():
            self.voice.stop()
            return True
        if self.current is None:
            return False
        self._next.set()
        return True

    def pause(self) -> bool:
        if self.voice and self.voice.is_playing():
            self.voice.pause()
            return True
        return False

    def resume(self) -> bool:
        if self.voice and self.voice.is_paused():
            self.voice.resume()
            return True
        return False

    def set_volume(self, volume: float) -> float:
        self.volume = max(0.0, min(2.0, volume))
        source = getattr(self.voice, "source", None) if self.voice else None
        if isinstance(source, discord.PCMVolumeTransformer):
            source.volume = self.volume
        return self.volume

    async def stop(self, *, disconnect: bool = True) -> None:
        self._closed = True
        self.queue = asyncio.Queue()
        if self._task and not self._task.done():
            self._task.cancel()
        if self.voice:
            if self.voice.is_playing() or self.voice.is_paused():
                self.voice.stop()
            if disconnect:
                try:
                    await self.voice.disconnect(force=True)
                except Exception:
                    pass
        self.voice = None
        self.current = None
        if self.panel is not None:
            await self.panel.close("已停止播放並離開語音頻道。")

    async def destroy(self) -> None:
        await self.stop(disconnect=True)

    def now_playing_embed(self) -> discord.Embed:
        if self.current is None:
            return discord.Embed(
                title="目前沒有播放中的歌曲",
                description="使用 `/play` 點一首吧。",
                color=discord.Color.blurple(),
            )
        return discord.Embed(
            title="正在播放",
            description=(
                f"**{self.current.title}**\n"
                f"長度：{format_duration(self.current.duration)}\n"
                f"點歌：{self.current.requester_name}"
            ),
            color=discord.Color.blurple(),
        )

    def queue_embed(self) -> discord.Embed:
        embed = discord.Embed(title="播放隊列", color=discord.Color.blurple())
        if self.current:
            embed.add_field(
                name="正在播放",
                value=f"**{self.current.title}**",
                inline=False,
            )
        pending = list(self.queue._queue)
        if pending:
            lines = [
                f"{index}. {track.title}（{format_duration(track.duration)}）"
                for index, track in enumerate(pending[:10], start=1)
            ]
            if len(pending) > 10:
                lines.append(f"... 還有 {len(pending) - 10} 首")
            embed.add_field(name="下一首", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="下一首", value="隊列是空的", inline=False)
        return embed

    async def _send(self, content: str | None = None, *, embed: discord.Embed | None = None) -> None:
        if self.text_channel is None:
            return
        try:
            await self.text_channel.send(
                content=content, embed=embed, silent=True
            )
        except Exception:
            logger.exception("無法發送訊息到文字頻道")


class MusicManager:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}

    def get(
        self, guild: discord.Guild, text_channel: discord.abc.Messageable
    ) -> GuildPlayer:
        player = self.players.get(guild.id)
        if player is None:
            player = GuildPlayer(self.bot, guild, text_channel)
            self.players[guild.id] = player
        else:
            player.text_channel = text_channel
        return player

    async def remove(self, guild_id: int) -> None:
        player = self.players.pop(guild_id, None)
        if player:
            await player.destroy()

    async def destroy_all(self) -> None:
        for guild_id in list(self.players):
            await self.remove(guild_id)
