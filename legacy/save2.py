import os

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

@bot.event
async def on_ready():
    setup_database()
    await bot.tree.sync()
    print(f'已登入為 {bot.user}')

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    #if message.author.id == 100000000000000024:
        responses = [
            "\n——乳冰花（自封文豪）",
            "\n——乳冰花（網絡文學家）",
            "\n——乳冰花（偽古風大師）",
            "\n——乳冰花（這真的不是李白說的）",
            "\n——乳冰花（文壇泰斗）",
            "\n——乳冰花（詩詞大家）",
            "\n——乳冰花（文學界新星）",
            "\n——乳冰花（業餘寫手）",
            "\n——乳冰花（偽裝成李白）",
            "\n——乳冰花（文學夢想家）",
            "\n——乳冰花（網文達人）",
            "\n——乳冰花（自稱色情��",
            "\n——乳冰花（文青代表）",
            "\n——乳冰花（偽文學家）",
            "\n——乳冰花（網絡詩人）",
            "\n——乳冰花（文學愛好者）",
            "\n——乳冰花（準文豪）",
            "\n——乳冰花（偽裝杜甫）",
            "\n——乳冰花（文學小白）",
            "\n——乳冰花（網絡寫手）"
        ]
        if not "www." in message.content:
            await message.channel.send(message.content + random.choice(responses))

    #if message.author.id == 100000000000000025:
        #if message.author.id != bot.user.id:
            #responses = [
                #"\n——雪寶寶（可愛小天使）",
                #"\n——雪寶寶（暖心小可愛）",
                #"\n——雪寶寶（甜甜小作家）",
                #"\n——雪寶寶（文學小精靈）",
                #"\n——雪寶寶（溫柔小詩人）",
                #"\n——雪寶寶（創意小天才）",
                #"\n——雪寶（夢想追逐者）",
                #"\n——雪寶寶（快樂寫手）",
                #"\n——雪寶寶（靈感女神）",
                #"\n——雪寶寶（文藝小公主）",
                #"\n——雪寶寶（才華橫溢）",
                #"\n——雪寶寶（文學小仙女）",
                #"\n——雪寶寶（創作小天使）",
                #"\n——雪寶寶（優雅小淑女）",
                #"\n——雪寶寶（詩詞小精靈）",
                #"\n——雪寶寶（文藝小天使）",
                #"\n——雪寶寶（靈動小仙子）",
                #"\n——雪寶寶（文學小花朵）",
                #"\n——雪寶寶（創意小精靈）",
                #"\n——雪寶寶（溫暖小太陽）"
            #]
            if not "www." in message.content:
                await message.channel.send(message.content + random.choice(responses))
    
    
    #if message.author.id == 100000000000000026:
    #    await message.channel.send(f"司馬司馬仲：{message.content}")

    #if message.channel.id in [100000000000000020, 100000000000000027, 100000000000000028]:
    #if message.author.id in [100000000000000029, 100000000000000024]:
        #if not "怡" in message.content and not "忄台" in message.content:
                #if not message.attachments and "http" not in message.content:
                    #await message.channel.send(message.content + "嘻嘻嘻嘻嘻嘻嘻嘻")

    #f '怡' in message.content:
        #await message.channel.send('我好愛她')

    if "小孟" in message.content:
        try:
            await message.delete()
        except discord.Forbidden:
            print("沒有權限刪除消息。")
        except discord.HTTPException as e:
            print(f"刪除消息時發生錯誤: {e}")

    await bot.process_commands(message)

vote_kick = {}

class VoteKickView(discord.ui.View):
    def __init__(self, target_member: discord.Member):
        super().__init__(timeout=60)
        self.target_member = target_member
        self.voters = set()

    @discord.ui.button(label="投票踢出", style=discord.ButtonStyle.danger, emoji="👢")
    async def vote_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.voters:
            await interaction.response.send_message("你已經投過票了！", ephemeral=True)
            return
            
        if interaction.user.id == self.target_member.id:
            await interaction.response.send_message("你不能投票踢出自己！", ephemeral=True)
            return
            
        self.voters.add(interaction.user.id)
        
        if len(self.voters) >= 4:
            try:
                await self.target_member.kick()
                await self.message.delete()
                result_msg = await interaction.channel.send(
                    f"✅ 投票成功！{self.target_member.mention} 已被踢出伺服器。"
                )
                await asyncio.sleep(5)
                await result_msg.delete()
            except discord.Forbidden:
                await interaction.channel.send(
                    "❌ 機器人沒有權限踢出該用戶！",
                    delete_after=5
                )
            self.stop()
        else:
            embed = discord.Embed(
                title="🗳️ 踢出投票進行中",
                description=f"目標用戶：{self.target_member.mention}\n"
                          f"當前票數：{len(self.voters)}/4\n"
                          f"發起人：{interaction.user.mention}\n"
                          f"剩餘時間：60秒",
                color=discord.Color.yellow()
            )
            await self.message.edit(embed=embed)
            await interaction.response.send_message(
                f"你的投票已記錄！當前票數：{len(self.voters)}/4", 
                ephemeral=True
            )

    async def on_timeout(self):
        try:
            await self.message.delete()
            timeout_msg = await self.message.channel.send(
                f"❌ 投票失敗！踢出 {self.target_member.mention} 的投票因超時而結束。"
                f"\n最終票數：{len(self.voters)}/4"
            )
            await asyncio.sleep(5)
            await timeout_msg.delete()
        except:
            pass

@bot.tree.command(name='votekick', description='發起投票踢出用戶')
async def votekick(interaction: discord.Interaction, member: discord.Member):
    try:
        if member.id == interaction.user.id:
            await interaction.response.send_message(
                "你不能投票踢出自己！", 
                ephemeral=True
            )
            return
            
        view = VoteKickView(member)
        
        embed = discord.Embed(
            title="🗳️ 踢出投票已發起",
            description=f"目標戶{member.mention}\n"
                      f"票數：0/4\n"
                      f"發起人：{interaction.user.mention}\n"
                      f"剩餘時間：60秒",
            color=discord.Color.yellow()
        )
        
        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()
        view.message = message
        
    except Exception as e:
        await interaction.response.send_message(
            f"發生錯誤：{str(e)}", 
            ephemeral=True
        )

# 修改 setup_database 函數，添加數據庫更新功能
def setup_database():
    conn = sqlite3.connect('marriage.db')
    c = conn.cursor()
    
    # 先創建 marriages 表
    c.execute('''CREATE TABLE IF NOT EXISTS marriages
                 (user1_id INTEGER,
                  user2_id INTEGER,
                  marriage_date TEXT,
                  PRIMARY KEY (user1_id, user2_id))''')
    
    # 檢查 children 表是否存在
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='children'")
    if not c.fetchone():
        # 如果不存在，���建新表
        c.execute('''CREATE TABLE IF NOT EXISTS children
                     (child_id INTEGER PRIMARY KEY AUTOINCREMENT,
                      parent1_id INTEGER,
                      parent2_id INTEGER,
                      child_name TEXT,
                      birth_date TEXT,
                      last_feed TEXT DEFAULT NULL,
                      is_alive INTEGER DEFAULT 1)''')
    
    conn.commit()
    conn.close()

# 在機器人啟動時調用此函數
@bot.event
async def on_ready():
    print(f'Bot已啟動為 {bot.user}')
    setup_database()  # 確保數據庫結構正確
    bot.loop.create_task(check_children())

# 添加定時檢查函數
async def check_children():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            conn = sqlite3.connect('marriage.db')
            c = conn.cursor()
            
            # 獲取所有活著且需要檢查的孩子
            c.execute('''SELECT child_id, child_name, parent1_id, parent2_id, last_feed, birth_date 
                        FROM children 
                        WHERE is_alive = 1''')
            children = c.fetchall()
            
            for child in children:
                child_id, child_name, parent1_id, parent2_id, last_feed, birth_date = child
                
                # 如果從未餵食，使用出生時間
                check_time = last_feed if last_feed else birth_date
                last_time = datetime.strptime(check_time, '%Y-%m-%d %H:%M:%S')
                
                # 檢查是否超過5分鐘
                if datetime.utcnow() - last_time > timedelta(minutes=5):
                    # 更新為死亡狀態
                    c.execute('UPDATE children SET is_alive = 0 WHERE child_id = ?', (child_id,))
                    conn.commit()
                    
                    try:
                        # 獲取父母信息
                        parent1 = await bot.fetch_user(parent1_id)
                        parent2 = await bot.fetch_user(parent2_id)
                        
                        # 尋找一個適合發送消息的頻道
                        for guild in bot.guilds:
                            for channel in guild.text_channels:
                                if channel.permissions_for(guild.me).send_messages:
                                    embed = discord.Embed(
                                        title="👶 孩子離世通知",
                                        description=f"😢 {parent1.mention} 和 {parent2.mention} 的孩子 {child_name} 因未及時餵養而離世...",
                                        color=discord.Color.red()
                                    )
                                    embed.add_field(
                                        name="存活時間",
                                        value=f"出生時間：{birth_date}\n"
                                              f"最後餵養：{last_feed if last_feed else '從未餵養'}"
                                    )
                                    await channel.send(embed=embed)
                                    break
                            break
                    except:
                        continue
            
            conn.close()
            
        except Exception as e:
            print(f"檢查孩子狀態時發生錯誤：{str(e)}")
            
        await asyncio.sleep(60)  # 每分鐘檢查一次

# 將婚姻檢查邏輯抽取為獨立函數
async def check_marriage_status(c, user_id):
    c.execute('SELECT * FROM marriages WHERE user1_id=? OR user2_id=?', 
             (user_id, user_id))
    return c.fetchone()

@bot.tree.command(name='propose', description='向某人求婚')
async def propose(interaction: discord.Interaction, target: discord.Member):
    try:
        if target.id == interaction.user.id:
            await interaction.response.send_message("你不能和自己結婚！", ephemeral=True)
            return
            
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()
        
        # 檢查雙方婚姻狀態
        if await check_marriage_status(c, interaction.user.id):
            await interaction.response.send_message("你已經結婚了！", ephemeral=True)
            conn.close()
            return
            
        if await check_marriage_status(c, target.id):
            await interaction.response.send_message(f"{target.name} 已經結婚了！", ephemeral=True)
            conn.close()
            return
        
        # 發送求婚請求
        await interaction.response.send_message(
            f"{interaction.user.mention} 向 {target.mention} 求婚！\n"
            f"{target.mention} 請在60秒內回覆 'yes' 接受或 'no' 拒絕。"
        )
        
        def check(m):
            return m.author.id == target.id and m.content.lower() in ['yes', 'no']
        
        try:
            msg = await bot.wait_for('message', timeout=60.0, check=check)
            if msg.content.lower() == 'yes':
                c.execute('INSERT INTO marriages VALUES (?, ?, datetime("now"))',
                         (interaction.user.id, target.id))
                conn.commit()
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="💕 結婚成功",
                        description=f"🎉 恭喜！{interaction.user.mention} 和 {target.mention} 結為夫妻！",
                        color=discord.Color.pink()
                    )
                )
            else:
                await interaction.followup.send(f"{target.mention} 拒絕了求婚。")
        except asyncio.TimeoutError:
            await interaction.followup.send("求婚超時，請重新求婚。")
            
    except Exception as e:
        await interaction.response.send_message(f"發生錯誤：{str(e)}", ephemeral=True)
    finally:
        conn.close()

@bot.tree.command(name='divorce', description='離婚')
async def divorce(interaction: discord.Interaction):
    conn = sqlite3.connect('marriage.db')
    c = conn.cursor()
    
    # 查找婚姻關係
    c.execute('''SELECT * FROM marriages 
                 WHERE user1_id=? OR user2_id=?''', 
             (interaction.user.id, interaction.user.id))
    marriage = c.fetchone()
    
    if not marriage:
        await interaction.response.send_message("你還沒有結婚！")
        conn.close()
        return

    # 刪除婚姻關係
    c.execute('''DELETE FROM marriages 
                 WHERE user1_id=? OR user2_id=?''', 
             (interaction.user.id, interaction.user.id))
    conn.commit()
    conn.close()
    
    await interaction.response.send_message("離婚成功。")

@bot.tree.command(name='marriage_info', description='查看婚姻狀況')
async def marriage_info(interaction: discord.Interaction, member: discord.Member = None):
    if member is None:
        member = interaction.user

    conn = sqlite3.connect('marriage.db')
    c = conn.cursor()
    
    c.execute('''SELECT * FROM marriages 
                 WHERE user1_id=? OR user2_id=?''', 
             (member.id, member.id))
    marriage = c.fetchone()
    conn.close()

    if not marriage:
        await interaction.response.send_message(f"{member.name} 目前是單身。")
        return

    # 獲取配偶信息
    spouse_id = marriage[1] if marriage[0] == member.id else marriage[0]
    spouse = await bot.fetch_user(spouse_id)
    marriage_date = marriage[2]

    await interaction.response.send_message(
        f"{member.name} 的婚姻狀況：\n"
        f"配偶：{spouse.name}\n"
        f"結婚日期：{marriage_date}"
    )

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

@bot.tree.command(name='force_marry', description='強制兩個用戶結婚（僅限管理員）')
async def force_marry(interaction: discord.Interaction, user1: discord.Member, user2: discord.Member):
    try:
        # 檢查使用者是否為您
        if interaction.user.id != 100000000000000003:
            await interaction.response.send_message("❌ 你沒有權限使用此命令！", ephemeral=True)
            return
            
        # 檢查是否為同一人
        if user1.id == user2.id:
            await interaction.response.send_message("❌ 不能讓同一個人和自己結婚！", ephemeral=True)
            return
            
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()
        
        # 檢查雙方婚姻狀態
        if await check_marriage_status(c, user1.id):
            await interaction.response.send_message(f"❌ {user1.name} 已經結婚了！", ephemeral=True)
            conn.close()
            return
            
        if await check_marriage_status(c, user2.id):
            await interaction.response.send_message(f"❌ {user2.name} 已經結婚了！", ephemeral=True)
            conn.close()
            return
            
        # 執行強制結婚
        c.execute('INSERT INTO marriages VALUES (?, ?, datetime("now"))',
                 (user1.id, user2.id))
        conn.commit()
        conn.close()
        
        # 創建嵌入消息
        embed = discord.Embed(
            title="💕 強制結婚",
            description=f"🎉 恭喜！{user1.mention} 和 {user2.mention} 已被強制結為夫妻！",
            color=discord.Color.pink()
        )
        embed.set_footer(text=f"由 {interaction.user.name} 執行的強制結婚")
        
        # 發送通知
        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
        await interaction.response.send_message(f"發生錯誤：{str(e)}", ephemeral=True)
    finally:
        conn.close()

# 修改生孩子相關函數，移除初始餵食時間
async def add_child(c, parent1_id, parent2_id, child_name):
    c.execute('''INSERT INTO children 
                 (parent1_id, parent2_id, child_name, birth_date, is_alive) 
                 VALUES (?, ?, ?, datetime("now"), 1)''',
             (parent1_id, parent2_id, child_name))

# 修改餵食命令，添加更多提示
@bot.tree.command(name='feed', description='餵養你的孩子')
async def feed(interaction: discord.Interaction, child_name: str):
    try:
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()
        
        c.execute('''SELECT child_id, parent1_id, parent2_id, last_feed, is_alive, birth_date
                    FROM children 
                    WHERE child_name=? AND (parent1_id=? OR parent2_id=?)''',
                 (child_name, interaction.user.id, interaction.user.id))
        child = c.fetchone()
        
        if not child:
            await interaction.response.send_message("找不到這個孩子，或者你不是孩子的父母！", ephemeral=True)
            return
            
        if child[4] == 0:  # is_alive
            await interaction.response.send_message(f"😢 {child_name} 已經離開了我們...", ephemeral=True)
            return
            
        # 更新餵食時間
        c.execute('''UPDATE children 
                    SET last_feed = datetime("now")
                    WHERE child_id = ?''', (child[0],))
        conn.commit()
        
        # 計算距離上次餵食的時間
        last_time = datetime.strptime(child[3] if child[3] else child[5], '%Y-%m-%d %H:%M:%S')
        time_diff = datetime.utcnow() - last_time
        minutes_left = 5 - int(time_diff.total_seconds() / 60)
        
        embed = discord.Embed(
            title="🍼 餵養成功",
            description=f"你餵養了 {child_name}！",
            color=discord.Color.green()
        )
        embed.add_field(
            name="提示",
            value=f"距離上次餵食已經過去了 {int(time_diff.total_seconds() / 60)} 分鐘\n"
                  f"請記得在5分鐘內再次餵養，否則孩子會離世",
            inline=False
        )
        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
        await interaction.response.send_message(f"發生錯誤：{str(e)}", ephemeral=True)
    finally:
        conn.close()

# 修改 children_list 命令，顯示存活狀態和最後餵食時間
@bot.tree.command(name='children_list', description='查看指定用戶或所有人的孩子')
async def children_list(interaction: discord.Interaction, member: discord.Member = None):
    try:
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()
        
        if member:
            c.execute('''SELECT child_name, parent1_id, parent2_id, birth_date, last_feed, is_alive 
                        FROM children 
                        WHERE parent1_id=? OR parent2_id=?''', 
                     (member.id, member.id))
        else:
            c.execute('SELECT child_name, parent1_id, parent2_id, birth_date, last_feed, is_alive FROM children')
        
        children = c.fetchall()
        
        if not children:
            await interaction.response.send_message(
                "👶 目前還沒有任何孩子記錄。" if not member else f"👶 {member.name} 目前還沒有孩子。"
            )
            return
            
        embed = discord.Embed(
            title="👶 伺服器所有孩子列表" if not member else f"👶 {member.name} 的孩子列表",
            color=discord.Color.blue()
        )
        
        for child in children:
            try:
                parent1 = await bot.fetch_user(child[1])
                parent2 = await bot.fetch_user(child[2])
                
                # 檢查是否需要更新存活狀態
                if child[5] == 1:  # 如果還活著
                    last_feed = datetime.strptime(child[4], '%Y-%m-%d %H:%M:%S')
                    if datetime.utcnow() - last_feed > timedelta(minutes=5):
                        # 更新為死亡狀態
                        c.execute('UPDATE children SET is_alive = 0 WHERE child_name = ?', (child[0],))
                        conn.commit()
                        status = "💀 已死亡（未及時餵養）"
                    else:
                        status = "💚 健康"
                else:
                    status = "💀 已死亡"
                
                embed.add_field(
                    name=f"👶 {child[0]} ({status})", 
                    value=f"出生日期：{child[3]}\n"
                          f"父母：{parent1.name} 和 {parent2.name}\n"
                          f"最後餵養時間：{child[4] if child[4] else '從未餵養'}",
                    inline=False
                )
            except:
                continue
        
        embed.set_footer(text=f"查詢時間：{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")
        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
        await interaction.response.send_message(f"發生錯誤：{str(e)}", ephemeral=True)
    finally:
        conn.close()

@bot.tree.command(name='force_baby', description='強制兩個用戶生孩子（僅限管理員）')
async def force_baby(interaction: discord.Interaction, parent1: discord.Member, parent2: discord.Member, child_name: str):
    try:
        # 檢查使用者是否為您
        if interaction.user.id != 100000000000000003:
            await interaction.response.send_message("❌ 你沒有權限使用此命令！", ephemeral=True)
            return
            
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()
        
        # 檢查孩子名字是否已存在
        c.execute('SELECT * FROM children WHERE child_name = ?', (child_name,))
        if c.fetchone():
            await interaction.response.send_message("❌ 這個名字已經被使用了！", ephemeral=True)
            conn.close()
            return
        
        # 添加孩子記錄
        c.execute('''INSERT INTO children 
                    (parent1_id, parent2_id, child_name, birth_date, is_alive) 
                    VALUES (?, ?, ?, datetime("now"), 1)''',
                 (parent1.id, parent2.id, child_name))
        conn.commit()
        
        # 創建嵌入消息
        embed = discord.Embed(
            title="👶 強制生子",
            description=f"🎉 {parent1.mention} 和 {parent2.mention} 被強制生下了一個寶寶！",
            color=discord.Color.gold()
        )
        embed.add_field(
            name="寶寶資訊",
            value=f"👶 名字：{child_name}\n"
                  f"📅 出生時間：{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}\n"
                  f"⚠️ 請記得在5分鐘內餵養寶寶，否則寶寶會離世",
            inline=False
        )
        embed.set_footer(text=f"由 {interaction.user.name} 執行的強制生子")
        
        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
        await interaction.response.send_message(f"發生錯誤：{str(e)}", ephemeral=True)
    finally:
        conn.close()

# 添加自願生孩子命令
@bot.tree.command(name='have_baby', description='和配偶生一個孩子')
async def have_baby(interaction: discord.Interaction, spouse: discord.Member, child_name: str):
    try:
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()
        
        # 檢查是否已婚且是否為配偶
        c.execute('''SELECT * FROM marriages 
                    WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)''',
                 (interaction.user.id, spouse.id, spouse.id, interaction.user.id))
        marriage = c.fetchone()
        
        if not marriage:
            await interaction.response.send_message("你們還沒有結婚！", ephemeral=True)
            conn.close()
            return
            
        # 檢查孩子名字是否已存在
        c.execute('SELECT * FROM children WHERE child_name = ?', (child_name,))
        if c.fetchone():
            await interaction.response.send_message("這個名字已經被使用了！", ephemeral=True)
            conn.close()
            return
            
        # 發送確認請求
        await interaction.response.send_message(
            f"{spouse.mention}，{interaction.user.mention} 想要和你生一個叫 {child_name} 的孩子！\n"
            f"請在30秒內回覆 'yes' 同意或 'no' 拒絕。"
        )
        
        def check(m):
            return m.author.id == spouse.id and m.content.lower() in ['yes', 'no']
            
        try:
            msg = await bot.wait_for('message', timeout=30.0, check=check)
            if msg.content.lower() == 'yes':
                c.execute('''INSERT INTO children 
                            (parent1_id, parent2_id, child_name, birth_date, is_alive)
                            VALUES (?, ?, ?, datetime("now"), 1)''',
                         (interaction.user.id, spouse.id, child_name))
                conn.commit()
                
                embed = discord.Embed(
                    title="👶 新生兒誕生",
                    description=f"🎉 恭喜！{interaction.user.mention} 和 {spouse.mention} 喜獲一位寶寶！",
                    color=discord.Color.gold()
                )
                embed.add_field(
                    name="寶寶資訊",
                    value=f"👶 名字：{child_name}\n"
                          f"📅 出生時間：{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}\n"
                          f"⚠️ 請記得在5分鐘內餵養寶寶，否則寶寶會離世",
                    inline=False
                )
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(f"{spouse.mention} 拒絕了生孩子的請求。")
        except asyncio.TimeoutError:
            await interaction.followup.send("請求超時，請重新嘗試。")
            
    except Exception as e:
        await interaction.followup.send(f"發生錯誤：{str(e)}", ephemeral=True)
    finally:
        conn.close()

bot.run(os.getenv("DISCORD_BOT_TOKEN"))