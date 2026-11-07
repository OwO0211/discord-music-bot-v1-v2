"""YouTube search and audio stream helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse

import discord
import yt_dlp

from config import FFMPEG_PATH

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "cachedir": False,
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

FFMPEG_BEFORE_OPTIONS = (
    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
)
FFMPEG_OPTIONS = "-vn"


@dataclass
class Track:
    title: str
    webpage_url: str
    duration: int | None
    requester_id: int
    requester_name: str


def format_duration(seconds: int | None) -> str:
    if not seconds:
        return "未知長度"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _extract(query: str, *, flat: bool = False) -> dict:
    options = dict(YTDL_OPTIONS)
    if flat:
        options["extract_flat"] = True
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(query, download=False)


def _first_entry(data: dict) -> dict:
    if data and data.get("entries"):
        entries = [entry for entry in data["entries"] if entry]
        if not entries:
            raise ValueError("找不到符合的歌曲。")
        return entries[0]
    return data


async def resolve_track(
    query: str, requester_id: int, requester_name: str
) -> Track:
    """Resolve a URL or search keyword into a playable track."""
    target = query if is_url(query) else f"ytsearch1:{query}"
    loop = asyncio.get_running_loop()
    data = await asyncio.wait_for(
        loop.run_in_executor(None, lambda: _extract(target)), timeout=45
    )
    entry = _first_entry(data)
    webpage_url = entry.get("webpage_url") or entry.get("url")
    if not webpage_url:
        raise ValueError("找不到可播放的網址。")
    return Track(
        title=entry.get("title") or "未知歌曲",
        webpage_url=webpage_url,
        duration=entry.get("duration"),
        requester_id=requester_id,
        requester_name=requester_name,
    )


async def search_tracks(
    query: str, requester_id: int, requester_name: str, limit: int = 5
) -> list[Track]:
    """Search YouTube and return a short result list."""
    loop = asyncio.get_running_loop()
    data = await asyncio.wait_for(
        loop.run_in_executor(
            None, lambda: _extract(f"ytsearch{limit}:{query}", flat=True)
        ),
        timeout=45,
    )
    entries = data.get("entries") or []
    tracks: list[Track] = []
    for entry in entries:
        if not entry:
            continue
        webpage_url = entry.get("webpage_url") or entry.get("url")
        if not webpage_url:
            video_id = entry.get("id")
            if video_id:
                webpage_url = f"https://www.youtube.com/watch?v={video_id}"
        if not webpage_url:
            continue
        tracks.append(
            Track(
                title=entry.get("title") or "未知歌曲",
                webpage_url=webpage_url,
                duration=entry.get("duration"),
                requester_id=requester_id,
                requester_name=requester_name,
            )
        )
    return tracks[:limit]


class YTDLSource(discord.PCMVolumeTransformer):
    """A PCM audio source backed by yt-dlp and FFmpeg."""

    def __init__(
        self, source: discord.AudioSource, *, data: dict, volume: float = 0.5
    ) -> None:
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title", "未知歌曲")
        self.webpage_url = data.get("webpage_url", "")

    @classmethod
    async def create(cls, track: Track, volume: float = 0.5) -> "YTDLSource":
        loop = asyncio.get_running_loop()
        data = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _extract(track.webpage_url)),
            timeout=60,
        )
        data = _first_entry(data)
        stream_url = data.get("url")
        if not stream_url:
            raise ValueError("YouTube 沒有回傳可用的音訊串流。")

        kwargs = {
            "before_options": FFMPEG_BEFORE_OPTIONS,
            "options": FFMPEG_OPTIONS,
        }
        if FFMPEG_PATH:
            kwargs["executable"] = FFMPEG_PATH

        audio = discord.FFmpegPCMAudio(stream_url, **kwargs)
        return cls(audio, data=data, volume=volume)
