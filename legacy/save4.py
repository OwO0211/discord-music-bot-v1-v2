import discord
import yt_dlp
import asyncio
from discord.ext import commands
import random
import sqlite3
from datetime import datetime, timedelta

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='/', intents=intents)

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}


ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

class MusicPlayer:
    def __init__(self, interaction: discord.Interaction):
        self.interaction = interaction
        self.queue = asyncio.Queue()
        self.next = asyncio.Event()
        self.current = None
        self.voice_client = None
        self.bot = interaction.client
        self.current_user = None

    async def player_loop(self):
        while True:
            self.next.clear()
            try:
                self.current = await asyncio.wait_for(self.queue.get(), timeout=300)
            except asyncio.TimeoutError:
                return self.bot.loop.create_task(self.stop())

            if not self.interaction.guild.voice_client:
                self.voice_client = await self.interaction.user.voice.channel.connect()

            self.interaction.guild.voice_client.play(self.current, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            await self.next.wait()

            self.current.cleanup()

    async def play(self, url):
        if self.interaction.user.voice is None:
            print("用戶不在任何語音頻道中。")
            return "你必須加入語音頻道。"
        
        if not self.interaction.guild.voice_client:
            self.voice_client = await self.interaction.user.voice.channel.connect()
        else:
            self.voice_client = self.interaction.guild.voice_client
        
        # 检查 URL 是否在数据库中
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()
        c.execute('SELECT * FROM songs WHERE url = ?', (url,))
        song_exists = c.fetchone()

        if not song_exists:
            # 如果 URL 不存在，则插入到数据库
            c.execute('INSERT INTO songs (url, title) VALUES (?, ?)', (url, "未知標題"))  # 这里可以根据需要设置标题
            conn.commit()

        conn.close()

        player = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
        await self.queue.put(player)
        
        if self.current is None:
            self.current_user = self.interaction.user
            self.bot.loop.create_task(self.player_loop())
            return f"正在播放：{player.title}"
        else:
            return f"已將 {player.title} 添加到播放列表"

    async def stop(self):
        if self.interaction.guild.voice_client:
            await self.interaction.guild.voice_client.disconnect()
            await self.interaction.response.send_message("已停止音樂。")
            await self.list()  # 刷新播放列表

    async def pause(self):
        if self.interaction.guild.voice_client and self.interaction.guild.voice_client.is_playing():
            self.interaction.guild.voice_client.pause()

    async def resume(self):
        if self.interaction.guild.voice_client and self.interaction.guild.voice_client.is_paused():
            self.interaction.guild.voice_client.resume()

    async def info(self):
        if self.current:
            user_name = self.current_user.name if self.current_user else "未知用戶"
            await self.interaction.response.send_message(f"現在播放：{self.current.title} 由 {user_name} 播放")
        else:
            await self.interaction.response.send_message("當前沒有正在播放的音樂。")

    async def list(self):
        if self.queue.empty() and self.current is None:
            await self.interaction.response.send_message("播放列表是空的。")
        else:
            message = "播放列表中有以下歌曲：\n"
            if self.current:
                message += f"現在播放：{self.current.title}\n"
            for item in self.queue._queue:
                message += f"- {item.title}\n"
            await self.interaction.response.send_message(message)

    async def random_play(self, count: int = 1):
        if count < 1 or count > 5:
            return "請指定1到5首歌曲。"
        
        if self.interaction.user.voice is None:
            return "你必須先加入語音頻道。"
        
        if not self.interaction.guild.voice_client:
            self.voice_client = await self.interaction.user.voice.channel.connect()
        
        # 隨機選擇指定數量的歌曲
        selected_songs = random.sample(SONG_LIBRARY, min(count, len(SONG_LIBRARY)))
        
        # 添加第一首歌並開始播放
        first_song = await YTDLSource.from_url(selected_songs[0], loop=self.bot.loop, stream=True)
        await self.queue.put(first_song)
        first_title = first_song.title
        
        # 添加剩餘的歌曲到隊列
        message = f"🎲 正在播放：{first_title}\n"
        if len(selected_songs) > 1:
            message += "\n即將播放：\n"
            for song_url in selected_songs[1:]:
                player = await YTDLSource.from_url(song_url, loop=self.bot.loop, stream=True)
                await self.queue.put(player)
                message += f"- {player.title}\n"
        
        if self.current is None:
            self.current_user = self.interaction.user
            self.bot.loop.create_task(self.player_loop())
        
        return message

music_players = {}

def get_music_player(interaction: discord.Interaction):
    player = music_players.get(interaction.guild.id)
    if player is None:
        player = MusicPlayer(interaction)
        music_players[interaction.guild.id] = player
    return player

@bot.tree.command(name='play', description='播放音樂')
async def play(interaction: discord.Interaction, url: str):
    try:
        player = get_music_player(interaction)
        message = await player.play(url)
        await interaction.response.send_message(message)
    except Exception as e:
        try:
            await interaction.response.send_message(f"發生錯誤：{str(e)}")
        except discord.InteractionResponded:
            await interaction.followup.send(f"發生錯誤：{str(e)}")

@bot.tree.command(name='stop', description='停止音樂')
async def stop(interaction: discord.Interaction):
    player = get_music_player(interaction)
    await player.stop()
    try:
        await interaction.response.send_message("已停止音樂。")
    except discord.InteractionResponded:
        await interaction.followup.send("已停止音樂。")

@bot.tree.command(name='pause', description='暫停音樂')
async def pause(interaction: discord.Interaction):
    player = get_music_player(interaction)
    await player.pause()
    try:
        await interaction.response.send_message("音樂已暫停。")
    except discord.InteractionResponded:
        await interaction.followup.send("音樂已暫停。")

@bot.tree.command(name='resume', description='繼續播放音樂')
async def resume(interaction: discord.Interaction):
    player = get_music_player(interaction)
    await player.resume()
    try:
        await interaction.response.send_message("音樂已繼續播放。")
    except discord.InteractionResponded:
        await interaction.followup.send("音樂已繼續播放。")

@bot.tree.command(name='info', description='顯示前播放的音樂信息')
async def info(interaction: discord.Interaction):
    player = get_music_player(interaction)
    message = f"現在播放：{player.current.title}" if player.current else "當前沒有正在播放的音樂。"
    try:
        await interaction.response.send_message(message)
    except discord.InteractionResponded:
        await interaction.followup.send(message)

@bot.tree.command(name='list', description='顯示播放列表')
async def list_music(interaction: discord.Interaction):
    player = get_music_player(interaction)
    await player.list()


@bot.tree.command(name='avatar', description='獲取用戶頭像')
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
    # 如果沒有指定用戶，則獲取命令使用者的頭像
    target = member or interaction.user
    
    # 創建嵌入消息
    embed = discord.Embed(
        title=f"{target.name} 的頭像",
        color=discord.Color.blue()
    )
    
    # 添加頭像圖片
    # 使用 1024 大小的頭像以獲得高質量圖片
    avatar_url = target.avatar.url if target.avatar else target.default_avatar.url
    embed.set_image(url=avatar_url)
    
    # 添加不同格式的鏈接
    formats = ['png', 'jpg', 'webp']
    if target.avatar and target.avatar.is_animated():
        formats.append('gif')
    
    # 添加不同格式的下載鏈接
    links = []
    for fmt in formats:
        if target.avatar:
            url = target.avatar.replace(format=fmt, size=1024).url
            links.append(f"[{fmt.upper()}]({url})")
    
    if links:
        embed.add_field(
            name="下載鏈接", 
            value=" | ".join(links), 
            inline=False
        )
    
    try:
        await interaction.response.send_message(embed=embed)
    except discord.InteractionResponded:
        await interaction.followup.send(embed=embed)

# 在文件頂部添加歌曲庫
SONG_LIBRARY = [
    
    "https://www.youtube.com/watch?v=b9v1xfoWAFg",  # 示例歌曲1
    "https://www.youtube.com/watch?v=6j0riQjd7Sc",  # 示例歌曲2
    "https://www.youtube.com/watch?v=UyGJNeBcntk",  # 示例歌曲3
    "https://www.youtube.com/watch?v=nmKS2mU9GOY",  # 示例歌曲4
    "https://www.youtube.com/watch?v=qr9C5VWLGaw",  # 示例歌曲5
    "https://www.youtube.com/watch?v=ZFw6rTEw1hA",
    "https://www.youtube.com/watch?v=YfvR3zccdV0",
    "https://www.youtube.com/watch?v=R8VxXGW9hoY",
    "https://www.youtube.com/watch?v=IIFtASwv5PI",
    "https://www.youtube.com/watch?v=TvSLCxXyrBA",
    "https://www.youtube.com/watch?v=hVlmlDwhpWY",
    "https://www.youtube.com/watch?v=lN4J3zB_VuE",
    "https://www.youtube.com/watch?v=_uX6ndtAUjA",
    "https://www.youtube.com/watch?v=JWhiMqUEhjM",
    "https://www.youtube.com/watch?v=CZkpTaDLjGg",
    "https://www.youtube.com/watch?v=BHZhN-q9hDE",
    "https://www.youtube.com/watch?v=uy3bB5HzEzM",
    "https://www.youtube.com/watch?v=othQVXxFd6I",
    "https://www.youtube.com/watch?v=daW4zyd4lxE",
    "https://www.youtube.com/watch?v=U8ijz0uIbfQ",
    "https://www.youtube.com/watch?v=W8FuzKquWwk",
    "https://www.youtube.com/watch?v=IFMcyb4yxBc",
    "https://www.youtube.com/watch?v=beh80hi8Dl0",
    "https://www.youtube.com/watch?v=Br4zlDRuH9U",
    "https://www.youtube.com/watch?v=H8K0uWlebHc",
    "https://www.youtube.com/watch?v=ZRovbhxsFN4",
    "https://www.youtube.com/watch?v=DMF2ZdTZR0M",
    "https://www.youtube.com/watch?v=HbwvAB88IUQ",
    "https://www.youtube.com/watch?v=sbmzhqjZ-_k",
    "https://www.youtube.com/watch?v=j8CzM8cQ7IQ",
    "https://www.youtube.com/watch?v=O_U9xlmZG2w",
    "https://www.youtube.com/watch?v=cxBZzf8lzqo",
    "https://www.youtube.com/watch?v=jfzOlIVBGzM",
    "https://www.youtube.com/watch?v=c1V_B4I3BJc",
    "https://www.youtube.com/watch?v=rUt5gnQjj04",
    "https://www.youtube.com/watch?v=gTw3jQz_nK0",
    "https://www.youtube.com/watch?v=WmW9Fh4Msu4",
    "https://www.youtube.com/watch?v=WHoWoqqYP6I",
    "https://www.youtube.com/watch?v=aSrWyQIZfgY",
    "https://www.youtube.com/watch?v=_l-Mtr-lCAU",
    "https://www.youtube.com/watch?v=eiDLGyVHpds",
    "https://www.youtube.com/watch?v=eWZT6SSTw18",
    "https://www.youtube.com/watch?v=n2N3Mhj4DyI",
    "https://www.youtube.com/watch?v=uDtIIu8okUU",
    "https://www.youtube.com/watch?v=ynaARgs_yvQ",
    "https://www.youtube.com/watch?v=h7A-VpGYVlU",
    "https://www.youtube.com/watch?v=gb4nuVNAh40",
    "https://www.youtube.com/watch?v=6l9NbH4yZS4",
    "https://www.youtube.com/watch?v=cHHLHGNpCSA",
    "https://www.youtube.com/watch?v=ZXJqjcQH46E",
    "https://www.youtube.com/watch?v=zZmtt5g4tHs",
    "https://www.youtube.com/watch?v=4shwLyB7690",
    "https://www.youtube.com/watch?v=ESmaELNy8GE",
    "https://www.youtube.com/watch?v=yr4WKlmc5vM",
    "https://www.youtube.com/watch?v=g0YlXQbdRqw",
    "https://www.youtube.com/watch?v=vsl6k1C4QH0",
    "https://www.youtube.com/watch?v=jTFhkMrCx3Q",
    "https://www.youtube.com/watch?v=-gS9G3qKdL8",
    "https://www.youtube.com/watch?v=1XESBy6zV44",
    "https://www.youtube.com/watch?v=dQX9H0_ytXo",
    "https://www.youtube.com/watch?v=bQMOIpmfSMM",
    "https://www.youtube.com/watch?v=2ByPpQbNzwk",
    "https://www.youtube.com/watch?v=aIxF_2wtPwc",
    "https://www.youtube.com/watch?v=8EIeK68l0VU",
    "https://www.youtube.com/watch?v=iIF2ikZfoxw",
    "https://www.youtube.com/watch?v=0-4mm0e2h44",
    "https://www.youtube.com/watch?v=2bvb-Ql8Pb4",
    "https://www.youtube.com/watch?v=2AGem2tw9GU",
    "https://www.youtube.com/watch?v=JJFo_CBFFwA",
    "https://www.youtube.com/watch?v=fgZoQbPYeSY",
    "https://www.youtube.com/watch?v=TZ_bFtq7MK8",
    "https://www.youtube.com/watch?v=xD5eCnghqS8",
    "https://www.youtube.com/watch?v=XK1QiIpOKqA",
    "https://www.youtube.com/watch?v=-oeKxMMsIfU",
    "https://www.youtube.com/watch?v=6LxJ_JfxuqI",
    "https://www.youtube.com/watch?v=MYFRc6q1bwI",
    "https://www.youtube.com/watch?v=d4eAzTP2Knw",
    "https://www.youtube.com/watch?v=OqmkthmPBSw",
    "https://www.youtube.com/watch?v=AfPUiV-jamA",
    "https://www.youtube.com/watch?v=25ROFXjoaAU",
    "https://www.youtube.com/watch?v=lR17BV2_7PQ",
    "https://www.youtube.com/watch?v=idi9xhRU50c",
    "https://www.youtube.com/watch?v=hZlU8N5UNAo",
    "https://www.youtube.com/watch?v=YPb3hUPNdGU",
    "https://www.youtube.com/watch?v=HWnuvLGf1Ek",
    "https://www.youtube.com/watch?v=o2gah6hYm7Y",
    "https://www.youtube.com/watch?v=i2N8ap31B0I",
    "https://www.youtube.com/watch?v=4bEn27biSTE",
    "https://www.youtube.com/watch?v=L229QDxDakU",
    "https://www.youtube.com/watch?v=f8arpOUQc-E",
    "https://www.youtube.com/watch?v=a4eR-5OJ-7Q",
    "https://www.youtube.com/watch?v=QcDzWSNqaMo",
    "https://www.youtube.com/watch?v=Ccr8t6-qTZo",
    "https://www.youtube.com/watch?v=J4RUWl__eEY",
    "https://www.youtube.com/watch?v=weKctnACxhY",
    "https://www.youtube.com/watch?v=xbwqkqv5ljo",
    "https://www.youtube.com/watch?v=WpHY9ZHZRQs"
]

# 添加隨機播放命令
@bot.tree.command(name='randomplay', description='隨機播放指定數量的歌曲(1-5首)')
async def randomplay(interaction: discord.Interaction, count: int = 1):
    try:
        if count < 1 or count > 5:
            await interaction.response.send_message("請指定1到5首歌曲。", ephemeral=True)
            return
            
        player = get_music_player(interaction)
        messages = []  # 用于存储播放消息

        # 从数据库中随机选择歌曲
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()
        c.execute('SELECT url, title FROM songs ORDER BY RANDOM() LIMIT ?', (count,))
        selected_songs = c.fetchall()
        conn.close()

        if not selected_songs:
            await interaction.response.send_message("數據庫中沒有可用的歌曲。", ephemeral=True)
            return

        for song_url, title in selected_songs:
            await player.queue.put(await YTDLSource.from_url(song_url, loop=player.bot.loop, stream=True))  # 将歌曲添加到队列
            messages.append(f"🎵 已添加到播放列表：{title}")

        if player.current is None:
            player.current_user = interaction.user
            player.bot.loop.create_task(player.player_loop())  # 开始播放

        # 创建嵌入消息
        embed = discord.Embed(
            title=f"🎲 隨機播放 {count} 首歌曲",
            description="\n".join(messages),  # 将所有消息合并
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"由 {interaction.user.name} 觸發的隨機播放")
        
        await interaction.response.send_message(embed=embed)  # 确保只调用一次
    except Exception as e:
        await interaction.followup.send(f"發生錯誤：{str(e)}", ephemeral=True)

@bot.tree.command(name='buildroom', description='創建一個臨時語音房間並上鎖')
async def buildroom(interaction: discord.Interaction, room_code: str):
    if len(room_code) != 4 or not room_code.isdigit():
        await interaction.response.send_message("請提供一個四位數的數字作為房間代碼。", ephemeral=True)
        return

    guild = interaction.guild
    channel_name = f"尊貴包廂-{room_code}"

    # 獲取特定類別（請替換 '特定類別名稱' 為您的類別名稱）
    category = discord.utils.get(guild.categories, name='語音頻道')

    # 創建語音頻道，只有創建者可以進入
    voice_channel = await guild.create_voice_channel(channel_name, overwrites={
        guild.default_role: discord.PermissionOverwrite(connect=False),  # 禁止所有人進入
        interaction.user: discord.PermissionOverwrite(connect=True)  # 允許創建者進入
    }, category=category)  # 將頻道放到特定類別下

    await interaction.response.send_message(f"已創建語音房間：{voice_channel.mention}，並已上鎖。", ephemeral=True)

    # 存在時間
    await asyncio.sleep(30)  # 等待30秒

    # 等待用戶退出語音頻道
    while True:
        await bot.wait_until_ready()
        if not voice_channel.members:  # 如果沒有成員在頻道中
            await voice_channel.delete()  # 刪除頻道
            break
        await asyncio.sleep(10)  # 每10秒檢查一次


@bot.tree.command(name='stepme', description='隨機回復一條消息')
async def step_me(interaction: discord.Interaction):
    responses = [
        "哼！敢惹我？",
        "你什麽意思？",
        "你這個臭小子！",
        "你找死！",
        "我生氣了",
        "你完了！",
        "你死定了！",
        "你這個混蛋！",
        "你這個垃圾！",
        "你這個廢物！",
        "你這個白癡！",
        "你這個傻逼！",
        "你這個蠢貨！",
        "你這個笨蛋！",
        "你這個智障！",
        "你這個傻子！",
        "你沒救了！",
        "真是廢物呢！",
        "要你有什麽用？"
        "回去吃奶吧你！"
    ]
    
    body_parts = [
        "頭",
        "手",
        "腳",
        "肩膀",
        "膝蓋",
        "腹部",
        "脖子",
        "臉",
        "手臂",
        "大腿",
        "���腿",
        "腳踝",
    ]
    
    # 隨機選擇回復和身體部位
    response = random.choice(responses)
    body_part = random.choice(body_parts)
    
    await interaction.response.send_message(f"{response}（踩你{body_part}！！！）")
