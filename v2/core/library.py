"""Read-only access to the migrated song library, plus unavailable marking."""

from __future__ import annotations

import asyncio
import sqlite3

from config import MUSIC_DB_PATH
from core.youtube import Track


def _connect() -> sqlite3.Connection:
    if not MUSIC_DB_PATH.exists():
        raise FileNotFoundError("music.db 不存在，請先執行歌庫遷移。")
    return sqlite3.connect(f"file:{MUSIC_DB_PATH.as_posix()}?mode=ro", uri=True)


def _random_tracks_sync(
    count: int, requester_id: int, requester_name: str
) -> list[Track]:
    connection = _connect()
    try:
        rows = connection.execute(
            """
            SELECT title, url FROM songs
            WHERE is_available = 1
            ORDER BY RANDOM() LIMIT ?
            """,
            (count,),
        ).fetchall()
    finally:
        connection.close()
    return [
        Track(
            title=(title or "").strip() or "未知歌曲",
            webpage_url=url,
            duration=None,
            requester_id=requester_id,
            requester_name=requester_name,
        )
        for title, url in rows
        if url
    ]


def _count_sync() -> int:
    connection = _connect()
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM songs WHERE is_available = 1"
            ).fetchone()[0]
        )
    finally:
        connection.close()


def _mark_unavailable_sync(url: str) -> None:
    connection = sqlite3.connect(MUSIC_DB_PATH, timeout=10)
    try:
        connection.execute(
            "UPDATE songs SET is_available = 0 WHERE url = ?", (url,)
        )
        connection.commit()
    finally:
        connection.close()


async def random_tracks(
    count: int, requester_id: int, requester_name: str
) -> list[Track]:
    return await asyncio.to_thread(
        _random_tracks_sync, count, requester_id, requester_name
    )


async def song_count() -> int:
    return await asyncio.to_thread(_count_sync)


async def mark_unavailable(url: str) -> None:
    await asyncio.to_thread(_mark_unavailable_sync, url)
