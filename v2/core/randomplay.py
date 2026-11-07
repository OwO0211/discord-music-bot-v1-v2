"""Shared random-song collection used by slash command and control panel."""

from __future__ import annotations

import logging

from core.library import mark_unavailable, random_tracks
from core.youtube import Track, resolve_track

logger = logging.getLogger(__name__)

PERMANENT_ERROR_MARKERS = (
    "private video",
    "video unavailable",
    "has been removed",
    "deleted",
    "copyright",
    "not available",
)


def is_permanent_unavailable(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in PERMANENT_ERROR_MARKERS)


async def collect_random_playable(
    count: int, requester_id: int, requester_name: str
) -> list[Track]:
    """Pick random songs and return only tracks that resolve successfully."""
    collected: list[Track] = []
    attempts = 0
    while len(collected) < count and attempts < 6:
        batch = await random_tracks(
            max(count * 2, 3), requester_id, requester_name
        )
        if not batch:
            break
        for track in batch:
            try:
                resolved = await resolve_track(
                    track.webpage_url, requester_id, requester_name
                )
            except Exception as exc:
                logger.warning(
                    "跳過無法播放的隨機歌曲 %s: %s",
                    track.webpage_url,
                    exc,
                )
                if is_permanent_unavailable(exc):
                    await mark_unavailable(track.webpage_url)
                continue
            collected.append(resolved)
            if len(collected) >= count:
                break
        attempts += 1
    return collected
