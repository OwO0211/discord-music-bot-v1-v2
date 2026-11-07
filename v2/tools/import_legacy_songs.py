"""Migrate only the legacy `songs` table into the v2 music library.

The legacy database is opened read-only. Ratings, marriages and other social
tables are intentionally not copied.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_DIR.parent / "legacy" / "marriage.db"
DEFAULT_TARGET = PROJECT_DIR / "data" / "music.db"


def migrate(source: Path, target: Path) -> tuple[int, int]:
    if not source.exists():
        raise FileNotFoundError(f"找不到舊資料庫：{source}")

    source_connection = sqlite3.connect(
        f"file:{source.as_posix()}?mode=ro", uri=True
    )
    try:
        source_connection.row_factory = sqlite3.Row
        rows = source_connection.execute(
            "SELECT url, title FROM songs WHERE url IS NOT NULL AND url != ''"
        ).fetchall()
    finally:
        source_connection.close()

    target.parent.mkdir(parents=True, exist_ok=True)
    target_connection = sqlite3.connect(target)
    try:
        target_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS songs (
                song_id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                is_available INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        columns = [
            row[1]
            for row in target_connection.execute("PRAGMA table_info(songs)")
        ]
        if "is_available" not in columns:
            target_connection.execute(
                "ALTER TABLE songs ADD COLUMN is_available INTEGER NOT NULL DEFAULT 1"
            )
        before = target_connection.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
        target_connection.executemany(
            "INSERT OR IGNORE INTO songs (url, title) VALUES (?, ?)",
            [(row["url"], row["title"] or "未知歌曲") for row in rows],
        )
        target_connection.commit()
        after = target_connection.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    finally:
        target_connection.close()
    return before, after


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="舊版 marriage.db 路徑（預設：repo 根目錄的 legacy/marriage.db）",
    )
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args()

    before, after = migrate(args.source, args.target)
    print(f"target: {args.target}")
    print(f"before: {before}")
    print(f"after:  {after}")
    print(f"added:  {after - before}")


if __name__ == "__main__":
    main()
