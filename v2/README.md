# Discord Music Bot v2

A maintainable rewrite of the old `discordbot`, starting with the part that gets used most: **music playback**, turned into clean, modular code.

Once the dependencies are installed and your own token is in `.env`, the bot logs in and syncs its 12 slash commands.

The old version (v1) source is kept in `legacy/` at the repo root, for reference only.

> The v1 source once had the bot token hard-coded in the file. Never upload or share a file that contains a token; if you want to keep using an old bot, reset its token in the Discord Developer Portal first.

## Commands

- `/play <URL or keywords>`: play from YouTube, or search and play the first match
- `/search <keywords>`: list the first 5 results, add one from a select menu
- `/randomplay <1-5>`: pick random songs from the old library
- `/library`: show how many songs the library has
- `/skip`: skip the current song
- `/pause` / `/resume`: pause and resume
- `/queue`: show the queue
- `/nowplaying`: show what is playing
- `/volume <0-200>`: change the volume
- `/join`: join your current voice channel
- `/stop`: stop and leave the voice channel

## Playback panel

Whenever a new song starts, Eileen posts a fresh control panel in the text channel and deletes the previous song's panel, so the channel does not fill up. The panel has:

- **Pause / Resume**
- **Skip**
- **Stop**
- **Queue**
- **Volume - / Volume +**
- **Random 5**: the same as `/randomplay 5`; it validates that the songs are playable before adding them

The panel shows the current song, its length, who requested it, the queue length, the volume and the playback state; it updates as soon as a button is pressed. The buttons only affect that one player, and the panel is replaced when the song changes.

The bot's Discord presence is set to **Do Not Disturb**, with a custom status of "Do Not Disturb".

### Silent messages

Every command reply and the music panel is sent with `silent=True`, so Discord adds the `SUPPRESS_NOTIFICATIONS` flag and tries not to notify anyone.

Notes:

- This is **not a private message**; channel members can still read it.
- Whether notifications are really suppressed still depends on each person's Discord notification settings.
- Editing an existing panel message cannot re-apply the silent flag; a new song's new panel is sent silent again.
- If you want a reply only the caller can see, that is `ephemeral=True`, which is different from `silent=True`.

## Library and `/randomplay`

The old `marriage.db` only had its `songs` table migrated; ratings, marriage, RPG and other data were not copied. The new library lives at:

`data\music.db`

It currently holds **1,535 unique songs** (the old table had 1,628 rows, and duplicate URLs were merged), with songs already marked unplayable excluded automatically; about **1,533** are usable right now.

`/randomplay 3` picks 3 songs straight from the `songs` table and **validates each one before playing**; private, deleted or region-locked videos are skipped and marked unavailable, so a wall of yt-dlp errors no longer lands in the channel. `/library` shows the current usable count.

To re-import from the old database:

```powershell
.\.venv\Scripts\python.exe tools\import_legacy_songs.py
```

The default source is `legacy\marriage.db` at the repo root, and you can point elsewhere with `--source`. The import uses `INSERT OR IGNORE`, so running it twice does not create duplicates.

## Voice channel lifecycle

- After the queue finishes, the bot waits **60 seconds** by default; with no new song it leaves the voice channel, so a long idle connection does not get it kicked by Discord.
- The wait time is configurable in `.env`:

  ```dotenv
  IDLE_DISCONNECT_SECONDS=60
  ```

- If the bot is kicked mid-song or the voice connection drops, the next `/play` reconnects; no restart needed.
- A failed voice connection is retried 3 times; if it still fails, the bot shows "Discord voice connection timed out" instead of a blank error.
- The player has a watchdog: fetching the audio source waits at most 60 seconds; a song that is still playing past "duration + 30 seconds" is skipped; deleting, sending or editing a panel times out after 10-15 seconds, so one stuck call cannot freeze the whole queue.
- A manual `/stop` stops playback and leaves immediately.
- Resuming the previous queue after the bot was kicked is not supported; just `/play` or `/randomplay` again.

### Library tool

Double-click:

```text
music-db-tool.bat
```

Options:

1. Show statistics
2. Search songs
3. List songs
4. Disable a song
5. Enable a song
6. Delete a song (by ID)
7. Purge all disabled songs
0. Exit

Before a delete or purge, it backs up to:

`data\backups\music-YYYYmmdd-HHMMSS.db`

You can also use the command line:

```powershell
# stats
.\.venv\Scripts\python.exe tools\manage_music_db.py stats
.\.venv\Scripts\python.exe tools\manage_music_db.py search "keyword"

# preview only, nothing is deleted
.\.venv\Scripts\python.exe tools\manage_music_db.py delete --id 123

# actually delete
.\.venv\Scripts\python.exe tools\manage_music_db.py delete --id 123 --yes
```

Deleting only affects future `/randomplay` picks; a song that is playing or already queued is not interrupted. The old `marriage.db` is never modified.

## Project layout

```text
discordbot-v2/
├─ bot.py                 # entry point: create the bot, load cogs, sync commands
├─ config.py              # environment variables, paths, logging
├─ cogs/
│  └─ music.py            # all slash commands and the search menu
├─ core/
│  ├─ player.py           # per-server queue and playback lifecycle
│  ├─ library.py          # read-only queries against the old songs table
│  └─ youtube.py          # yt-dlp search/resolve and the FFmpeg audio source
├─ data/
│  ├─ downloads/          # reserved for a future download mode
│  └─ music.db            # songs table migrated from the old marriage.db
├─ tools/
│  └─ import_legacy_songs.py
├─ logs/                  # bot.log
├─ legacy/                # v1 source, for reference only
├─ .env.example
├─ requirements.txt
├─ setup.bat
└─ start.bat
```

## Install

### 1. Python and FFmpeg

- Python 3.10 or newer (3.12 recommended)
- FFmpeg: make sure it is on PATH, or fill in `FFMPEG_PATH` in `.env` later

### 2. Create the virtual environment and install dependencies

Double-click `setup.bat`, or run it by hand:

```powershell
cd discordbot-v2
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 3. Create the Discord application

1. Go to https://discord.com/developers/applications
2. Create an Application, then a Bot
3. Copy the new bot token
4. Turn on **Server Members Intent**
5. The invite needs these permissions:
   - View Channels
   - Send Messages
   - Embed Links
   - Read Message History
   - Connect
   - Speak
   - Use Application Commands

> This version only uses slash commands, so Message Content Intent is not needed.

### 4. Create `.env`

Copy `.env.example` to `.env` and fill it in:

```dotenv
DISCORD_BOT_TOKEN=your new token
FFMPEG_PATH=C:\ffmpeg\bin\ffmpeg.exe
LOG_LEVEL=INFO
```

If `FFMPEG_PATH` is left empty, `ffmpeg` from PATH is used.

### 5. Start

Double-click `start.bat`, or run:

```powershell
.\.venv\Scripts\python.exe bot.py
```

The first start syncs the slash commands automatically. If Discord does not show them, press `Ctrl + R` to refresh the client.

## Desktop console

You can create a desktop shortcut to `EileenControl.ps1` (named "Eileen Switch", for example); double-clicking it gives:

```text
1. Start Eileen
2. Stop Eileen
3. Restart
4. Show status
5. Open the log
0. Exit
```

This console starts the bot through the Windows scheduled task `Eileen`; there is **no run-at-login setting**. Closing the console window does not stop the bot; choose `2` to stop it.

You can also run it directly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\EileenControl.ps1
```

The PID is written to `eileen.pid` and the log is `logs\bot.log`.

## Migration status

Only the music features have been fully ported. The old version still has a lot of other features in the original `main (2).py`:

- Avatar lookup
- Polls, announcements, team assignment
- Justice Online in-game name database
- UNO, Werewolf, rock-paper-scissors
- Daily quote, riddles, today in history
- Lyrics, ratings, random play
- Other community and moderation commands

None of these were ported directly, because the old file is a single 5,000+ line module with deep interdependencies; forcing it apart breaks things fast. Migrate one feature at a time, starting with the ones you actually use.

## Troubleshooting

### FFmpeg not found

```text
FFmpeg was not found. Install FFmpeg, or set FFMPEG_PATH in .env.
```

After downloading FFmpeg, put the full path to `bin\ffmpeg.exe` in `.env`.

### The bot joins voice but there is no sound

- Check that the bot has **Connect** and **Speak**
- Check that FFmpeg runs
- Update `yt-dlp`:

```powershell
.\.venv\Scripts\python.exe -m pip install -U yt-dlp
```

### Search fails

YouTube sometimes blocks requests or asks for a login. Update `yt-dlp` and try again; if needed, use the video URL instead.

### The token is invalid

Go to Discord Developer Portal, then Bot, then Reset Token, generate a new one, update `.env` and restart. Do **not** write the token back into the code.

## Security

- `.env` is in `.gitignore`
- The code contains no token
- Never upload `.env`, `logs/`, or the old `main (2).py` to GitHub
