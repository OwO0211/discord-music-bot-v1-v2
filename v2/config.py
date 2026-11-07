"""Central configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
try:
    IDLE_DISCONNECT_SECONDS = max(
        10, int(os.getenv("IDLE_DISCONNECT_SECONDS", "60"))
    )
except ValueError:
    IDLE_DISCONNECT_SECONDS = 60

DATA_DIR = BASE_DIR / "data"
DOWNLOAD_DIR = DATA_DIR / "downloads"
LOG_DIR = BASE_DIR / "logs"
MUSIC_DB_PATH = DATA_DIR / "music.db"
PID_FILE = BASE_DIR / "eileen.pid"
for directory in (DATA_DIR, DOWNLOAD_DIR, LOG_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def _find_ffmpeg() -> str | None:
    explicit = os.getenv("FFMPEG_PATH", "").strip()
    if explicit:
        return explicit
    return shutil.which("ffmpeg")


FFMPEG_PATH = _find_ffmpeg()


def configure_logging() -> None:
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8")
    file_handler.setFormatter(formatter)

    handlers = [file_handler]
    if sys.stderr is not None:
        handlers.insert(0, stream_handler)

    logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True,
    )
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)
