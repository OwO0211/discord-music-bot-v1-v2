# v1 vs v2 — Full Comparison

This document answers three questions: **what changed, why I rewrote it, and what was kept.**

---

## 1. Short summary

- **v1**: a hobby project that grew fast. Every idea went into one file. It worked, it was fun, it had many features, but a small change could break something far away.
- **v2**: I took the only part I used every day (music) and rewrote it. The result has modules, config, logging, and small tools. The cost: the other 90% of the features are gone.

---

## 2. Size and structure

| Metric | v1 | v2 |
| --- | --- | --- |
| Main file | 5,159 lines (`main.py`) | 104 lines (`bot.py`) |
| Python files | 12 (mostly snapshots of the same file) | 10 (one job each) |
| Total code | 5,159 lines + ~3,700 lines of snapshots | 1,729 lines |
| Slash commands | 73 | 12 |
| Database tables | 12 | 1 |
| Dependencies | discord.py, yt-dlp, **transformers + torch**, Pillow, OpenCC, BeautifulSoup, requests, pytz, aiohttp | discord.py, yt-dlp, PyNaCl, davey, python-dotenv |
| Install size | Several GB (includes a 7B model) | Tens of MB |

### Structure

```text
v1 (one file)                       v2 (modules)
─────────────────────────           ─────────────────────────
main.py 5,159 lines                 bot.py          entry point, events, sync
├─ music (global players dict)      config.py       config + logging
├─ AI role-play (7B model)          cogs/music.py   12 slash commands
├─ UNO / Werewolf                   core/player.py  per-guild queue
├─ marriage / names                 core/panel.py   control panel buttons
├─ announcements / voting           core/library.py song DB queries
├─ translate / quote / riddle       core/youtube.py yt-dlp search and stream
└─ joke commands                    tools/          import and manage
```

---

## 3. Features

| Feature | v1 | v2 | Note |
| --- | --- | --- | --- |
| Play / search | ✅ `/play` `/searchplay` | ✅ `/play` `/search` | v2 streams, v1 downloaded mp3 |
| Random play | ✅ `/randomplay` | ✅ `/randomplay` | v1 used `marriage.db`, v2 uses `music.db` |
| Queue / skip / pause / volume | ✅ | ✅ | v2 adds a button panel |
| Lyrics | ✅ `/lyrics` (NetEase API) | ❌ | Easy to add back |
| Playlist view | ⚠️ `/list` (text file) | ✅ `/library` + CLI tool | v2 library can be backed up, disabled, deleted |
| Autoplay | ✅ `/autoplay` | ❌ | |
| AI role-play chat | ✅ local `deepseek-llm-7b-chat` | ❌ | Needs a GPU and 14 GB |
| UNO | ✅ | ❌ | |
| Werewolf | ✅ | ❌ | |
| Marriage / children | ✅ (5 marriages, 9 children) | ❌ | Data still in `marriage.db`, not exported |
| Rename / swap names | ✅ | ❌ | |
| Announcements | ✅ | ❌ | |
| Voting | ✅ | ❌ | |
| Quote image cards | ✅ `/quote` (Pillow) | ❌ | |
| Chinese conversion | ✅ `/translate` (OpenCC) | ❌ | |
| History / hitokoto / riddles / RPS | ✅ | ❌ | |
| Joke commands | ✅ `/tp` `/kill` `/summon` | ❌ | |
| Admin commands | ✅ hard-coded user IDs | ❌ | v2 has none |

The v2 trade-off is simple: keep "someone is listening to music", drop the rest. That is a common way to rewrite a project. Make the core solid first, then add things back one by one.

---

## 4. Music playback: the biggest change

### v1: global dict + download

```python
players = {}          # guild_id -> state, used in 12 places
autoplay_running = False
autoplay_task = None

ytdl_format_options = {
    'format': 'bestaudio/best',
    'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}],
    ...
}
```

- Every song was **downloaded to mp3 first**, then played.
- Playback state lived in a global dict. Any function could change it.
- Errors were printed with `print()` (124 times). Users and the developer saw the same raw text.

### v2: one object per server + stream

```python
class GuildPlayer:            # core/player.py
    def __init__(self, bot, guild_id, ...): ...
    async def connect(self, channel): ...
    def enqueue(self, track) -> int: ...
    async def _loop(self): ...          # queue, idle timeout, cleanup
    async def _idle_disconnect(self): ...

class MusicManager:           # one GuildPlayer per guild
    def get(self, ...) -> GuildPlayer: ...
    async def destroy_all(self): ...
```

- `core/youtube.py` streams through `YTDLSource`. No files on disk.
- After `IDLE_DISCONNECT_SECONDS` (default 60) with no songs, the bot leaves the voice channel.
- A **control panel** appears with buttons: pause, skip, stop, queue, volume −/+, random 5.
- Blocking yt-dlp and SQLite calls go to `asyncio.to_thread`, so the event loop stays free.
- Everything uses `logging` (39 calls) and writes to `logs/bot.log`.

---

## 5. Data layer

| | v1 `marriage.db` | v2 `data/music.db` |
| --- | --- | --- |
| Tables | 12 | 1 |
| Content | `songs` (1,628), `song_ratings` (1,851), `marriages` (5), `children` (9), `user_names` (3), `announcements`, `channel_names` (6), `exclude_keywords` (44), `include_keywords` (96), `keyword_song_mapping` (12), `rpg_players` | `songs` (1,522) |
| Purpose | Music, social, and games in one file | Song library only |
| Access | Each function opened its own `sqlite3.connect('marriage.db')` | `core/library.py` opens read-only. The only write is "mark unavailable". |
| Maintenance | None | `tools/manage_music_db.py`: inspect, disable, delete, with automatic backup |

v2 ships an import tool. It copies only the `songs` table:

```powershell
.\.venv\Scripts\python.exe tools\import_legacy_songs.py --source ..\legacy\marriage.db
```

Ratings, marriages, and keyword tables are **not** copied, because v2 has no features that use them.

---

## 6. Config and secrets

### v1: hard-coded

```python
TOKEN = os.getenv('DISCORD_BOT_TOKEN')   # read, but never used
...
bot.run('<token was hard-coded here>')   # never do this
```

- The token was written directly in `bot.run()`, and the **same token appeared in 6 files**.
- `GUILD_ID` was defined twice with different values (L1330 and L4873). Python uses the last one, so every "only this server" check before it stopped working.
- Permission checks were inline: `if interaction.user.id != 100000000000000003`.

### v2: central and out of git

```python
# config.py
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
IDLE_DISCONNECT_SECONDS = max(10, int(os.getenv("IDLE_DISCONNECT_SECONDS", "60")))
MUSIC_DB_PATH = DATA_DIR / "music.db"
```

- `.env` is loaded by `config.py` with `python-dotenv` and excluded by `.gitignore`.
- If the token is missing, `bot.py` prints a clear message and exits. It does not crash.
- No hard-coded server ID. The bot works on any server.

---

## 7. Code quality: the evidence

| Problem | v1 | v2 |
| --- | --- | --- |
| Duplicate handlers | `on_ready` defined 3 times (L4925, L4974, L5102). `on_message` defined 2 times (L3854, L4929). The last one wins, so earlier code never ran. | One implementation each |
| Global state | `players`, `autoplay_running`, `autoplay_task`, `current_target` | State lives in `GuildPlayer`. `MusicManager` controls its life cycle. |
| Error handling | 124 `print()`, 0 loggers. Many `except Exception: pass`. | 39 `logging` calls, levels INFO/WARNING/ERROR, written to a file |
| Type hints | Almost none | `from __future__ import annotations`, type hints, `@dataclass` |
| Blocking calls | Sync yt-dlp and `requests` on the event loop | Wrapped with `asyncio.to_thread` |
| Dependencies | No `requirements.txt` | `requirements.txt` + `setup.bat` |
| Config | Constants spread across the file | `config.py` |
| Operations | Manual window. Closing it stops the bot. | `EileenControl.ps1`, PID file, background start, logs |

---

## 8. Why there are `save*.py` and `ultimatesave.py`

These are **manual version control** from before I used git. When you have no commits, you copy the file.

| File | Lines | Stage |
| --- | --- | --- |
| `save3.py` | 341 | Early. Music just started working. |
| `save4.py` | 499 | More commands added |
| `save.py` | 1,085 | Fast growth |
| `save2.py` | 1,092 | Almost the same as `save.py` |
| `ultimatesave.py` | 690 | Another branch |
| `main.py` | 5,159 | Final version, everything inside |

I kept them because some parts (for example, a few UI views) were later overwritten in the main file. **Do not run them.** They are history, not code.

---

## 9. Moving v1 features back to v2

Suggested order, easiest first. Each step can ship alone.

1. **`/lyrics`**: one API call. Add a new file in `cogs/`.
2. **`/autoplay`**: extend `core/randomplay.py`.
3. **Announcements**: needs a new table and permission checks. Start `core/announcements.py`.
4. **Voting / image cards**: image cards need fonts and Chinese text handling. Use `core/image_card.py`.
5. **Social features (marriage, names)**: the most data and the tightest coupling. Move those tables to a separate DB first.
6. **AI role-play**: call an external API, or run a local Ollama server. Do not load a 7B model in the main process again.
7. **UNO / Werewolf**: these are separate state machines. Make them separate cogs, or even separate services.

---

## 10. Conclusion

v1 proved one thing: **one person can build 73 commands in a single file.**

v2 proved another: **12 commands that work are worth more than 73 that half work.**

If this project keeps growing, v2 has the structure for it. If you only want the memory, v1's `main.py` is a good souvenir.
