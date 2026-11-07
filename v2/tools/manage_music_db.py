"""Manage the Eileen music library: inspect, disable, or delete songs.

All destructive operations create a SQLite backup first. The old
``marriage.db`` is never touched.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_DIR / "data" / "music.db"


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


def backup_database(path: Path) -> Path:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"music-{time.strftime('%Y%m%d-%H%M%S')}.db"
    source = sqlite3.connect(path)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return destination


def print_song(row: sqlite3.Row) -> None:
    status = "可用" if row["is_available"] else "停用"
    print(f"[{row['song_id']:>5}] {status} | {row['title']}")
    print(f"        {row['url']}")


def find_songs(
    connection: sqlite3.Connection,
    *,
    song_id: int | None = None,
    url: str | None = None,
    keyword: str | None = None,
) -> list[sqlite3.Row]:
    if song_id is not None:
        return connection.execute(
            "SELECT * FROM songs WHERE song_id = ?", (song_id,)
        ).fetchall()
    if url:
        return connection.execute(
            "SELECT * FROM songs WHERE url = ?", (url,)
        ).fetchall()
    if keyword:
        like = f"%{keyword}%"
        return connection.execute(
            "SELECT * FROM songs WHERE title LIKE ? OR url LIKE ? ORDER BY song_id",
            (like, like),
        ).fetchall()
    return []


def print_stats(path: Path) -> None:
    with connect(path) as connection:
        total = connection.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
        available = connection.execute(
            "SELECT COUNT(*) FROM songs WHERE is_available = 1"
        ).fetchone()[0]
    print(f"資料庫：{path}")
    print(f"總歌曲：{total}")
    print(f"可用：{available}")
    print(f"停用／不可播放：{total - available}")


def list_songs(path: Path, limit: int = 30) -> None:
    with connect(path) as connection:
        rows = connection.execute(
            "SELECT * FROM songs ORDER BY song_id LIMIT ?", (limit,)
        ).fetchall()
    for row in rows:
        print_song(row)
    print(f"（只顯示前 {len(rows)} 首）")


def search_songs(path: Path, keyword: str, limit: int = 30) -> None:
    with connect(path) as connection:
        like = f"%{keyword}%"
        rows = connection.execute(
            """
            SELECT * FROM songs
            WHERE title LIKE ? OR url LIKE ?
            ORDER BY song_id LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()
    for row in rows:
        print_song(row)
    print(f"找到 {len(rows)} 首（最多顯示 {limit} 首）")


def set_enabled(path: Path, song_id: int, enabled: bool) -> None:
    with connect(path) as connection:
        row = connection.execute(
            "SELECT * FROM songs WHERE song_id = ?", (song_id,)
        ).fetchone()
        if row is None:
            print(f"找不到 ID {song_id}")
            return
        connection.execute(
            "UPDATE songs SET is_available = ? WHERE song_id = ?",
            (1 if enabled else 0, song_id),
        )
        connection.commit()
    print(f"已{'啟用' if enabled else '停用'} [{song_id}] {row['title']}")


def delete_songs(
    path: Path,
    *,
    song_id: int | None = None,
    url: str | None = None,
    keyword: str | None = None,
    yes: bool = False,
) -> None:
    with connect(path) as connection:
        rows = find_songs(
            connection, song_id=song_id, url=url, keyword=keyword
        )
    if not rows:
        print("找不到符合的歌曲。")
        return

    print(f"符合 {len(rows)} 首：")
    for row in rows[:20]:
        print_song(row)
    if len(rows) > 20:
        print(f"... 另外 {len(rows) - 20} 首")

    if not yes:
        print("\n目前是預覽模式，沒有刪除。")
        print("確定要刪除請加 --yes，或在互動選單中確認。")
        return

    backup = backup_database(path)
    print(f"已備份：{backup}")
    with connect(path) as connection:
        placeholders = ",".join("?" for _ in rows)
        connection.execute(
            f"DELETE FROM songs WHERE song_id IN ({placeholders})",
            [row["song_id"] for row in rows],
        )
        connection.commit()
    print(f"已刪除 {len(rows)} 首。")


def purge_unavailable(path: Path, yes: bool = False) -> None:
    with connect(path) as connection:
        rows = connection.execute(
            "SELECT * FROM songs WHERE is_available = 0"
        ).fetchall()
    if not rows:
        print("沒有停用中的歌曲。")
        return
    print(f"停用中的歌曲：{len(rows)} 首")
    for row in rows[:10]:
        print_song(row)
    if len(rows) > 10:
        print(f"... 另外 {len(rows) - 10} 首")
    if not yes:
        print("\n目前是預覽模式，沒有刪除。加 --yes 才會真的清除。")
        return

    backup = backup_database(path)
    print(f"已備份：{backup}")
    with connect(path) as connection:
        connection.execute("DELETE FROM songs WHERE is_available = 0")
        connection.commit()
    print(f"已清除 {len(rows)} 首。")


def confirm(prompt: str) -> bool:
    return input(prompt).strip().lower() in {"y", "yes", "是", "確認"}


def interactive(path: Path) -> None:
    while True:
        print("\n===== Eileen 歌庫工具 =====")
        print("1. 查看統計")
        print("2. 搜尋歌曲")
        print("3. 列出歌曲")
        print("4. 停用歌曲")
        print("5. 啟用歌曲")
        print("6. 刪除歌曲")
        print("7. 清除所有停用歌曲")
        print("0. 離開")
        choice = input("請輸入選項：").strip()

        if choice == "1":
            print_stats(path)
        elif choice == "2":
            search_songs(path, input("關鍵字：").strip())
        elif choice == "3":
            list_songs(path)
        elif choice in {"4", "5"}:
            try:
                song_id = int(input("歌曲 ID：").strip())
            except ValueError:
                print("ID 必須是數字。")
                continue
            set_enabled(path, song_id, choice == "5")
        elif choice == "6":
            try:
                song_id = int(input("要刪除的歌曲 ID：").strip())
            except ValueError:
                print("ID 必須是數字。")
                continue
            if confirm("確定刪除？(y/N) "):
                delete_songs(path, song_id=song_id, yes=True)
        elif choice == "7":
            if confirm("確定清除所有停用歌曲？(y/N) "):
                purge_unavailable(path, yes=True)
        elif choice == "0":
            return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Eileen music database tool")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("stats", help="show counts")
    sub.add_parser("list", help="list songs").add_argument(
        "--limit", type=int, default=30
    )

    search = sub.add_parser("search", help="search songs")
    search.add_argument("keyword")
    search.add_argument("--limit", type=int, default=30)

    for name in ("enable", "disable"):
        command = sub.add_parser(name, help=f"{name} one song")
        command.add_argument("--id", type=int, required=True)

    delete = sub.add_parser("delete", help="delete songs")
    group = delete.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", type=int)
    group.add_argument("--url")
    group.add_argument("--keyword")
    delete.add_argument("--yes", action="store_true")

    purge = sub.add_parser("purge-unavailable", help="delete disabled songs")
    purge.add_argument("--yes", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    path = args.db.resolve()
    if not path.exists():
        print(f"找不到資料庫：{path}")
        return 1

    if not args.command:
        interactive(path)
        return 0

    if args.command == "stats":
        print_stats(path)
    elif args.command == "list":
        list_songs(path, args.limit)
    elif args.command == "search":
        search_songs(path, args.keyword, args.limit)
    elif args.command == "enable":
        set_enabled(path, args.id, True)
    elif args.command == "disable":
        set_enabled(path, args.id, False)
    elif args.command == "delete":
        delete_songs(
            path,
            song_id=args.id,
            url=args.url,
            keyword=args.keyword,
            yes=args.yes,
        )
    elif args.command == "purge-unavailable":
        purge_unavailable(path, yes=args.yes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
