"""Discord control panel shown for the current track."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord

from core.youtube import Track, format_duration
from core.randomplay import collect_random_playable

if TYPE_CHECKING:
    from core.player import GuildPlayer

logger = logging.getLogger(__name__)


class MusicControlView(discord.ui.View):
    def __init__(self, panel: "MusicPanel") -> None:
        super().__init__(timeout=None)
        self.panel = panel

    def sync_labels(self) -> None:
        player = self.panel.player
        paused = bool(player.voice and player.voice.is_paused())
        self.pause_resume.label = "繼續" if paused else "暫停"
        self.pause_resume.emoji = "▶️" if paused else "⏸️"

    @discord.ui.button(
        label="暫停", emoji="⏸️", style=discord.ButtonStyle.primary, row=0
    )
    async def pause_resume(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        player = self.panel.player
        if player.voice and player.voice.is_paused():
            player.resume()
        else:
            player.pause()
        self.sync_labels()
        await interaction.response.defer()
        await self.panel.refresh()

    @discord.ui.button(
        label="跳過", emoji="⏭️", style=discord.ButtonStyle.secondary, row=0
    )
    async def skip(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        await self.panel.player.skip()
        await self.panel.refresh()

    @discord.ui.button(
        label="停止", emoji="⏹️", style=discord.ButtonStyle.danger, row=0
    )
    async def stop_playback(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        await self.panel.player.bot.music.remove(self.panel.player.guild.id)

    @discord.ui.button(
        label="隊列", emoji="📜", style=discord.ButtonStyle.secondary, row=0
    )
    async def queue(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            embed=self.panel.player.queue_embed(), ephemeral=True, silent=True
        )

    @discord.ui.button(
        label="音量 -", emoji="🔉", style=discord.ButtonStyle.secondary, row=1
    )
    async def volume_down(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        player = self.panel.player
        player.set_volume(player.volume - 0.1)
        await interaction.response.defer()
        await self.panel.refresh()

    @discord.ui.button(
        label="音量 +", emoji="🔊", style=discord.ButtonStyle.secondary, row=1
    )
    async def volume_up(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        player = self.panel.player
        player.set_volume(player.volume + 0.1)
        await interaction.response.defer()
        await self.panel.refresh()

    @discord.ui.button(
        label="隨機 5 首", emoji="🎲", style=discord.ButtonStyle.success, row=1
    )
    async def random_five(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        player = self.panel.player
        try:
            if player.voice is None or not player.voice.is_connected():
                if player.voice_channel is None:
                    raise RuntimeError("目前沒有可重新連接的語音頻道。")
                await player.connect(player.voice_channel)

            tracks = await collect_random_playable(
                5, interaction.user.id, interaction.user.display_name
            )
            if not tracks:
                await interaction.followup.send(
                    "歌庫裡目前沒有可播放的歌曲。",
                    ephemeral=True,
                    silent=True,
                )
                return
            for track in tracks:
                player.enqueue(track)
            player.start()
            await self.panel.refresh()
            await interaction.followup.send(
                f"已加入 {len(tracks)} 首隨機歌曲。",
                ephemeral=True,
                silent=True,
            )
        except Exception as exc:
            logger.exception("面板隨機 5 首失敗")
            await interaction.followup.send(
                f"隨機加入失敗：{exc}", ephemeral=True, silent=True
            )


class MusicPanel:
    def __init__(self, player: "GuildPlayer") -> None:
        self.player = player
        self.message: discord.Message | None = None
        self.view = MusicControlView(self)

    def build_embed(self, *, finished_reason: str | None = None) -> discord.Embed:
        player = self.player
        embed = discord.Embed(
            title="🎵 音樂控制台",
            color=discord.Color.blurple(),
        )
        if finished_reason:
            embed.description = finished_reason
        elif player.current is not None:
            track = player.current
            embed.description = (
                f"**{track.title[:200]}**\n"
                f"{format_duration(track.duration)} • 點歌：{track.requester_name}"
            )
        else:
            embed.description = "目前沒有播放中的歌曲。"

        paused = bool(player.voice and player.voice.is_paused())
        embed.add_field(name="隊列", value=f"{player.queue.qsize()} 首", inline=True)
        embed.add_field(name="音量", value=f"{int(player.volume * 100)}%", inline=True)
        embed.add_field(
            name="狀態",
            value=finished_reason or ("暫停" if paused else "播放中"),
            inline=True,
        )
        embed.set_footer(text="Eileen Music • 按鈕可直接控制")
        return embed

    async def show(self, track: Track) -> None:
        """Delete the previous panel and send a fresh one for the new track."""
        self.view.stop()
        if self.message is not None:
            try:
                await asyncio.wait_for(self.message.delete(), timeout=10)
            except discord.NotFound:
                pass
            except asyncio.TimeoutError:
                logger.warning("刪除舊音樂面板逾時")
            except discord.HTTPException:
                logger.warning("無法刪除舊音樂面板", exc_info=True)
            self.message = None

        if self.player.text_channel is None:
            return

        self.view = MusicControlView(self)
        try:
            self.message = await asyncio.wait_for(
                self.player.text_channel.send(
                    embed=self.build_embed(), view=self.view, silent=True
                ),
                timeout=15,
            )
        except asyncio.TimeoutError:
            logger.warning("發送音樂控制面板逾時")
            self.message = None
        except discord.HTTPException:
            logger.exception("無法發送音樂控制面板")

    async def refresh(self) -> None:
        if self.message is None:
            return
        self.view.sync_labels()
        try:
            await asyncio.wait_for(
                self.message.edit(embed=self.build_embed(), view=self.view),
                timeout=10,
            )
        except asyncio.TimeoutError:
            logger.warning("更新音樂控制面板逾時")
        except discord.NotFound:
            self.message = None
        except discord.HTTPException:
            logger.warning("無法更新音樂控制面板", exc_info=True)

    async def close(self, reason: str) -> None:
        self.view.stop()
        if self.message is None:
            return
        try:
            await asyncio.wait_for(
                self.message.edit(
                    embed=self.build_embed(finished_reason=reason), view=None
                ),
                timeout=10,
            )
        except asyncio.TimeoutError:
            logger.warning("關閉音樂控制面板逾時")
        except discord.NotFound:
            pass
        except discord.HTTPException:
            logger.warning("無法關閉音樂控制面板", exc_info=True)
        self.message = None
