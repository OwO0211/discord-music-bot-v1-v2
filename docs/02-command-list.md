# Command List

v1 has 73 commands. v2 has 12. Most v1 commands had no description, so the text below comes from reading the code.

---

## v2 (12 commands, all slash commands)

| Command | Description |
| --- | --- |
| `/play <song or URL>` | Play a YouTube song, or search a keyword and play the first result |
| `/search <keyword>` | Search YouTube and choose from the first 5 results |
| `/randomplay <1-5>` | Queue 1–5 random songs from the library |
| `/library` | Show how many songs are in the library |
| `/join` | Join your current voice channel |
| `/pause` / `/resume` | Pause / continue |
| `/skip` | Skip the current song |
| `/stop` | Stop and leave the voice channel |
| `/queue` | Show the queue |
| `/nowplaying` | Show the current song |
| `/volume <0-200>` | Change the volume |

During playback, a **button panel** appears: pause, skip, stop, queue, volume −, volume +, random 5.

---

## v1 (73 commands, all slash commands)

### Music (18)

| Command | Description |
| --- | --- |
| `/play` | Play a song or playlist |
| `/searchplay` | Search, then pick a song |
| `/internalsearchplay` | Search inside the local library |
| `/autosearchplay` | Auto-search and keep playing |
| `/stopautosearchplay` | Stop auto-search |
| `/autoplay` / `/stopautoplay` | Autoplay on / off |
| `/randomplay` | Random song from the library |
| `/list` | Show the song list (reads the `list_music` text file) |
| `/lyrics` | Get lyrics from the NetEase API |
| `/pause` / `/resume` | Pause / continue |
| `/skip` | Skip |
| `/stop` | Stop and leave |
| `/stopafterthis` | Stop after the current song |
| `/reset` | Reset the player when the connection is stuck |
| `/111howtoplaymusic` | Usage help |
| `/add_music_announcement` | Add a music announcement |

### Announcements and voting (6)

| Command | Description |
| --- | --- |
| `/add_announcement` | Add an announcement |
| `/delete_announcement` | Delete an announcement |
| `/list_announcements` | List announcements |
| `/pin_announcement` | Pin an announcement |
| `/voteevent` | Start a vote |
| `/limited_vote` | Limited vote |

### Games (10)

| Command | Description |
| --- | --- |
| `/startuno` `/joinuno` `/uno` `/stopuno` | UNO: start, join, play, stop |
| `/startwolf` `/joinwolf` `/wolf` `/stopwolf` | Werewolf: start, join, act, stop |
| `/rps` | Rock-paper-scissors |
| `/riddleeng` | English riddle |

### Teams and social (9)

| Command | Description |
| --- | --- |
| `/team` | Split players into three teams by role |
| `/teamwork` | Advanced version: set leaders and members per role |
| `/check_roles` | List members who have no role yet |
| `/checkname` / `/myname` | Read / set your in-game name |
| `/checkspecific` | Read another member's in-game name |
| `/restnshname` | Reset one member's saved name (admin) |
| `/swap_names` | Randomly swap all server nicknames |
| `/reset_names` | Restore all nicknames |

### Avatars (3)

| Command | Description |
| --- | --- |
| `/avatar` | Show a user's avatar |
| `/avatarinserver` | Show the avatar as seen in this server |
| `/avatarserver` | Show the server icon |

### Fun and role-play (8)

| Command | Description |
| --- | --- |
| `/dsl` | Random quote |
| `/dslcallmama` | Send an image file |
| `/dslsingmiaomiao` | Send an audio file |
| `/simalol` | Small interactive game with a select menu |
| `/stepme` | Random reply from a fixed line list |
| `/fansi` | Admin: post many messages to one channel |
| `/quote` | Generate a quote image card with Pillow |
| `/onthisday` | "Today in history" |

### Tools (10)

| Command | Description |
| --- | --- |
| `/calculate` | Calculator |
| `/limited_calculate` | Calculator with a usage limit |
| `/countchar` | Count characters |
| `/translate` / `/translate_message` | Chinese conversion / translate a message |
| `/info` | Bot information |
| `/ranking` | Ranking by message count or rating |
| `/buildroom` | Create a temporary voice room |
| `/advisefrommdeveloper` | Send advice to the developer |
| `/yeyeye` | Admin: assign roles in bulk |

### Joke commands (3)

| Command | Description |
| --- | --- |
| `/tp <member> <x> <y> <z>` | Pretend to teleport a member (text only) |
| `/kill <member>` | Pretend to kill a member |
| `/summon <mob> <x> <y> <z>` | Pretend to summon a mob |

### Admin (6)

| Command | Description |
| --- | --- |
| `/deletemessage` | Delete a message |
| `/sendmessagein` | Send a message to a channel |
| `/sendmessageto` | Send a DM to a member |
| `/reload` | Reload modules |
| `/addexcludekeyword` | Add an excluded keyword |
| `/addincludekeyword` | Add an included keyword |

v1 checked permissions with a **hard-coded user ID whitelist**. v2 has no admin commands.
