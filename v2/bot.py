"""Discord music bot entry point."""

from __future__ import annotations

import logging
import os
import sys

import discord
from discord.ext import commands

from config import FFMPEG_PATH, LOG_DIR, PID_FILE, TOKEN, configure_logging
from core.player import MusicManager

logger = logging.getLogger(__name__)


class MusicBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=True
            ),
        )
        self.music = MusicManager(self)
        self._commands_synced = False

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.music")
        logger.info("已載入 music cog")

    async def _apply_presence(self) -> None:
        try:
            await self.change_presence(
                status=discord.Status.do_not_disturb,
                activity=discord.CustomActivity(name="勿擾"),
            )
            logger.info("已設定勿擾狀態")
            return
        except Exception:
            logger.exception("自訂勿擾狀態失敗，改用一般活動")

        try:
            await self.change_presence(
                status=discord.Status.do_not_disturb,
                activity=discord.Activity(
                    type=discord.ActivityType.listening, name="勿擾"
                ),
            )
            logger.info("已設定勿擾狀態（Listening）")
        except Exception:
            logger.exception("設定勿擾狀態失敗")

    async def on_ready(self) -> None:
        logger.info("已登入：%s（ID: %s）", self.user, self.user.id if self.user else "?")
        logger.info("伺服器數量：%s", len(self.guilds))
        await self._apply_presence()
        if not self._commands_synced:
            try:
                synced = await self.tree.sync()
                self._commands_synced = True
                logger.info("已同步 %s 個斜線指令", len(synced))
            except Exception:
                logger.exception("同步斜線指令失敗")

    async def on_resumed(self) -> None:
        logger.info("Discord 連線已恢復，重新套用勿擾狀態")
        await self._apply_presence()


def main() -> int:
    configure_logging()
    if not TOKEN:
        logger.error(
            "找不到 DISCORD_BOT_TOKEN。請複製 .env.example 成 .env，填入新的 Bot Token。"
        )
        return 1
    if not FFMPEG_PATH:
        logger.warning(
            "找不到 FFmpeg。播放會失敗；請安裝 FFmpeg 或在 .env 設定 FFMPEG_PATH。"
        )
    logger.info("日誌目錄：%s", LOG_DIR)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    bot = MusicBot()
    try:
        bot.run(TOKEN, log_handler=None)
    except discord.LoginFailure:
        logger.error("Discord 登入失敗：Token 無效或已被重設。")
        return 1
    except KeyboardInterrupt:
        logger.info("收到中斷，正在關閉。")
    finally:
        PID_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
