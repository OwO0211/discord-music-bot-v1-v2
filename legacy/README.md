# v1 (legacy)

The first generation: **one file, 5,159 lines, 73 slash commands**, grown from late 2024 to April 2025.

It was not written in one night. It grew feature by feature, so the file still shows the history: duplicate event handlers, two different server IDs, and five saved copies.

---

## Files

| File | Lines | Content |
| --- | --- | --- |
| `main.py` | 5,159 | **Final main program.** Music, AI role-play, UNO, Werewolf, marriage, announcements, voting, image cards, translation, joke commands |
| `ultimatesave.py` | 690 | A snapshot from another branch. Fewer features. |
| `save.py` / `save2.py` | 1,085 / 1,092 | Snapshots from the fast-growth period (almost identical) |
| `save3.py` / `save4.py` | 341 / 499 | Earlier snapshots |
| `cmddiscord.py` | 72 | The first experiment: change avatar and nickname |
| `adadad.py` / `adasd.py` / `hit.py` | 39 / 35 / 49 | Small tests |
| `sql.py` | 30 | SQLite helper snippets |
| `music.py` | 0 | Empty file (I planned to split modules but never did) |
| `list_music` | — | Early song list, plain text |

> `save*.py` are history only. **Do not run them.** Read `main.py` for features.

> **All Discord IDs in this folder were replaced with placeholder numbers** (`1000...0001`, etc.) before publishing. Server IDs, channel IDs, and the admin user ID are no longer the real values.

---

## How to run

```powershell
cd legacy
pip install discord.py yt-dlp opencc-python-reimplemented pillow ^
            beautifulsoup4 requests pytz aiohttp transformers torch
$env:DISCORD_BOT_TOKEN = "your token"
python main.py
```

The token was removed from the code. It now comes from the `DISCORD_BOT_TOKEN` environment variable.

---

## Known issues (left as they were)

1. **Tokens were hard-coded** and appeared in six files. If the same token is still active, reset it in the Discord Developer Portal now.
2. **`on_ready` is defined three times and `on_message` twice** (L4925/L4974/L5102 and L3854/L4929). The last one wins, so the earlier code never ran.
3. **`GUILD_ID` is defined twice with different values** (L1330 and L4873). Python uses the second one, so earlier "this server only" checks stopped working.
4. **It needs a 7B model.** `main.py` loads `deepseek-ai/deepseek-llm-7b-chat` at startup. Without a GPU this is very slow. If you only want music, comment out everything after the AI model marker (`# === 載入 AI 模型 ===`, "load AI model") to the end of the file.
5. **Data files are not in the repo.** `marriage.db`, `cookies.json`, `picture/`, and downloaded media are excluded (privacy and size). Bring your own to reproduce v1 fully.
6. **Hard-coded admin user ID.** Several commands check `if interaction.user.id != ...`.

---

## What it taught me

- Global state (`players`, `autoplay_running`) is fast to write, but it makes "why did server A affect server B" bugs very hard to trace.
- Without logging, you debug with `print` and guesses.
- Of 73 commands, fewer than 15 were used every day.

That is why v2 exists. See [`../docs/01-version-comparison.md`](../docs/01-version-comparison.md).
