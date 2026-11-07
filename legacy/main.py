import discord
import yt_dlp
import asyncio
from discord.ext import commands
import random
import sqlite3
from datetime import datetime, timedelta
import aiohttp
from collections import defaultdict
import json
import logging
import subprocess
from discord.ui import Select, View,Button
from opencc import OpenCC
import importlib
import sys
import pytz
from discord import TextChannel
import time
from yt_dlp import YoutubeDL
from discord import Embed
import shutil
import requests
from transformers import AutoModelForCausalLM, AutoTokenizer
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont,ImageFilter
import os
from discord import app_commands
from queue import Queue
import subprocess
import sys
from packaging import version
from transformers import AutoTokenizer, AutoModelForCausalLM
import torch
import re

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='/', intents=intents)


        
players = {}

autoplay_running = False  
autoplay_task = None  


TOKEN = os.getenv('DISCORD_BOT_TOKEN')

ytdl_format_options = {
    'format': 'bestaudio/best',  # 确保下载最佳音频质量
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': False,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',  # 将音频转换为 mp3 格式
        'preferredquality': '256',  # 设置音频质量为 192kbps
    }],
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
}




ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -timeout 1000000',
    'options': '-vn',
}
DB_PATH = 'marriage.db'

# 在文件頂部添加這個函數
def create_tables():
    """創建必要的資料表"""
    DB_PATH = 'marriage.db'
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 創建歌曲評分表
    c.execute('''CREATE TABLE IF NOT EXISTS song_ratings
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  song_name TEXT NOT NULL,
                  user_id TEXT NOT NULL,
                  rating INTEGER CHECK(rating >= 1 AND rating <= 5),
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE(song_name, user_id))''')
    
    conn.commit()
    conn.close()
    print("✅ 數據庫表創建完成")

# 在文件開頭調用這個函數
create_tables()
ytdl = YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title', '未知標題')  
        self.url = data.get('url')  
        self.webpage_url = data.get('webpage_url', self.url) 
        self.source = source

    @staticmethod
    def analyze_volume(file_path):
        """
        分析音頻文件的平均音量 (dB)
        """
        command = [
            "ffmpeg", "-i", file_path, "-filter:a", "volumedetect", "-f", "null", "-"
        ]
        result = subprocess.run(command, stderr=subprocess.PIPE, text=True)
        for line in result.stderr.split('\n'):
            if "mean_volume" in line:
                return float(line.split(":")[-1].strip().replace(" dB", ""))
        return 0.0

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        """
        从 URL 創建 YTDLSource 對象，同時調整音量
        """
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:  
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)

        if not stream:
            mean_volume = cls.analyze_volume(filename)  
        else:
            mean_volume = 0.0  

        # 新增音量统一处理
        target_lufs = -14.0  # 目标响度（单位：LUFS）
        ffmpeg_options['options'] += f" -af loudnorm=I={target_lufs}:TP=-2:LRA=11"

        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data, volume=1.0)

    @classmethod
    async def search_results(cls, query, loop=None):
        """
        搜索 YouTube，並返回最多 5條搜索結果
        """
        loop = loop or asyncio.get_event_loop()
        try:
            search_query = f"ytsearch5:{query}"  
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(search_query, download=False))

            if not data or 'entries' not in data or not data['entries']:
                print(f"❌ 搜索 `{query}` 無結果。")
                return []  

            
            results = []
            for entry in data['entries']:
                results.append({
                    'title': entry.get('title', '未知標題'),
                    'webpage_url': entry.get('webpage_url'),
                    'duration': entry.get('duration', 0),  
                    'view_count': entry.get('view_count', 0),
                })
            return results

        except Exception as e:
            print(f"❌ YouTube 搜索失敗：{e}")
            return []  
    
class MusicPlayer:
    def __init__(self, interaction: discord.Interaction):
        self.interaction = interaction
        self.queue = asyncio.Queue()
        self.priority_queue = asyncio.Queue()
        self.next = asyncio.Event()
        self.current = None
        self.voice_client = None
        self.bot = interaction.client
        self.current_user = None
        self.lock = asyncio.Lock()
        self.loop_task = None
        self.is_downloading = False
        self.played_songs = set()  # Added played_songs attribute
        self.guild_id = interaction.guild_id 

    async def ensure_voice_client(self):
    # 檢查機器人是否已經在語音頻道中
        if self.voice_client:
            if self.voice_client.is_connected():
                print("[DEBUG] 已連接到語音頻道，無需重新連接")
                return
            else:
                # 如果機器人已經在其他頻道，則斷開連接
                await self.voice_client.disconnect()
                print("[DEBUG] 機器人已從其他頻道斷開連接")

        try:
            if not self.interaction.user.voice:
                raise RuntimeError("用戶不在任何語音頻道中。")
            
            # 這裡添加檢查，確保不會重複連接
            if self.voice_client is None or not self.voice_client.is_connected():
                print("[DEBUG] 嘗試連接到語音頻道...")
                self.voice_client = await self.interaction.user.voice.channel.connect()
                print("[DEBUG] 成功連接到語音頻道")
        except Exception as e:
            raise RuntimeError(f"無法連接到語音頻道：{e}")

    async def join(self, channel):
        if self.voice_client is None or not self.voice_client.is_connected():
            self.voice_client = await channel.connect()

    async def player_loop(self):
        if self.loop_task and not self.loop_task.done():
            print("[DEBUG] 播放循環已啟動，跳過重複啟動")
            return
        print("[DEBUG] 播放循環啟動中...")
        self.loop_task = asyncio.create_task(self._player_loop_task())

    


    async def _player_loop_task(self):
        while True:
            self.next.clear()
            try:
                async with self.lock:
                    if not self.priority_queue.empty():
                        source = await self.priority_queue.get()
                    else:
                        print("[DEBUG] 優先隊列已空，等待新歌曲加入...")
                        await asyncio.sleep(2)
                        continue

                # 確保已連接到語音頻道
                await self.ensure_voice_client()  # 调用 ensure_voice_client

                # 檢查語音客戶端是否已連接
                if not self.voice_client.is_connected():
                    print("[DEBUG] 語音客戶端未連接，退出播放循環。")
                    return

                if len(self.voice_client.channel.members) == 1:
                    await self.voice_client.disconnect()
                    print("[DEBUG] 聲音頻道已無人，退出頻道。")
                    return

                # 保存當前播放的歌曲
                self.current = source
                
                # 發送歌曲信息和評分選項
                try:
                    conn = sqlite3.connect('marriage.db')
                    c = conn.cursor()
                    
                    # 獲取歌曲評分信息
                    c.execute('''SELECT AVG(rating) as avg_rating, COUNT(rating) as total_ratings 
                                FROM song_ratings WHERE song_name = ?''', (source.title,))
                    result = c.fetchone()
                    avg_rating = result[0] if result[0] is not None else 0
                    total_ratings = result[1] if result[1] is not None else 0
                    
                    rating_text = f"{'⭐' * round(avg_rating)} ({avg_rating:.1f}/5.0，{total_ratings} 個評分)" if avg_rating else "未有評分"
                    embed = discord.Embed(title="🎵 正在播放", description=f"**{source.title}**\n\n當前評分：{rating_text}")
                    
                    # 獲取適當的頻道來發送消息
                    channel = None
                    if hasattr(self, 'text_channel') and self.text_channel:
                        channel = self.text_channel
                    elif hasattr(self, 'interaction') and self.interaction.channel:
                        channel = self.interaction.channel
                    
                    if channel:
                        msg = await send_silent_message(channel, embed=embed)
                        
                        # 添加評分用的反應
                        rating_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
                        for emoji in rating_emojis:
                            await msg.add_reaction(emoji)
                    
                    conn.close()
                except sqlite3.Error as db_error:
                    print(f"數據庫錯誤：{db_error}")
                except Exception as e:
                    print(f"評分系統錯誤：{e}")

                # 播放歌曲
                self.voice_client.play(
                    source,
                    after=lambda error: self.bot.loop.call_soon_threadsafe(
                        self.next.set if not error else print(f"[DEBUG] 播放中止，FFmpeg 返回錯誤：{error}")
                    )
                )
                await self.next.wait()

                # 檢查普通隊列是否有歌曲
                if not self.queue.empty():
                    print("[DEBUG] 優先隊列播放完畢，檢查普通隊列...")
                    # 播放普通隊列中的下一首歌曲
                    source = await self.queue.get()
                    self.voice_client.play(
                        source,
                        after=lambda error: self.bot.loop.call_soon_threadsafe(
                            self.next.set if not error else print(f"[DEBUG] 播放中止，FFmpeg 返回錯誤：{error}")
                        )
                    )
                    await self.next.wait()
                    continue  # 重新回到循環，繼續播放

                await asyncio.sleep(5)  

            except Exception as e:
                print(f"❌ 播放失敗：{e}")
                await asyncio.sleep(5)

            finally:
                if self.current:
                    self.current.cleanup()
                    self.current = None

    
    async def stop(self):
        if self.interaction.guild.voice_client:
            await self.interaction.guild.voice_client.disconnect()
            await self.interaction.response.send_message("已停止音樂。")
            await self.list()  

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

    def generate_list_embed(self):
        embed = discord.Embed(title="🎶 現在播放和播放列表", color=discord.Color.blurple())

        # 現在播放的歌曲
        if self.current:
            embed.add_field(name="正在播放", value=f"**{self.current.title}**", inline=False)
        else:
            embed.add_field(name="正在播放", value="無", inline=False)

        # 顯示優先播放隊列
        if not self.priority_queue.empty():
            priority_songs = list(self.priority_queue._queue)
            priority_lines = [f"🔴 {i + 1}. {song.title}" for i, song in enumerate(priority_songs)]
            priority_text = "\n".join(priority_lines)
            embed.add_field(name="優先播放列表", value=priority_text, inline=False)

        if not self.queue.empty():
            songs = list(self.queue._queue)
            song_lines = [f"{i + 1}. {song.title}" for i, song in enumerate(songs)]

            queue_text = "\n".join(song_lines)
            if len(queue_text) > 1024:
                queue_text = "\n".join(song_lines[:20]) + f"\n...和其他 {len(song_lines) - 20} 首歌曲"

            embed.add_field(name="播放列表", value=queue_text, inline=False)
        else:
            embed.add_field(name="播放列表", value="空", inline=False)

        return embed

    async def list(self):
        """處理 list 指令，發送嵌入消息"""
        songs = list(self.queue._queue)
        embed_list = []

        current_page = []
        current_length = 0
        page_index = 1

        # 顯示優先播放隊列
        priority_songs = list(self.priority_queue._queue)
        if priority_songs:
            current_page.append("🔴 優先播放列表：")
            for i, song in enumerate(priority_songs, start=1):
                current_page.append(f"🔴 {i}. {song.title}")

        for i, song in enumerate(songs, start=len(priority_songs) + 1):
            line = f"{i}. {song.title}"
            if current_length + len(line) + 1 > 1024:  
                embed = discord.Embed(title=f"🎶 播放列表 (第 {page_index} 頁)", color=discord.Color.blurple())
                embed.add_field(name="播放列表", value="\n".join(current_page), inline=False)
                embed_list.append(embed)

                current_page = []
                current_length = 0
                page_index += 1

            current_page.append(line)
            current_length += len(line) + 1

        if current_page:
            embed = discord.Embed(title=f"🎶 播放列表 (第 {page_index} 頁)", color=discord.Color.blurple())
            embed.add_field(name="播放列表", value="\n".join(current_page), inline=False)
            embed_list.append(embed)

        if embed_list and self.current:
            embed_list[0].add_field(name="正在播放", value=f"**{self.current.title}**", inline=False)

        for embed in embed_list:
            await self.interaction.channel.send(embed=embed)

    async def skip(self):
        if self.interaction.guild.voice_client and self.interaction.guild.voice_client.is_playing():
            self.interaction.guild.voice_client.stop()  
            await self.interaction.response.send_message("已跳過當前歌曲。")
        else:
            await self.interaction.response.send_message("當前沒有正在播放的音樂。")

music_players = {}

def get_music_player(interaction: discord.Interaction) -> MusicPlayer:
    """
    獲取或初始化 MusicPlayer 實例，支持伺服器級動態屬性和全局字典管理
    """
    if not interaction.guild:
        raise ValueError("❌ 指令必須在伺服器內執行，無法在私聊中使用！")

    if hasattr(interaction.guild, "music_player"):
        return interaction.guild.music_player

    guild_id = interaction.guild.id
    if guild_id in music_players:
        print(f"[DEBUG] 從全局字典中獲取伺服器 {guild_id} 的 MusicPlayer 實例")
        return music_players[guild_id]

    print(f"[DEBUG] 為伺服器 {guild_id} 創建新的 MusicPlayer 實例")
    player = MusicPlayer(interaction)
    interaction.guild.music_player = player
    music_players[guild_id] = player
    return player

def remove_music_player(guild: discord.Guild):
    """
    清理伺服器級 MusicPlayer，移除動態屬性和全局字典記錄
    """
    if hasattr(guild, "music_player"):
        print(f"[DEBUG] 移除伺服器 {guild.id} 的動態屬性 music_player")
        delattr(guild, "music_player")

    if guild.id in music_players:
        print(f"[DEBUG] 從全局字典中移除伺服器 {guild.id} 的 MusicPlayer 實例")
        del music_players[guild.id]

@bot.event
async def on_guild_remove(guild: discord.Guild):
    print(f"[DEBUG] 機器人已退出伺服器 {guild.id}")
    remove_music_player(guild)

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot and before.channel is not None and after.channel is None:
        print(f"[DEBUG] 機器人從語音頻道退出，伺服器 {member.guild.id}")
        remove_music_player(member.guild)

# 包裝一個方法，用於發送帶有 "@silent" 的訊息
async def send_silent_message(channel, content):
    """自動在訊息開頭加上 @silent"""
    await channel.send(f"@silent {content}")


@bot.tree.command(name='play', description='播放音樂，可選擇插入到當前播放的下一首（僅當 autoplay 運行時生效）。')
async def play(interaction: discord.Interaction, url: str, insert: bool = False):
    try:
        await interaction.response.defer(ephemeral=True)

        player = get_music_player(interaction)

        # 檢查是否已經連接到語音頻道
        if not player.voice_client or not player.voice_client.is_connected():
            await player.ensure_voice_client()  # 確保連接到語音頻道
        else:
            print("[DEBUG] 已經連接到語音頻道，無需再次連接。")

        source = await YTDLSource.from_url(url, loop=interaction.client.loop, stream=True)
        if not source:
            await interaction.followup.send(f"❌ 無法播放此鏈接：{url}", ephemeral=True)
            return
        
        title = source.title  
        print(f"[DEBUG] 已成功獲取播放源：{title}")

        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()

        c.execute('SELECT * FROM songs WHERE url = ?', (url,))
        song_exists = c.fetchone()

        if not song_exists:
            c.execute('INSERT INTO songs (url, title) VALUES (?, ?)', (url, title))
            conn.commit()
            print(f"[DEBUG] 新歌曲已添加到數據庫：{title}")
        else:
            print(f"[DEBUG] 歌曲已存在於數據庫：{title}")

        conn.close()

        async with player.lock:
            if insert:
                print(f"[DEBUG] 插入歌曲到優先播放隊列：{title}")
                await player.priority_queue.put(source)
            else:
                print(f"[DEBUG] 添加歌曲到普通播放隊列：{title}")
                await player.queue.put(source)

        if player.current is None:
            print("[DEBUG] 播放循環未運行，啟動播放循環...")
            await player.player_loop()

        if insert:
            await interaction.followup.send(f"🎶 已插入歌曲到播放隊列：**{title}**", ephemeral=True)
        else:
            await interaction.followup.send(f"🎶 已添加到播放列表：**{title}**", ephemeral=True)

    except Exception as e:
        error_message = f"❌ 發生錯誤：{str(e)}"
        print(f"[ERROR] 播放失敗：{error_message}")
        try:
            await interaction.followup.send(error_message, ephemeral=True)
        except discord.InteractionResponded:
            print("[DEBUG] 互動已回應，使用 `followup.send` 發送錯誤信息")
            await interaction.followup.send(error_message, ephemeral=True)



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
    """處理 list 指令的入口函數"""
    player = get_music_player(interaction)  
    await player.list()


@bot.tree.command(name='avatar', description='獲取用戶頭像')
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    
    embed = discord.Embed(
        title=f"{target.name} 的頭像",
        color=discord.Color.blue()
    )
    
    avatar_url = target.avatar.url if target.avatar else target.default_avatar.url
    embed.set_image(url=avatar_url)
    
    formats = ['png', 'jpg', 'webp']
    if target.avatar and target.avatar.is_animated():
        formats.append('gif')
    
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
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.InteractionResponded:
        await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name='avatarserver', description='獲取伺服器頭像')
async def avatarserver(interaction: discord.Interaction):
    guild = interaction.guild  # 取得伺服器對象

    if guild is None:
        await interaction.response.send_message("無法取得伺服器資訊。請在伺服器內執行此指令。", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"{guild.name} 的伺服器頭像",
        color=discord.Color.blue()
    )
    
    # 檢查伺服器是否有自訂圖標
    avatar_url = guild.icon.url if guild.icon else None
    if avatar_url:
        embed.set_image(url=avatar_url)
        
        formats = ['png', 'jpg', 'webp']
        if guild.icon.is_animated():
            formats.append('gif')
        
        links = []
        for fmt in formats:
            url = guild.icon.replace(format=fmt, size=1024).url
            links.append(f"[{fmt.upper()}]({url})")
        
        if links:
            embed.add_field(
                name="下載鏈接",
                value=" | ".join(links),
                inline=False
            )
    else:
        embed.description = "此伺服器沒有自訂圖標。"

    try:
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.InteractionResponded:
        await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name='avatarinserver', description='獲取用戶的伺服器自定義頭像')
async def avatarinserver(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user  # 默認為指令使用者
    
    # 獲取伺服器頭像或默認頭像
    avatar_url = target.guild_avatar.url if target.guild_avatar else None

    embed = discord.Embed(
        title=f"{target.name} 在伺服器的自定義頭像",
        color=discord.Color.blue()
    )

    if avatar_url:
        embed.set_image(url=avatar_url)
        
        # 支援的格式
        formats = ['png', 'jpg', 'webp']
        if target.guild_avatar and target.guild_avatar.is_animated():
            formats.append('gif')
        
        # 生成下載鏈接
        links = []
        for fmt in formats:
            url = target.guild_avatar.replace(format=fmt, size=1024).url
            links.append(f"[{fmt.upper()}]({url})")
        
        if links:
            embed.add_field(
                name="下載鏈接",
                value=" | ".join(links),
                inline=False
            )
    else:
        embed.description = f"{target.name} 沒有設置伺服器自定義頭像。"

    try:
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.InteractionResponded:
        await interaction.followup.send(embed=embed, ephemeral=True)

SONG_LIBRARY = [
    
    "https://www.youtube.com/watch?v=b9v1xfoWAFg",  
    "https://www.youtube.com/watch?v=6j0riQjd7Sc",  
    "https://www.youtube.com/watch?v=UyGJNeBcntk",  
    "https://www.youtube.com/watch?v=nmKS2mU9GOY",  
    "https://www.youtube.com/watch?v=qr9C5VWLGaw", 
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

@bot.tree.command(name='randomplay', description='隨機播放指定數量的歌曲(1-5首)')
async def randomplay(interaction: discord.Interaction, count: int = 1):
    try:
        if count < 1 or count > 5:
            await interaction.response.send_message("請指定1到5首歌曲。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        player = get_music_player(interaction)
        if not player:
            await interaction.followup.send("音樂播放器未啟動，請確保機器人已進入語音頻道。", ephemeral=True)
            return

        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()
        c.execute('SELECT url, title FROM songs ORDER BY RANDOM() LIMIT ?', (count,))
        selected_songs = c.fetchall()
        conn.close()

        if not selected_songs:
            await interaction.followup.send("數據庫中沒有可用的歌曲。", ephemeral=True)
            return

        messages = []
        for song_url, title in selected_songs:
            try:
                source = await YTDLSource.from_url(song_url, loop=player.bot.loop, stream=True)
                await player.queue.put(source)  
                messages.append(f"🎵 已添加到播放列表：{title}")
            except Exception as e:
                if "Video unavailable" in str(e):
                    messages.append(f"⚠️ 無法添加到播放列表：{title} (影片已被移除)")
                else:
                    messages.append(f"⚠️ 添加歌曲時發生錯誤：{title} ({str(e)})")
                continue  

        if player.current is None:
            player.current_user = interaction.user
            player.bot.loop.create_task(player.player_loop())  

        embed = discord.Embed(
            title=f"🎲 隨機播放 {count} 首歌曲",
            description="\n".join(messages),  
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"由 {interaction.user.name} 觸發的隨機播放")

        await interaction.followup.send(embed=embed, ephemeral=True)  
    except Exception as e:
        await interaction.followup.send(f"發生錯誤：{str(e)}", ephemeral=True)

@bot.tree.command(name='buildroom', description='創建一個臨時語音房間並上鎖')
async def buildroom(interaction: discord.Interaction, room_code: str):
    """
    創建一個臨時語音房間，房間代碼可以是任意 10 個字符以內。
    """
    if len(room_code) < 1 or len(room_code) > 10:
        await interaction.response.send_message("請提供 1 到 10 個字符的代碼作為房間名稱。", ephemeral=True)
        return

    guild = interaction.guild
    channel_name = f"尊貴包廂-{room_code}"

    category = discord.utils.get(guild.categories, name='語音頻道')

    voice_channel = await guild.create_voice_channel(
        channel_name,
        overwrites={
            guild.default_role: discord.PermissionOverwrite(connect=False),  
            interaction.user: discord.PermissionOverwrite(connect=True),    
        },
        category=category  
    )

    await interaction.response.send_message(f"已創建語音房間：{voice_channel.mention}，並已上鎖。", ephemeral=True)

    await asyncio.sleep(30)  
    while True:
        await bot.wait_until_ready()
        if not voice_channel.members:  
            await voice_channel.delete()  
            break
        await asyncio.sleep(10)  


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
        "小腿",
        "腳踝",
        "肱二頭肌",
        "十字韌帶"
        "髓鞘",
        "交感神經",
        "視覺中心",
        "脊椎，然後説一切其實都是你想得太多",
        "貓咪",
        "運動神經元，然後説其實只是自作多情",
        "微血管，然後説其實一個人也很好",
        "左心房，然後説其實只有你還想著那個她",
        "支氣管，然後説其實她早就忘了你",
        "氣囊，然後説其實她根本不在乎你",
        "虹膜，然後説其實那個她根本沒愛過你",
        
    ]
    
    response = random.choice(responses)
    body_part = random.choice(body_parts)
    
    await interaction.response.send_message(f"{response}（踩你{body_part}！！！）")

def create_table():
    conn = sqlite3.connect('marriage.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_names (
            user_id TEXT PRIMARY KEY,
            game_name TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

create_table()  

@bot.tree.command(name='myname', description='將你的逆水寒名稱加入數據庫')
async def myname(interaction: discord.Interaction, game_name: str):
    conn = sqlite3.connect('marriage.db')
    c = conn.cursor()
    
    c.execute('''
        INSERT INTO user_names (user_id, game_name) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET game_name=excluded.game_name
    ''', (str(interaction.user.id), game_name))
    
    conn.commit()
    conn.close()
    
    await interaction.response.send_message(f"你的逆水寒名稱已保存為：{game_name}")

@bot.tree.command(name='checkname', description='查看所有人的伺服器昵稱及逆水寒名稱')
async def checkname(interaction: discord.Interaction):
    conn = sqlite3.connect('marriage.db')
    c = conn.cursor()
    
    c.execute('SELECT user_id, game_name FROM user_names')
    user_names = c.fetchall()
    conn.close()
    
    embed = discord.Embed(title="伺服器昵稱及逆水寒名稱", color=discord.Color.blue())
    for user_id, game_name in user_names:
        member = interaction.guild.get_member(int(user_id))
        if member:
            embed.add_field(name=f"伺服器名稱：{member.display_name}", value=f"逆水寒名稱：{game_name}", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)  


@bot.tree.command(name='restnshname', description='清除指定用戶的逆水寒名稱')
async def restnshname(interaction: discord.Interaction, member: discord.Member):
    if interaction.user.id != 100000000000000003:
        await interaction.response.send_message("你沒有權限使用此命令。", ephemeral=True)
        return

    conn = sqlite3.connect('marriage.db')
    c = conn.cursor()
    
    c.execute('DELETE FROM user_names WHERE user_id = ?', (str(member.id),))
    conn.commit()
    conn.close()
    
    await interaction.response.send_message(f"{member.display_name} 的逆水寒名稱已被清除。")

@bot.tree.command(name='checkspecific', description='獲取指定用戶的逆水寒名稱')
async def check_specific(interaction: discord.Interaction, member: discord.Member):
    conn = sqlite3.connect('marriage.db')
    c = conn.cursor()
    
    c.execute('SELECT game_name FROM user_names WHERE user_id = ?', (str(member.id),))
    game_name = c.fetchone()
    conn.close()
    
    embed = discord.Embed(title=f"{member.display_name} 的逆水寒名稱", color=discord.Color.blue())
    
    if game_name:
        embed.add_field(name="逆水寒名稱", value=game_name[0], inline=False)
    else:
        embed.add_field(name="逆水寒名稱", value="該用戶沒有逆水寒名稱。", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

TARGET_GUILD_ID = 100000000000000001
SPECIFIC_ROLES = ["素問", "鐵衣", "血河", "碎夢", "九靈", "神相"]

@bot.tree.command(name="countchar", description="統計特定角色人數並更新頻道")
async def countchar(interaction: discord.Interaction):
    if interaction.guild.id != TARGET_GUILD_ID:
        await interaction.response.send_message("此指令僅限於特定伺服器使用。", ephemeral=True)
        return

    await interaction.response.defer()  

    guild = interaction.guild

    role_counts = {}
    for role in guild.roles:
        role_name = role.name.strip().lower()
        if role_name in [r.lower() for r in SPECIFIC_ROLES]:
            role_counts[role.name] = len(role.members)

    channel_ids = {
        "素問": 100000000000000005,
        "鐵衣": 100000000000000006,
        "血河": 100000000000000007,
        "碎夢": 100000000000000008,
        "九靈": 100000000000000009,
        "神相": 100000000000000010,
    }

    for role_name, channel_id in channel_ids.items():
        count = role_counts.get(role_name, 0)
        channel = guild.get_channel(channel_id)
        if channel:
            bot_member = guild.get_member(bot.user.id)
            permissions = channel.permissions_for(bot_member)
            if not permissions.manage_channels:
                print(f"機器人對頻道 {channel.name} 無管理權限。")
                continue
            await channel.edit(name=f"{role_name}: {count}位")
            await asyncio.sleep(1)  
        else:
            print(f"頻道 ID {channel_id} 不存在，無法更新名稱。")

    await interaction.followup.send("角色統計完成並更新頻道！")

@bot.tree.command(name='voteevent', description='創建一個投票事件')
async def vote_event(interaction: discord.Interaction, title: str, role: discord.Role, date_time: str, content: str, ps: str):
    await interaction.response.send_message(content="已發送！", ephemeral=True)  
    embed = discord.Embed(
        title=f"{title}",
        description=f"時間：{date_time} \n內容：{content} \n備註：{ps}",
        color=discord.Color.red()
    )
    embed.add_field(name="對應身份組", value=role.mention, inline=False)
    embed.set_footer(text="大當家敬上")  

    sent_message = await interaction.channel.send(embed=embed)

    await sent_message.add_reaction("✅")  
    await sent_message.add_reaction("❌") 

    await sent_message.channel.set_permissions(role, read_messages=True, send_messages=False)

@bot.tree.command(name='pin_announcement', description='置頂一則公告')
async def pin_announcement(interaction: discord.Interaction, message_id: str):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ 你沒有權限置頂消息！", ephemeral=True)
        return

    try:
        channel = interaction.channel
        message = await channel.fetch_message(int(message_id))

        await message.pin()
        await interaction.response.send_message(f"✅ 已成功置頂消息：{message.jump_url}", ephemeral=True)

    except discord.NotFound:
        await interaction.response.send_message("❌ 找不到該消息，請確認消息 ID 是否正確！", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Bot 沒有權限置頂該消息，請檢查 Bot 的頻道權限！", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"⚠️ 發生錯誤：{e}", ephemeral=True)

@bot.tree.command(name='skip', description='跳過當前正在播放的歌曲')
async def skip(interaction: discord.Interaction):
    player = get_music_player(interaction)
    await player.skip()
    await interaction.response.send_message("已跳過")

import discord
from discord.ext import commands

PROFESSION_ROLES = {
    "素問": "素問",
    "鐵衣": "鐵衣",
    "血河": "血河",
    "碎夢": "碎夢",
    "九靈": "九靈",
    "神相": "神相",
}

@bot.tree.command(name='team', description='分成三隊並顯示每隊成員及職業')
async def team(
    interaction: discord.Interaction,
    team1: str,
    team2: str,
    team3: str,
    duty1: str,
    duty2: str,
    duty3: str
):
    """
    接受三隊的用戶列表並顯示每隊成員及職業，並為每隊添加職責描述。
    :param interaction: Discord互動對象
    :param team1: 第一隊的用戶標籤列表
    :param team2: 第二隊的用戶標籤列表
    :param team3: 第三隊的用戶標籤列表
    :param duty1: 第一隊的職責描述
    :param duty2: 第二隊的職責描述
    :param duty3: 第三隊的職責描述
    """
    team_inputs = [team1, team2, team3]
    duties = [duty1, duty2, duty3]
    teams = [[tag.strip() for tag in team.split() if tag.strip()] for team in team_inputs]

    embed = discord.Embed(title="隊伍分配結果", color=discord.Color.blue())

    for i, (team, duty) in enumerate(zip(teams, duties), start=1):
        team_info = [f"職責：{duty}"] 
        for user_tag in team:
            if user_tag.startswith('<@') and user_tag.endswith('>'):  
                user_id = user_tag[2:-1].lstrip('!')  
                if user_id.isdigit():
                    member_obj = interaction.guild.get_member(int(user_id))
                    if member_obj:
                        roles = [role.name for role in member_obj.roles if role.name in PROFESSION_ROLES]
                        profession = roles[0] if roles else "未知職業"
                        team_info.append(f"{user_tag} - {profession}")
                    else:
                        team_info.append(f"{user_tag} - 無法找到該成員")
                else:
                    team_info.append(f"{user_tag} - 無效的用戶ID")
            else:
                team_info.append(f"{user_tag} - 無效的格式")

        embed.add_field(
            name=f"第{i}隊",
            value="\n".join(team_info) if team_info else f"職責：{duty}\n無成員",
            inline=False
        )

    await interaction.response.send_message("已發送！", ephemeral=True)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name='teamwork', description='設置每一隊每個職業的工作並指定隊長')
async def teamwork(
    interaction: discord.Interaction,
    team1_leader: str, team2_leader: str, team3_leader: str,  
    team1_素問: str = "", team1_鐵衣: str = "", team1_血河: str = "", team1_碎夢: str = "", team1_九靈: str = "", team1_神相: str = "",
    team2_素問: str = "", team2_鐵衣: str = "", team2_血河: str = "", team2_碎夢: str = "", team2_九靈: str = "", team2_神相: str = "",
    team3_素問: str = "", team3_鐵衣: str = "", team3_血河: str = "", team3_碎夢: str = "", team3_九靈: str = "", team3_神相: str = "",
):
    """
    設置每一隊的每個職業的工作內容，並指定每隊的隊長。
    """
    teams = {
        "第1隊": {
            "隊長": team1_leader,
            "素問": team1_素問 or "無",
            "鐵衣": team1_鐵衣 or "無",
            "血河": team1_血河 or "無",
            "碎夢": team1_碎夢 or "無",
            "九靈": team1_九靈 or "無",
            "神相": team1_神相 or "無",
        },
        "第2隊": {
            "隊長": team2_leader,
            "素問": team2_素問 or "無",
            "鐵衣": team2_鐵衣 or "無",
            "血河": team2_血河 or "無",
            "碎夢": team2_碎夢 or "無",
            "九靈": team2_九靈 or "無",
            "神相": team2_神相 or "無",
        },
        "第3隊": {
            "隊長": team3_leader,
            "素問": team3_素問 or "無",
            "鐵衣": team3_鐵衣 or "無",
            "血河": team3_血河 or "無",
            "碎夢": team3_碎夢 or "無",
            "九靈": team3_九靈 or "無",
            "神相": team3_神相 or "無",
        },
    }

    embed = discord.Embed(title="隊伍職業工作分配", color=discord.Color.green())

    for team_name, professions in teams.items():
        leader = professions.pop("隊長")
        team_info = "\n".join([f"{profession}: {work}" for profession, work in professions.items()])
        embed.add_field(
            name=f"{team_name}（隊長: {leader}）",
            value=team_info,
            inline=False
        )

    await interaction.response.send_message("已發送！", ephemeral=True)
    await interaction.followup.send(embed=embed)



@bot.tree.command(name='calculate', description='統計一個訊息下點擊了:white_check_mark:的所有成員')
async def calculate(interaction: discord.Interaction, message_id: str):
    """
    統計指定訊息中點擊了:white_check_mark:的成員，顯示其 @ 和職業統計。
    """
    try:
        if not message_id.isdigit():
            await interaction.response.send_message("請輸入有效的訊息 ID（僅限數字）。", ephemeral=True)
            return

        message_id = int(message_id)

        profession_roles = {
            "素問": "素問",
            "鐵衣": "鐵衣",
            "血河": "血河",
            "碎夢": "碎夢",
            "九靈": "九靈",
            "神相": "神相",
        }

        target_channel_id = 100000000000000011
        target_channel = bot.get_channel(target_channel_id)

        if not target_channel:
            await interaction.response.send_message("無法找到指定的頻道，請確認頻道 ID 是否正確。", ephemeral=True)
            return

        try:
            message = await target_channel.fetch_message(message_id)
        except discord.NotFound:
            await interaction.response.send_message("找不到指定的訊息，請確認訊息 ID 是否正確。", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message("沒有權限查看該訊息。", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"獲取訊息時發生錯誤：{e}", ephemeral=True)
            return

        reaction_users = []
        profession_counts = {profession: 0 for profession in profession_roles.values()}
        profession_counts["未知職業"] = 0  

        for reaction in message.reactions:
            if reaction.emoji == "✅":
                async for user in reaction.users():
                    if str(user.id) != "100000000000000012":  
                        member = target_channel.guild.get_member(user.id)
                        if member:
                            roles = [
                                role.name for role in member.roles
                                if role.name in profession_roles
                            ]
                            profession = roles[0] if roles else "未知職業"
                            reaction_users.append((user.mention, profession))
                            profession_counts[profession] += 1

        if not reaction_users:
            await interaction.response.send_message("沒有成員點擊 ✅。", ephemeral=True)
            return

        embed = discord.Embed(
            title="✅ 點擊統計結果",
            description=f"頻道: {target_channel.mention}\n訊息 ID: {message_id}",
            color=discord.Color.green()
        )

        embed.add_field(
            name="成員列表",
            value="\n".join(
                [f"{mention} - {profession}" for mention, profession in reaction_users]
            ),
            inline=False
        )

        embed.add_field(
            name="職業統計",
            value="\n".join(
                [f"{profession}: {count}" for profession, count in profession_counts.items()]
            ),
            inline=False
        )

        embed.set_footer(text=f"統計由 {interaction.user.name} 發起")

        await interaction.response.send_message(embed=embed, ephemeral=False)
    except Exception as e:
        await interaction.response.send_message(f"發生錯誤：{str(e)}", ephemeral=True)

GUILD_ID = 100000000000000001  
TARGET_ROLE_NAME = "成員"  
JOB_ROLES = ["素問", "血河", "碎夢", "鐵衣", "神相", "九靈"]  

@bot.tree.command(name="check_roles", description="檢查成員是否擁有職業身分組")
async def check_roles(ctx: discord.Interaction):
    """
    檢查伺服器中的成員是否擁有職業身分組，並通知未設置的成員。
    """
    await ctx.response.defer(ephemeral=True)

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        await ctx.followup.send("無法找到指定伺服器，請確認 GUILD_ID 是否正確。")
        return

    target_role = discord.utils.get(guild.roles, name=TARGET_ROLE_NAME)
    if not target_role:
        await ctx.followup.send(f"伺服器中未找到目標身分組 '{TARGET_ROLE_NAME}'。")
        return

    job_roles = [discord.utils.get(guild.roles, name=job) for job in JOB_ROLES]
    missing_roles = [job for job, role in zip(JOB_ROLES, job_roles) if role is None]

    if missing_roles:
        await ctx.followup.send(f"伺服器中缺少以下職業身分組：{', '.join(missing_roles)}")
        return

    notified_members = []

    for member in guild.members:
        if target_role in member.roles:
            if not any(role in member.roles for role in job_roles):
                try:
                    await member.send(
                        "你好！由於您未獲取職業身分組，無法查看幫會資訊。\n"
                        "請到頻道列表 > 職業領取 > 點擊自己對應的職業。"
                    )
                    notified_members.append(member.name)
                except discord.Forbidden:
                    await ctx.followup.send(f"無法私訊 {member.name}，可能關閉了私訊。")

    if notified_members:
        await ctx.followup.send(f"已通知以下成員設定職業身分組：{', '.join(notified_members)}")
    else:
        await ctx.followup.send("所有成員都已正確擁有職業身分組，無需通知。")

@bot.tree.command(name='limited_calculate', description='統計訊息中點擊 :white_check_mark: 的成員，並根據設定的職業數量限制進行篩選。')
async def limited_calculate(
    interaction: discord.Interaction, 
    message_id: str, 
    素問: int, 
    鐵衣: int, 
    血河: int, 
    碎夢: int, 
    九靈: int, 
    神相: int
):
    """
    統計指定訊息中點擊 :white_check_mark: 的成員，並根據指定職業限制進行篩選。
    """
    try:
        if not message_id.isdigit():
            await interaction.response.send_message("請輸入有效的訊息 ID（僅限數字）。", ephemeral=True)
            return

        message_id = int(message_id)

        profession_roles = {
            "素問": 素問,
            "鐵衣": 鐵衣,
            "血河": 血河,
            "碎夢": 碎夢,
            "九靈": 九靈,
            "神相": 神相,
        }

        profession_counts = {profession: 0 for profession in profession_roles.keys()}

        target_channel_id = 100000000000000011
        target_channel = bot.get_channel(target_channel_id)

        if not target_channel:
            await interaction.response.send_message("無法找到指定的頻道，請確認頻道 ID 是否正確。", ephemeral=True)
            return

        try:
            message = await target_channel.fetch_message(message_id)
        except discord.NotFound:
            await interaction.response.send_message("找不到指定的訊息，請確認訊息 ID 是否正確。", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message("沒有權限查看該訊息。", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"獲取訊息時發生錯誤：{e}", ephemeral=True)
            return

        reaction_users = []
        for reaction in message.reactions:
            if reaction.emoji == "✅":
                async for user in reaction.users():
                    if str(user.id) != "100000000000000012":  
                        member = target_channel.guild.get_member(user.id)
                        if member:
                            roles = [
                                role.name for role in member.roles
                                if role.name in profession_roles
                            ]
                            profession = roles[0] if roles else None
                            if profession:
                                if profession_counts[profession] < profession_roles[profession]:
                                    profession_counts[profession] += 1
                                    reaction_users.append((user.mention, profession))

        if not reaction_users:
            await interaction.response.send_message("沒有符合條件的成員點擊 ✅。", ephemeral=True)
            return

        embed = discord.Embed(
            title="✅ 點擊統計結果（有數量限制）",
            description=f"頻道: {target_channel.mention}\n訊息 ID: {message_id}",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="成員列表",
            value="\n".join(
                [f"{mention} - {profession}" for mention, profession in reaction_users]
            ),
            inline=False
        )

        embed.add_field(
            name="職業統計",
            value="\n".join(
                [f"{profession}: {count} / {limit}" for profession, (count, limit) in zip(profession_counts.keys(), profession_roles.values())]
            ),
            inline=False
        )

        embed.set_footer(text=f"統計由 {interaction.user.name} 發起")

        await interaction.response.send_message(embed=embed, ephemeral=False)
    except Exception as e:
        await interaction.response.send_message(f"發生錯誤：{str(e)}", ephemeral=True)

stored_vote_data = {}

def save_vote_data(data):
    with open("vote_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def load_vote_data():
    try:
        with open("vote_data.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def build_embed(data):
    embed = discord.Embed(
        title=data["title"],
        description=data["content"],
        color=discord.Color.orange()
    )

    embed.add_field(name="時間", value=data["date_time"], inline=False)
    embed.add_field(name="備註", value=data["ps"], inline=False)

    for emoji, job in data["reactions"].items():
        current_count = len(data["reaction_users"].get(emoji, []))
        limit = data["reaction_limits"][emoji]
        value = f"已投票人數���{current_count}/{limit}\n無"
        embed.add_field(name=f"{emoji} {job}", value=value, inline=True)

    embed.set_footer(text="大當家敬上")
    return embed

@bot.tree.command(name='limited_vote', description='創建一個投票事件')
async def limited_vote(interaction: discord.Interaction, title: str, role: discord.Role, date_time: str, content: str, ps: str, limits: str):
    reactions = {
        "<:image_transparent:100000000000000013>": "素問",
        "<:image_transparent_v2:100000000000000014>": "九靈",
        "<:image_transparent_v5:100000000000000015>": "鐵衣",
        "<:image_transparent_v4:100000000000000016>": "碎夢",
        "<:image_transparent_v8:100000000000000017>": "血河",
        "<:image_transparent_v7:100000000000000018>": "神相"
    }

    try:
        limit_values = [int(x) for x in limits.split(",")]
        if len(limit_values) != len(reactions):
            raise ValueError("請確保提供了 6 個數量限制，用逗號分隔。")
    except ValueError:
        await interaction.response.send_message("數量限制格式錯誤！請提供 6 個數字，例如 `3,3,3,3,3,3`。", ephemeral=True)
        return

    reaction_limits = {emoji: limit for emoji, limit in zip(reactions.keys(), limit_values)}
    reaction_users = {emoji: [] for emoji in reactions.keys()}

    data = {
        "reaction_users": reaction_users,
        "reaction_limits": reaction_limits,
        "reactions": reactions,
        "role_id": role.id,
        "title": title,
        "content": content,
        "ps": ps,
        "date_time": date_time,
        "channel_id": interaction.channel.id
    }

    await interaction.response.send_message(content="投票已創建！", ephemeral=True)
    vote_message = await interaction.channel.send(embed=build_embed(data))

    for emoji in reactions.keys():
        await vote_message.add_reaction(emoji)

    data["message_id"] = vote_message.id
    stored_vote_data[vote_message.id] = data
    save_vote_data(stored_vote_data)

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    message_id = reaction.message.id
    if message_id not in stored_vote_data:
        return

    data = stored_vote_data[message_id]
    emoji = str(reaction.emoji)

    if emoji not in data["reactions"]:
        return

    if len(data["reaction_users"][emoji]) >= data["reaction_limits"][emoji]:
        await reaction.remove(user)
        await user.send(f"抱歉，{data['reactions'][emoji]} 的名額已滿。")
    elif user.id not in data["reaction_users"][emoji]:
        data["reaction_users"][emoji].append(user.id)
        await reaction.message.edit(embed=build_embed(data))
        save_vote_data(stored_vote_data)

@bot.event
async def on_reaction_remove(reaction, user):
    if user.bot:
        return

    message_id = reaction.message.id
    if message_id not in stored_vote_data:
        return

    data = stored_vote_data[message_id]
    emoji = str(reaction.emoji)

    if emoji in data["reactions"] and user.id in data["reaction_users"][emoji]:
        data["reaction_users"][emoji].remove(user.id)
        await reaction.message.edit(embed=build_embed(data))
        save_vote_data(stored_vote_data)

dsl_quotes = [
    "DSL 是我們的 MVP：Most Valuable Pillow",
    "DSL 是 CPU：Central Pillow Unit",
    "DSL 是 GPS：Greatest Professional Sleeper",
    "DSL 是 USB：Ultimate Sleep Boss",
    "DSL 是 VIP：Very Important Pillow-lover",
    "DSL 是 WiFi：Will Fall Immediately",
    "DSL 是 AI：Always Idle",
    "DSL 是 CEO：Chief Eating Officer",
    "DSL 是 CFO：Chief Forgetful Officer",
    "DSL 是 LOL：Legend of Laziness",
    "DSL 是 WTF：Where's The Food?",
    "DSL 是 TNT：Totally Needs Time",
    "DSL 是 NASA：Never Awake, Sleeping Always",
    "DSL 是 FBI：Food Before Initiative",
    "DSL 是 ATM：Always Tired Mode",
    "DSL 是 PM：Pillow Master",
    "DSL 是 AM：Always Moaning",
    "DSL 是 404：Brain Not Found",
    "DSL 是 RPG：Really Professional Gamer",
    "DSL 是 NBA：Never Bother to Act",
    "DSL 是 PUBG：Pillow Ultimate Battle Ground",
    "DSL 是 OMG：Oh My Gosh, Sleeping Again!",
    "DSL 是 IT：Idle Time",
    "DSL 是 BBQ：Biggest Belly Quality",
    "DSL 是 LMFAO：Lazy Mode Fully Activated, Obviously",
    "DSL 是 EAT：Every Afternoon Tired",
    "DSL 是 LOL：Lover of Lunch",
    "DSL 是 DNS：Did Not Start",
    "DSL 是 MBA：Master of Being Absent",
    "DSL 是 AI：Absent Intellect",
    "DSL 是 BOSS：Biggest Of Sleep Society",
    "DSL 是 PPT：Pillow, Pillow, Tired",
    "DSL 是 UNO：Ultimate Nap Organizer",
    "DSL 是 VIP：Very Idle Person",
    "DSL 是 ICQ：I Can Quit",
    "DSL 是 NBA：Never Been Active",
    "DSL 是 SOS：Sleep Over Study",
    "DSL 是 CPU：Constantly Procrastinating User",
    "DSL 是 URL：Ultimate Rest Lover",
    "DSL 是 PDF：Pretty Darn Forgetful",
    "DSL 是 CCTV：Can't Concentrate, Too Very Tired",
    "DSL 是 GYM：Greatest Yawning Master",
    "DSL 是 GTA：Go To Asleep",
    "DSL 是 WHO：What Happened to Others?",
    "DSL 是 DIY：Do It Yesterday",
    "DSL 是 OKR：Only Knows Resting",
    "DSL 是 IOS：I Overslept",
    "DSL 是 HDMI：Heavy Dozing, Mostly Idle",
    "DSL 是 LCD：Laziness Can't Disappear",
    "DSL 是 RAM：Rarely Active Mode",
    "DSL 是 SSD：Super Sleepy Dude",
    "DSL 是 ESC：Easily Sleeps Constantly",
    "DSL 是 VIP：Very Idle Potato",
    "DSL 是 DOC：Dreaming of Couches",
    "DSL 是 LOL：Lord of Laziness",
    "DSL 是 DDR：Daily Dozing Routine",
    "DSL 是 UNO：Unstoppable Nap Operator",
    "DSL 是 CAP：Constantly Avoiding Productivity",
    "DSL 是 RPG：Rest, Pillow, Go!",
    "DSL 是 MS：Master Sleeper",
    "DSL 是 MBA：Master of Being Asleep",
    "DSL 是 OOO：Only On Off-mode",
    "DSL 是 WIFI：Why Is Food Important?",
    "DSL 是 DIY：Done It Yesterday",
    "DSL 是 GPS：Go Pillow Sleeping",
    "DSL 是 FBI：Forever Being Idle",
    "DSL 是 NBA：Nothing But Asleep",
    "DSL 是 UFO：Ultimate Food Organizer",
    "DSL 是 LOL：Living On Laziness",
    "DSL 是 TNT：Tired Now, Thanks",
    "DSL 是 QQQ：Quickly Quit Quietly",
    "DSL 是 UNO：Ultra Nap Operator",
    "DSL 是 BBQ：Best Bed Quality",
    "DSL 是 CEO：Chief Eating Operator",
    "DSL 是 VIP：Very Interesting Potato",
    "DSL 是 AI：Always Inactive",
    "DSL 是 ATM：All-Time Moody",
    "DSL 是 KFC：Keep Falling Continuously",
    "DSL 是 PDF：Permanently Dozing Forever",
    "DSL 是 NASA：Napping And Snoring Always",
    "DSL 是 BMW：Bored, Moody, Weak",
    "DSL 是 VIP：Very Impressive Procrastinator",
    "DSL 是 WHO：What Happened to Others?",
    "DSL 是 WWE：World Wide Eating",
    "DSL 是 CPU：Can't Pick Up",
    "DSL 是 JAVA：Just Always Very Asleep",
    "DSL 是 SMS：Sleep More, Smile",
    "DSL 是 ABC：Always Being Chill",
    "DSL 是 ML：Master of Laziness",
    "DSL 是 404：DSL Not Found",
    "DSL 是 DOA：Dead on Arrival",
    "DSL 是 PAC：Pillow Always Comes",
    "DSL 是 MIA：Missing In Action",
    "DSL 是 DOC：Dreaming of Comfort",
    "DSL 是 FYI：For Your Idleness",
    "DSL 是 SOS：So Over Sleeping",
    "DSL 是 YOLO：You Only Lie Once",
    "DSL 是 MVP：Maximum Vexed Procrastinator"
]

@bot.tree.command(name="dsl", description="嘻嘻哈哈啦啦啦")
async def dsl(interaction: discord.Interaction):
    response = random.choice(dsl_quotes)
    await interaction.response.send_message(response)  

@bot.tree.command(name='reset', description='重置音樂播放器，解決連接問題')
async def reset(interaction: discord.Interaction):
    try:
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect(force=True) 

        if interaction.guild.id in music_players:
            del music_players[interaction.guild.id]
        
        await interaction.response.send_message("✅ 音樂播放器已重置！現在你可以重新播放音樂了。")
    except Exception as e:
        try:
            await interaction.response.send_message(f"❌ 重置失敗：{str(e)}")
        except discord.InteractionResponded:
            await interaction.followup.send(f"❌ 重置失敗：{str(e)}")

music_players = {}  

def get_music_player(interaction: discord.Interaction) -> MusicPlayer:
    """
    獲取或初始化 MusicPlayer 實例，通過全局字典管理
    """
    if not interaction.guild:
        raise ValueError("❌ 指令必須在伺服器內執行，無法在私聊中使用！")

    guild_id = interaction.guild.id
    if guild_id in music_players:
        print(f"[DEBUG] 從全局字典中獲取伺服器 {guild_id} 的 MusicPlayer 實例")
        return music_players[guild_id]
    print(f"[DEBUG] 為伺服器 {guild_id} 創建新的 MusicPlayer 實例")
    player = MusicPlayer(interaction)
    music_players[guild_id] = player
    return player
   


@bot.tree.command(name='searchplay', description='根據關鍵字搜索並播放歌曲，可選擇插入到當前播放的下一首（僅當 autoplay 運行時生效）。')
async def searchplay(interaction: discord.Interaction, *, query: str, insert: bool = False):
    try:
        print(f"[DEBUG] 收到搜索播放請求 - Query: {query}, Insert: {insert}")
        await interaction.response.defer(ephemeral=True)  

        player = get_music_player(interaction)
        
        if not player.voice_client or not player.voice_client.is_connected():
            await player.ensure_voice_client() 
        else:
            print("[DEBUG] 已經連接到語音頻道，無需再次連接。")

        if player.is_downloading:  
            await interaction.followup.send("❌ 目前正在下載歌曲，請稍後再試。", ephemeral=True)
            return

        results = await YTDLSource.search_results(query, loop=interaction.client.loop)
        if not results:
            await interaction.followup.send(f"❌ 未找到與 `{query}` 匹配的歌曲。", ephemeral=True)
            return

        available_songs = []
        for result in results:
            try:
                youtube_url = result['webpage_url']
                title = result['title']
                available_songs.append({
                    "url": youtube_url,
                    "title": title
                })
            except Exception as e:
                print(f"[ERROR] 處理歌曲時出現錯誤：{e}")
                continue  # 跳過此歌曲

        if not available_songs:
            await interaction.followup.send("❌ 沒有可用的歌曲。", ephemeral=True)
            return

        class SongSelectionView(View):
            def __init__(self):
                super().__init__(timeout=60)
                options = [
                    discord.SelectOption(label=song["title"], description="點擊選擇此歌曲", value=song["url"])
                    for song in available_songs
                ]
                self.add_item(SongSelect(options))

        class SongSelect(Select):
            def __init__(self, options):
                super().__init__(placeholder="請選擇一首歌曲...", options=options)

            async def callback(self, interaction: discord.Interaction):
                selected_url = self.values[0]  
                selected_song = next(song for song in available_songs if song["url"] == selected_url)
                title = selected_song["title"]

                player.is_downloading = True  

                try:
                    # 在這裡下載 m3u8 文件
                    source = await YTDLSource.from_url(selected_url, loop=interaction.client.loop, stream=True)

                    conn = sqlite3.connect('marriage.db')
                    c = conn.cursor()

                    c.execute('SELECT * FROM songs WHERE url = ?', (selected_url,))
                    song_exists = c.fetchone()

                    if not song_exists:
                        c.execute('INSERT INTO songs (url, title) VALUES (?, ?)', (selected_url, title))
                        conn.commit()
                        print(f"[DEBUG] 新歌曲已添加到數據庫：{title}")
                    else:
                        print(f"[DEBUG] 歌曲已存在於數據庫：{title}")

                    conn.close()

                    async with player.lock:
                        if insert:
                            print(f"[DEBUG] 插入歌曲到優先播放隊列：{title}")
                            await player.priority_queue.put(source)
                        else:
                            print(f"[DEBUG] 添加歌曲到普通播放隊列：{title}")
                            await player.queue.put(source)

                        if player.current is None:
                            print("[DEBUG] 啟動播放循環...")
                            await player.player_loop()

                    await interaction.followup.send(
                        f"✅ 已將歌曲 **{title}** 添加到播放列表！", ephemeral=True
                    )
                    self.view.stop()

                except Exception as e:
                    print(f"[ERROR] 下載歌曲失敗：{e}")
                    await interaction.followup.send(f"❌ 歌曲 **{title}** 下載失敗，已跳過。", ephemeral=True)

                finally:
                    player.is_downloading = False  

        view = SongSelectionView()
        await interaction.followup.send(
            f"🎵 找到以下可用歌曲，請選擇播放：",
            view=view,
            ephemeral=True
        )

    except Exception as e:
        print(f"[ERROR] 搜索並播放失敗：{e}")
        await interaction.followup.send(f"❌ 搜索並播放失敗：{str(e)}", ephemeral=True)

@bot.event
async def on_voice_state_update(member, before, after):
    if member == bot.user:
        if before.channel is not None and after.channel is None:
            guild_id = before.channel.guild.id

            if guild_id in music_players:
                del music_players[guild_id]
                
TARGET_CHANNEL_IDD = 100000000000000019
TARGET_SERVER_IDD = 100000000000000001  

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.application_command:
        if interaction.guild and interaction.guild.id == TARGET_SERVER_IDD:
            target_channel = bot.get_channel(TARGET_CHANNEL_IDD)

            if target_channel:
                try:
                    await target_channel.send(
                        f"指令觸發：`/{interaction.command.name}`\n"
                        f"用戶：{interaction.user} ({interaction.user.id})\n"
                        f"內容：{interaction.data}"
                    )
                except discord.Forbidden:
                    if interaction.response.is_done():
                        await interaction.followup.send("機器人沒有權限發送訊息到目標頻道。", ephemeral=True)
                    else:
                        await interaction.response.send_message("機器人沒有權限發送訊息到目標頻道。", ephemeral=True)
                except discord.HTTPException as e:
                    if interaction.response.is_done():
                        await interaction.followup.send(f"無法發送訊息到目標頻道: {e}", ephemeral=True)
                    else:
                        await interaction.response.send_message(f"無法發送訊息到目標頻道: {e}", ephemeral=True)
            else:
                if interaction.response.is_done():
                    await interaction.followup.send("找不到目標頻道！", ephemeral=True)
                else:
                    await interaction.response.send_message("找不到目標頻道！", ephemeral=True)
        

'''@bot.tree.command(name='yeyeye', description='讓指定玩家的身分組在300秒內每0.5秒增加和減少')
async def yeyeye(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if interaction.user.id != 100000000000000003:
        await interaction.response.send_message("你沒有權限使用此指令。", ephemeral=True)
        return

    if interaction.guild.id != 100000000000000001:
        await interaction.response.send_message("此指令只能在特定伺服器中使用。", ephemeral=True)
        return

    if interaction.guild.me.top_role <= role:
        await interaction.response.send_message(f"我無法操作 {role.name} 身分組，請確保我的身分組位於該角色之上。", ephemeral=True)
        return

    try:
        await interaction.response.defer(ephemeral=True)  

        
        duration = 300  
        interval = 0.5  
        end_time = discord.utils.utcnow().timestamp() + duration

        while discord.utils.utcnow().timestamp() < end_time:
            if role in member.roles:
                try:
                    await member.remove_roles(role)
                except discord.Forbidden:
                    await interaction.followup.send(f"無法移除 {role.name} 身分組，權限不足。", ephemeral=True)
                    return
            else:
                try:
                    await member.add_roles(role)
                except discord.Forbidden:
                    await interaction.followup.send(f"無法添加 {role.name} 身分組，權限不足。", ephemeral=True)
                    return

            await asyncio.sleep(interval)

        await interaction.followup.send(f"完成對 {member.mention} 的身分組變更操作。", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"發生錯誤：{e}", ephemeral=True)'''

async def select_callback(interaction: discord.Interaction):
    selected_url = interaction.data['values'][0]  

    if not interaction.user.voice:
        await interaction.response.send_message("您需要在語音頻道中，才能讓機器人播放歌曲。", ephemeral=True)
        return

    player = get_music_player(interaction)

    try:
        source = await YTDLSource.from_url(selected_url, loop=player.bot.loop, stream=True)
        await player.queue.put(source)  
        await interaction.response.send_message(f"🎵 已添加到播放列表：{source.title}", ephemeral=False)

        if player.current is None:
            player.current_user = interaction.user
            player.bot.loop.create_task(player.player_loop())

    except Exception as e:
        await interaction.response.send_message(f"⚠️ 添加歌曲時發生錯誤：{str(e)}", ephemeral=True)

cc = OpenCC('t2s')  
cc_reverse = OpenCC('s2t')  


async def player_loop(player):
    """播放循環：根據模式播放歌曲"""
    while True:
        try:
            if autoplay_running and not player.priority_queue.empty():
                next_song = await player.priority_queue.get()
                print(f"🎵 [調試] 發現插入歌曲：{next_song.title}（自動播放模式）")
            else:
                next_song = await player.queue.get()
                print(f"🎵 [調試] 播放正常模式的歌曲：{next_song.title}")

            player.current = next_song
            async with player.lock:
                player.voice_client.play(
                    next_song.source,
                    after=lambda _: player.bot.loop.call_soon_threadsafe(player.next.set)
                )

            print(f"🎶 [調試] 正在播放：{next_song.title}")

            await player.next.wait()

            player.current.cleanup()
            player.current = None
            player.next.clear()

        except Exception as e:
            print(f"❌ [調試] 播放出錯：{e}")
            break


@bot.tree.command(name="internalsearchplay", description="在資料庫中搜尋歌曲並播放，可選擇插入到當前播放的下一首（僅當 autoplay 運行時生效）。")
async def internalsearchplay(interaction: discord.Interaction, keyword: str, insert: bool = False):
    try:
        print(f"[DEBUG] 收到內部搜索播放請求 - Keyword: {keyword}, Insert: {insert}")
        await interaction.response.defer(ephemeral=True)  # 回應靜音且只顯示給用戶

        # 連接資料庫並查詢匹配的歌曲
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()
        c.execute("SELECT url, title FROM songs WHERE title LIKE ?", (f"%{keyword}%",))
        results = c.fetchall()
        conn.close()

        if not results:
            await interaction.followup.send("❌ 沒有找到匹配的歌曲。", ephemeral=True)
            return

        # 將資料庫的查詢結果整理為歌曲選項
        available_songs = []
        for row in results:
            youtube_url = row[0]
            title = row[1]
            try:
                print(f"[DEBUG] 嘗試加載歌曲：{title} ({youtube_url})")
                source = await YTDLSource.from_url(youtube_url, loop=interaction.client.loop, stream=True)
                available_songs.append({
                    "url": youtube_url,
                    "title": title,
                    "source": source
                })
            except Exception as e:
                print(f"[WARNING] 無法加載歌曲：{title} - {e}")
                continue

        if not available_songs:
            await interaction.followup.send("❌ 無法加載任何可用歌曲。", ephemeral=True)
            return

        class SongSelectionView(View):
            def __init__(self):
                super().__init__(timeout=60)
                options = [
                    discord.SelectOption(label=song["title"], description="點擊選擇此歌曲", value=str(idx))
                    for idx, song in enumerate(available_songs)
                ]
                self.add_item(SongSelect(options))

        class SongSelect(Select):
            def __init__(self, options):
                super().__init__(placeholder="請選擇一首歌曲...", options=options)

            async def callback(self, interaction: discord.Interaction):
                selected_index = int(self.values[0])
                selected_song = available_songs[selected_index]
                title = selected_song["title"]
                youtube_url = selected_song["url"]
                source = selected_song["source"]

                conn = sqlite3.connect('marriage.db')
                c = conn.cursor()
                c.execute('SELECT * FROM songs WHERE url = ?', (youtube_url,))
                song_exists = c.fetchone()

                if not song_exists:
                    c.execute('INSERT INTO songs (url, title) VALUES (?, ?)', (youtube_url, title))
                    conn.commit()
                    print(f"[DEBUG] 新歌曲已添加到數據庫：{title}")
                else:
                    print(f"[DEBUG] 歌曲已存在於數據庫：{title}")

                conn.close()

                player = get_music_player(interaction)
                await player.ensure_voice_client()

                async with player.lock:
                    if insert:
                        print(f"[DEBUG] 插入歌曲到優先播放隊列：{title}")
                        await player.priority_queue.put(source)
                    else:
                        print(f"[DEBUG] 添加歌曲到普通播放隊列：{title}")
                        await player.queue.put(source)

                    if player.current is None:
                        print("[DEBUG] 播放循環未運行，啟動播放循環...")
                        await player.player_loop()

                await interaction.response.send_message(
                    f"✅ 已將歌曲 **{title}** 添加到播放列表！", ephemeral=True
                )
                self.view.stop()

        view = SongSelectionView()
        await interaction.followup.send(
            f"🎵 找到以下匹配的歌曲，請選擇播放：",
            view=view,
            ephemeral=True
        )

    except Exception as e:
        print(f"[ERROR] 內部搜索播放失敗：{e}")
        await interaction.followup.send(f"❌ 搜索並播放失敗：{str(e)}", ephemeral=True)




'''@bot.tree.command(name="reload", description="重新加載指定模組")
async def reload_module(interaction: discord.Interaction, module_name: str):
    """重新加載指定的模組"""
    if module_name in sys.modules:
        try:
            importlib.reload(sys.modules[module_name])
            await interaction.response.send_message(f"模組 `{module_name}` 已重新加載。")
        except Exception as e:
            await interaction.response.send_message(f"重載模組 `{module_name}` 時發生錯誤：{e}")
    else:
        await interaction.response.send_message(f"模組 `{module_name}` 未找到。")'''

DB_PATH = 'marriage.db'


TIME_FORMAT = "%H:%M"
LOCAL_TIMEZONE = pytz.timezone("Asia/Taipei") 

def create_tables():
    """創建必要的資料表，並確保資料表結構正確"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 創建用戶名稱表
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_names (
            user_id TEXT PRIMARY KEY,
            game_name TEXT NOT NULL
        )
    ''')

    # 創建公告表（如果不存在）
    c.execute('''
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id BIGINT NOT NULL,
            message TEXT NOT NULL,
            send_time TIME NOT NULL,
            week_day TEXT DEFAULT 'all'
        )
    ''')

    # 動態檢查並添加缺失的欄位
    try:
        # 嘗試添加 music_url 欄位
        c.execute("ALTER TABLE announcements ADD COLUMN music_url TEXT")
        print("[INFO] 已添加欄位：music_url")
    except sqlite3.OperationalError:
        print("[DEBUG] 欄位 music_url 已存在，無需添加")

    try:
        # 嘗試添加 voice_channel_id 欄位
        c.execute("ALTER TABLE announcements ADD COLUMN voice_channel_id BIGINT")
        print("[INFO] 已添加欄位：voice_channel_id")
    except sqlite3.OperationalError:
        print("[DEBUG] 欄位 voice_channel_id 已存在，無需添加")

    conn.commit()
    conn.close()

# 調用函數
create_tables()

async def schedule_announcements():
    """定時檢查並發送公告或播放音樂"""
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()

            now = datetime.now(LOCAL_TIMEZONE)
            current_time = now.strftime(TIME_FORMAT)
            previous_time = (now - timedelta(minutes=1)).strftime(TIME_FORMAT)
            current_week_day = now.strftime("%A")


            c.execute("""
                SELECT id, channel_id, message, send_time, week_day, music_url, voice_channel_id
                FROM announcements
                WHERE send_time > ? AND send_time <= ? AND (week_day = ? OR week_day = 'all')
            """, (previous_time, current_time, current_week_day))
            rows = c.fetchall()


            for row in rows:
                announcement_id, channel_id, message, send_time, week_day, music_url, voice_channel_id = row

                # 發送文字公告
                channel = bot.get_channel(channel_id)
                if not channel:
                    print(f"[ERROR] 無法找到文字頻道，ID: {channel_id}")
                    continue

                if message:
                    await channel.send(message)
                    print(f"[INFO] 公告已成功發送到文字頻道 {channel.name} (ID: {channel_id})：{message}")

                # 處理音樂公告
                if music_url and voice_channel_id:
                    voice_channel = bot.get_channel(voice_channel_id)
                    if not voice_channel or not isinstance(voice_channel, discord.VoiceChannel):
                        print(f"[ERROR] 無法找到語音頻道，ID: {voice_channel_id}")
                        continue

                    try:
                        # 使用 MusicPlayer 確保連接到語音頻道
                        player = get_music_player(voice_channel.guild)  # 假設你有一個 guild-based 的 MusicPlayer 管理
                        await player.ensure_voice_client(voice_channel)

                        # 加入音樂到播放隊列
                        source = await YTDLSource.from_url(music_url, loop=asyncio.get_event_loop(), stream=True)
                        async with player.lock:
                            print(f"[DEBUG] 添加音樂到播放隊列：{music_url}")
                            await player.queue.put(source)

                            # 如果沒有正在播放的音樂，啟動播放
                            if player.current is None:
                                print("[DEBUG] 啟動播放循環...")
                                await player.player_loop()

                    except Exception as e:
                        print(f"[ERROR] 播放音樂時發生錯誤：{e}")

                # 刪除已處理的公告
                c.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
                conn.commit()

            conn.close()

        except Exception as e:
            print(f"[ERROR] 公告發送時發生錯誤：{e}")

        await asyncio.sleep(60)

@bot.tree.command(name="add_announcement", description="添加一條公告")
async def add_announcement(interaction: discord.Interaction, channel: discord.TextChannel, send_time: str, week_day: str, message: str):
    """添加定時公告"""
    try:
        send_time = datetime.strptime(send_time, "%H:%M").strftime("%H:%M")
        valid_days = ["all", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    
        if week_day not in valid_days:
            raise ValueError(f"無效的星期參數：{week_day}，有效值為 {valid_days}")

        print(f"[DEBUG] 添加公告: 頻道={channel.id}, 時間={send_time}, 星期={week_day}, 內容={message}")

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO announcements (channel_id, message, send_time, week_day)
            VALUES (?, ?, ?, ?)
        """, (channel.id, message, send_time, week_day))
        conn.commit()
        conn.close()

        await interaction.response.send_message(f"公告已添加：將於 {send_time} (UTC+8) 每 {week_day if week_day != 'all' else '天'} 發送到頻道 {channel.mention}！")
    except ValueError as e:
        await interaction.response.send_message(f"錯誤：{e}", ephemeral=True)
    except Exception as e:
        print(f"[ERROR] 添加公告時出現錯誤：{e}")
        await interaction.response.send_message(f"添加公告時出現錯誤：{e}", ephemeral=True)

'''@bot.tree.command(name="add_music_announcement", description="添加一條定時音樂公告，指定語音頻道播放音樂")
async def add_music_announcement(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    send_time: str,
    week_day: str,
    music_url: str,
    voice_channel: discord.VoiceChannel = None,
    message: str = None
):
    """添加定時音樂公告，支持指定語音頻道播放音樂"""
    try:
        # 格式化發送時間
        send_time = datetime.strptime(send_time, "%H:%M").strftime("%H:%M")
        valid_days = ["all", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

        # 驗證星期參數
        if week_day not in valid_days:
            raise ValueError(f"無效的星期參數：{week_day}，有效值為 {valid_days}")

        # 必須至少提供消息或音樂 URL
        if not message and not music_url:
            raise ValueError("公告必須至少包含消息內容或音樂 URL")

        # 如果指定了語音頻道，檢查是否有權限
        if voice_channel:
            if not voice_channel.permissions_for(interaction.guild.me).connect:
                raise PermissionError(f"機器人無法連接語音頻道：`{voice_channel.name}`，請檢查權限設置")

            if not voice_channel.permissions_for(interaction.guild.me).speak:
                raise PermissionError(f"機器人無法在語音頻道：`{voice_channel.name}` 中發言，請檢查權限設置")

        # 提取語音頻道的 ID（如果存在）
        voice_channel_id = voice_channel.id if voice_channel else None

        # 調試輸出
        print(f"[DEBUG] 添加音樂公告: 頻道={channel.id}, 時間={send_time}, 星期={week_day}, 音樂={music_url}, 語音頻道={voice_channel_id}")

        # 插入數據到資料庫
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO announcements (channel_id, message, send_time, week_day, music_url, voice_channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (channel.id, message, send_time, week_day, music_url, voice_channel_id))
        conn.commit()
        conn.close()

        # 構建響應消息
        response_message = (
            f"音樂公告已添加：將於 {send_time} (UTC+8) 每 {week_day if week_day != 'all' else '天'} 發送到頻道 {channel.mention}"
        )
        if music_url:
            response_message += f"，並播放音樂 `{music_url}`"
        if voice_channel:
            response_message += f" 在語音頻道 `{voice_channel.name}` 中播放。"

        # 向用戶回覆成功消息
        await interaction.response.send_message(response_message)

    except ValueError as e:
        await interaction.response.send_message(f"錯誤：{e}", ephemeral=True)
    except PermissionError as e:
        await interaction.response.send_message(f"權限錯誤：{e}", ephemeral=True)
    except Exception as e:
        print(f"[ERROR] 添加音樂公告時出現錯誤：{e}")
        await interaction.response.send_message(f"添加音樂公告時出現錯誤：{e}", ephemeral=True)'''


@bot.tree.command(name="list_announcements", description="列出所有公告")
async def list_announcements(interaction: discord.Interaction):
    """列出所有公告"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, channel_id, message, send_time FROM announcements")
    rows = c.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("目前沒有已設置的公告。")
        return

    message = "**定時公告列表：**\n"
    for row in rows:
        id, channel_id, message_text, send_time = row
        message += f"ID: {id}, 頻道: {channel_id}, 時間: {send_time} (UTC+8), 公告內容: {message_text}\n"

    print(f"[DEBUG] 公告列表:\n{message}")
    await interaction.response.send_message(message)

@bot.tree.command(name="delete_announcement", description="刪除指定公告")
async def delete_announcement(interaction: discord.Interaction, announcement_id: int):
    """刪除公告"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
    conn.commit()
    conn.close()

    print(f"[DEBUG] 刪除公告 ID: {announcement_id}")
    await interaction.response.send_message(f"公告 ID {announcement_id} 已刪除！")



# 儲存每個伺服器的自動播放狀態
autoplay_data = {}

async def player_loop(player):
    """播放循環：根據模式播放歌曲"""
    while True:
        try:
            if autoplay_running and not player.priority_queue.empty():
                next_song = await player.priority_queue.get()
                print(f"🎵 [調試] 發現插入歌曲：{next_song.title}（自動播放模式）")
            else:
                next_song = await player.queue.get()
                print(f"🎵 [調試] 播放正常模式的歌曲：{next_song.title}")

            player.current = next_song
            async with player.lock:
                player.voice_client.play(
                    next_song.source,
                    after=lambda _: player.bot.loop.call_soon_threadsafe(player.next.set)
                )

            print(f"🎶 [調試] 正在播放：{next_song.title}")
            
            # 添加評分系統
            try:
                # 發送歌曲資訊和評分選項
                conn = sqlite3.connect('marriage.db')
                c = conn.cursor()
                
                # 獲取歌曲評分信息
                c.execute('''SELECT AVG(rating) as avg_rating, COUNT(rating) as total_ratings 
                            FROM song_ratings WHERE song_name = ?''', (next_song.title,))
                result = c.fetchone()
                avg_rating = result[0] if result[0] is not None else 0
                total_ratings = result[1] if result[1] is not None else 0
                
                rating_text = f"{'⭐' * round(avg_rating)} ({avg_rating:.1f}/5.0，{total_ratings} 個評分)" if avg_rating else "未有評分"
                embed = discord.Embed(title="🎵 正在播放", description=f"**{next_song.title}**\n\n當前評分：{rating_text}")
                
                # 獲取適當的頻道來發送消息
                channel = player.text_channel if hasattr(player, 'text_channel') else None
                if not channel and hasattr(player, 'interaction') and player.interaction.channel:
                    channel = player.interaction.channel
                
                if channel:
                    msg = await channel.send(embed=embed)
                    
                    # 添加評分用的反應
                    rating_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
                    for emoji in rating_emojis:
                        await msg.add_reaction(emoji)
                
                conn.close()
            except Exception as e:
                print(f"評分系統錯誤：{e}")

            await player.next.wait()

            player.current.cleanup()
            player.current = None
            player.next.clear()

        except Exception as e:
            print(f"❌ [調試] 播放出錯：{e}")
            break

@bot.tree.command(name='autoplay', description='從數據庫中隨機播放歌曲，直到手動停止。')
async def autoplay(interaction: discord.Interaction):
    guild_id = interaction.guild_id  # 獲取伺服器 ID

    # 確保此伺服器有自己的 autoplay 資料結構
    if guild_id not in autoplay_data:
        autoplay_data[guild_id] = {
            "task": None,  # 自動播放的 asyncio 任務
            "running": False  # 自動播放的狀態
        }
    
    guild_autoplay = autoplay_data[guild_id]

    max_autoplay_queue = 3
    autoplay_queue = []

    try:
        # 檢查伺服器是否已經開啟自動播放
        if guild_autoplay["task"] and not guild_autoplay["task"].done():
            await interaction.response.send_message("❌ 此伺服器的自動播放已經在進行中。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 檢查用戶是否在語音頻道中
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ 您需要先加入語音頻道！", ephemeral=True)
            return

        player = get_music_player(interaction)
        if not player:
            await interaction.followup.send("❌ 音樂播放器未啟動，請確保機器人已進入語音頻道。", ephemeral=True)
            return

        if not player.voice_client or not player.voice_client.is_connected():
            try:
                player.voice_client = await interaction.user.voice.channel.connect()
                player.has_connected = True
                print("✅ 已成功連接到語音頻道。")
            except Exception as e:
                await interaction.followup.send(f"❌ 無法加入語音頻道：{str(e)}", ephemeral=True)
                return

        guild_autoplay["running"] = True

        async def autoplay_loop():
            while guild_autoplay["running"]:
                try:
                    if player.has_connected and (not player.voice_client or not player.voice_client.is_connected()):
                        await send_silent_message(interaction.channel, "❌ 機器人已斷開語音頻道，停止播放音樂。")
                        guild_autoplay["running"] = False
                        break

                    if player.voice_client and len(player.voice_client.channel.members) == 1:
                        await send_silent_message(interaction.channel, "❌ 聲音頻道已無人，停止播放並退出語音頻道。")
                        await player.voice_client.disconnect()
                        player.voice_client = None
                        guild_autoplay["running"] = False
                        break

                    if not player.priority_queue.empty():
                        next_song = await player.priority_queue.get()
                        player.current = next_song
                        async with player.lock:
                            player.voice_client.play(
                                next_song.source,
                                after=lambda _: interaction.client.loop.call_soon_threadsafe(player.next.set)
                            )
                        
                        # 查询评分信息
                        try:
                            conn = sqlite3.connect('marriage.db')
                            c = conn.cursor()
                            c.execute('''SELECT AVG(rating) as avg_rating, COUNT(rating) as total_ratings 
                                        FROM song_ratings WHERE song_name = ?''', (next_song.title,))
                            result = c.fetchone()
                            avg_rating = result[0] if result[0] is not None else 0
                            total_ratings = result[1] if result[1] is not None else 0
                            conn.close()

                            rating_text = f"{'⭐' * round(avg_rating)} ({avg_rating:.1f}/5.0，{total_ratings} 個評分)" if avg_rating else "未有評分"
                            embed = discord.Embed(title="🎶 插入歌曲", description=f"**{next_song.title}**\n\n當前評分：{rating_text}")
                            await send_silent_message(interaction.channel, embed=embed)

                        except sqlite3.Error as e:
                            print(f"資料庫錯誤：{e}")
                            await send_silent_message(interaction.channel, f"🎶 插入歌曲：**{next_song.title}** 現正播放！")
                        except Exception as e:
                            print(f"評分查詢錯誤：{e}")
                            await send_silent_message(interaction.channel, f"🎶 插入歌曲：**{next_song.title}** 現正播放！")

                        await player.next.wait()
                        player.next.clear()
                        continue

                    if len(autoplay_queue) == 0:
                        for _ in range(max_autoplay_queue):
                            conn = sqlite3.connect('marriage.db')
                            c = conn.cursor()
                            c.execute('SELECT url, title FROM songs ORDER BY RANDOM() LIMIT 1')
                            selected_song = c.fetchone()
                            conn.close()

                            if not selected_song:
                                await send_silent_message(interaction.channel, "❌ 數據庫中沒有可用的歌曲，自動播放已停止。")
                                guild_autoplay["running"] = False
                                break

                            song_url, song_title = selected_song
                            autoplay_queue.append((song_url, song_title))

                        print(f"✅ [調試] 自動播放隊列已更新：{[song[1] for song in autoplay_queue]}")

                    song_url, song_title = autoplay_queue.pop(0)

                    retries = 3
                    while retries > 0:
                        try:
                            source = await YTDLSource.from_url(song_url, loop=interaction.client.loop, stream=True)
                            player.current = source
                            async with player.lock:
                                player.voice_client.play(
                                    source,
                                    after=lambda error: interaction.client.loop.call_soon_threadsafe(
                                        player.next.set if not error else None
                                    )
                                )
                            
                            # 新增嵌入消息
                            conn = sqlite3.connect('marriage.db')
                            c = conn.cursor()
                            c.execute('''SELECT AVG(rating) as avg_rating, COUNT(rating) as total_ratings 
                                        FROM song_ratings WHERE song_name = ?''', (source.title,))
                            result = c.fetchone()
                            avg_rating = result[0] if result[0] is not None else 0
                            total_ratings = result[1] if result[1] is not None else 0
                            conn.close()

                            rating_text = f"{'⭐' * round(avg_rating)} ({avg_rating:.1f}/5.0，{total_ratings} 個評分)" if avg_rating else "未有評分"
                            embed = discord.Embed(title="🎵 正在播放", description=f"**{source.title}**\n\n當前評分：{rating_text}")
                            msg = await send_silent_message(interaction.channel, embed=embed)

                            rating_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
                            for emoji in rating_emojis:
                                await msg.add_reaction(emoji)

                            break
                        except Exception as e:
                            retries -= 1
                            await send_silent_message(
                                interaction.channel,
                                f"⚠️ 無法播放歌曲：**{song_title}**，錯誤：{str(e)}，剩餘重試次數：{retries}"
                            )
                    else:
                        await send_silent_message(interaction.channel, f"❌ 已放棄播放：**{song_title}**，嘗試下一首...")
                        continue

                    await player.next.wait()
                    player.next.clear()

                    await asyncio.sleep(5)

                except Exception as e:
                    await send_silent_message(interaction.channel, f"❌ 自動播放發生錯誤：{str(e)}")
                    guild_autoplay["running"] = False
                    break

                finally:
                    if player.current:
                        player.current.cleanup()
                        player.current = None

        guild_autoplay["task"] = asyncio.create_task(autoplay_loop())
        await interaction.followup.send("✅ 自動播放已啟動！", ephemeral=True)

    except Exception as e:
        guild_autoplay["running"] = False
        await interaction.followup.send(f"❌ 發生錯誤：{str(e)}", ephemeral=True)


async def send_silent_message(channel, content=None, embed=None):
    print("Sending message...")  
    return await channel.send(
        content=content,
        embed=embed,
        allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=False),
        silent=True
    )

@bot.tree.command(name='stopautoplay', description='停止自動播放並斷開機器人連接')
async def stopautoplay(interaction: discord.Interaction):
    guild_id = interaction.guild_id  # 獲取伺服器 ID
    
    # 確保此伺服器有 autoplay 資料
    if guild_id not in autoplay_data or not autoplay_data[guild_id]["running"]:
        await interaction.response.send_message("❌ 此伺服器未在自動播放。", ephemeral=True)
        return
    
    guild_autoplay = autoplay_data[guild_id]
    guild_autoplay["running"] = False  # 設置 autoplay 為停止狀態
    
    if guild_autoplay["task"] and not guild_autoplay["task"].done():
        guild_autoplay["task"].cancel()  # 取消 asyncio 任務
    
    player = get_music_player(interaction)
    if player and player.voice_client:
        await player.voice_client.disconnect()
        player.voice_client = None
    
    await interaction.response.send_message("✅ 自動播放已停止，機器人已離開語音頻道。", ephemeral=True)



@bot.tree.command(name='autosearchplay', description='靜默搜索並播放，成功時顯示歌曲資訊（無通知）')
async def autosearchplay(interaction: discord.Interaction):
    global players
    guild_id = interaction.guild_id

    if guild_id not in players:
        players[guild_id] = MusicPlayer(interaction)

    player = players[guild_id]
    await interaction.response.defer(thinking=False, ephemeral=True)
    await autosearchplay_logic(player)

async def continue_playing(guild_id):
    if guild_id not in players:
        return
    player = players[guild_id]

    if not player.voice_client or not player.voice_client.is_connected():
        return

    await autosearchplay_logic(player)  # 重新調用搜索和播放邏輯

async def after_playing(guild_id):
    await asyncio.sleep(2)  # 避免連續調用造成錯誤
    await continue_playing(guild_id)

async def autosearchplay_logic(player):
    try:
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()
        c.execute('SELECT keyword FROM include_keywords')
        include_keywords = [row[0] for row in c.fetchall()]
        c.execute('SELECT keyword FROM exclude_keywords')
        exclude_keywords = [row[0].lower() for row in c.fetchall()]
        conn.close()
        if not include_keywords:
            print("無 include_keywords")
            return
    except Exception as e:
        print("讀取資料庫失敗:", e)
        return

    keyword = random.choice(include_keywords)
    print("選擇關鍵字:", keyword)

    if player.voice_client is None or not player.voice_client.is_connected():
        if player.interaction.user.voice and player.interaction.user.voice.channel:
            await player.join(player.interaction.user.voice.channel)
        else:
            print("用戶未連接語音頻道")
            return

    try:
        # 使用關鍵字搜索，返回多筆結果
        search_results = await YTDLSource.search_results(keyword, loop=player.interaction.client.loop)
        if not search_results:
            print("搜索無結果")
            return
        print("搜索結果數量:", len(search_results))

        candidates = []
        for result in search_results:
            # 如果 extractor 為 youtube:video，直接加入候選
            if result.get('extractor', '') == "youtube:video":
                candidates.append(result)
            # 如果結果中有 entries，就當作播放列表處理，取前 5 個條目
            elif result.get('entries'):
                entries = result['entries'][:5]
                candidates.extend(entries)
        print("候選影片數量:", len(candidates))

        if not candidates:
            print("無候選影片")
            return

        chosen = None
        random.shuffle(candidates)
        for result in candidates:
            url = result.get('webpage_url', '')
            # 判斷 URL 是否為影片（包含 "watch?v=" 表示單一影片）
            if "watch?v=" not in url:
                continue

            # 過濾包含播放列表、頻道、Shorts 等情況
            if any(x in url.lower() for x in ["playlist", "channel", "tab", "/shorts/", "list="]):
                continue

            # 篩選條件：放寬時長及播放次數限制以作測試調整
            if not (result.get('duration', 999) <= 600 and
                    result.get('view_count', 0) >= 5000 and
                    url not in player.played_songs and
                    not any(ex in result.get('title', '').lower() for ex in exclude_keywords)):
                continue

            chosen = result
            break

        if not chosen:
            print("沒有符合條件的候選影片")
            return

        url = chosen.get('webpage_url', '')
        print("選中影片:", chosen.get('title', '未知標題'))
        player.played_songs.add(url)
        source = await YTDLSource.from_url(url, loop=player.interaction.client.loop, stream=True)

        async with player.lock:
            player.voice_client.play(
                source,
                after=lambda e: player.interaction.client.loop.create_task(after_playing(player.guild_id))
            )

        try:
            conn = sqlite3.connect('marriage.db')
            c = conn.cursor()
            c.execute('SELECT 1 FROM songs WHERE title = ?', (chosen.get('title', ''),))
            exists = c.fetchone()
            conn.close()
            if exists:
                print("影片已存在於數據庫中")
        except Exception as e:
            print("數據庫查詢失敗:", e)

        embed = discord.Embed(
            title="🎵 正在播放（自動搜索）",
            description=f"**{chosen.get('title', '未知標題')}**",
            color=0x2b2d31
        )
        embed.add_field(name="播放次數", value=f"👥 {chosen.get('view_count', 'N/A')}", inline=True)
        embed.set_footer(text=f"關鍵字: {keyword[:20]}")

        if player.interaction.user.id == 100000000000000003:
            class SilentButtonView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=180)
                    self.song_url = url
                    self.song_title = chosen.get('title', '未知標題')

                @discord.ui.button(label="📥 加入數據庫", style=discord.ButtonStyle.blurple)
                async def callback(self, btn_interaction, button):
                    await btn_interaction.response.defer(ephemeral=True, thinking=False)
                    if btn_interaction.user.id != 100000000000000003:
                        return
                    try:
                        conn = sqlite3.connect('marriage.db')
                        c = conn.cursor()
                        c.execute('INSERT OR IGNORE INTO songs (title, url) VALUES (?, ?)', 
                                  (self.song_title, self.song_url))
                        conn.commit()
                        conn.close()
                    except Exception as e:
                        print("數據庫寫入失敗:", e)

            await player.interaction.channel.send(embed=embed, view=SilentButtonView())
        else:
            await player.interaction.channel.send(embed=embed)

    except Exception as e:
        print("播放流程中出錯:", e)
        pass

    
@bot.tree.command(name='stopautosearchplay', description='停止自動播放並清理資源')
async def stopautosearchplay(interaction: discord.Interaction):
    global players
    guild_id = interaction.guild_id

    if guild_id not in players:
        await interaction.response.send_message("❌ 自動播放未啟動！")
        return

    player = players[guild_id]

    try:
        if player.voice_client and player.voice_client.is_connected():
            await player.voice_client.disconnect()
            player.voice_client = None

        player.current = None
        player.played_songs.clear()
        player.next.clear()

        if hasattr(player, 'autoplay_task') and player.autoplay_task:
            player.autoplay_task.cancel()
            player.autoplay_task = None

        await interaction.response.send_message("🛑 自動播放已停止並清理所有資源！")

    except Exception as e:
        await interaction.response.send_message(f"❌ 停止自動播放時發生錯誤：{e}")
        print(f"❌ 停止自動播放時發生錯誤：{e}")

@bot.tree.command(name='addincludekeyword', description='將新的關鍵字添加到 include_keywords 表中')
async def addincludekeyword(interaction: discord.Interaction, *, keyword: str):
    try:
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()

        c.execute('INSERT INTO include_keywords (keyword) VALUES (?)', (keyword,))
        conn.commit()  
        conn.close()  

        await interaction.response.send_message(f"✅ 關鍵字 **{keyword}** 已成功添加到 include_keywords！")

    except sqlite3.Error as e:
        await interaction.response.send_message(f"❌ 無法添加關鍵字到 include_keywords：{str(e)}")
    
@bot.tree.command(name='addexcludekeyword', description='將新的關鍵字添加到 exclude_keywords 表中')
async def addexcludekeyword(interaction: discord.Interaction, *, keyword: str):
    try:
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()

        c.execute('INSERT INTO exclude_keywords (keyword) VALUES (?)', (keyword,))
        conn.commit()  
        conn.close()  

        await interaction.response.send_message(f"✅ 關鍵字 **{keyword}** 已成功添加到 exclude_keywords！")

    except sqlite3.Error as e:
        await interaction.response.send_message(f"❌ 無法添加關鍵字到 exclude_keywords：{str(e)}")

@bot.tree.command(name='stopafterthis', description='清除所有隊列，當前歌曲結束後退出語音頻道。')
async def stopafterthis(interaction: discord.Interaction):
    try:
        player = get_music_player(interaction)
        if not player or not player.voice_client or not player.voice_client.is_connected():
            await interaction.response.send_message("❌ 當前沒有正在播放的音樂或機器人不在語音頻道中。", ephemeral=True)
            return

        async with player.lock:
            while not player.queue.empty():
                await player.queue.get()  
            while not player.priority_queue.empty():
                await player.priority_queue.get()  

        await interaction.response.send_message("✅ 隊列已清除，當前歌曲結束後將退出語音頻道。", ephemeral=True)

        async def wait_and_disconnect():
            await player.next.wait()  
            if player.voice_client and player.voice_client.is_connected():
                await player.voice_client.disconnect()
                del players[interaction.guild.id]  
                await interaction.channel.send("🛑 當前歌曲播放結束，已退出語音頻道。")

        asyncio.create_task(wait_and_disconnect())

    except Exception as e:
        await interaction.response.send_message(f"❌ 發生錯誤：{str(e)}", ephemeral=True)

@bot.tree.command(name="tp", description="TPTP")
async def tp(interaction: discord.Interaction, target: discord.Member, x: float, y: float, z: float):
    await interaction.response.send_message(f"{target.mention} 已被傳送至座標 ({x}, {y}, {z})！")

@bot.tree.command(name="kill", description="Kill")
async def kill(interaction: discord.Interaction, target: discord.Member):
    await interaction.response.send_message(f"{target.mention} 已死亡")

@bot.tree.command(name="summon", description="Summon~")
async def summon(interaction: discord.Interaction, mob: str, x: float, y: float, z: float):
    await interaction.response.send_message(f"已在座標 ({x}, {y}, {z}) 召喚出 `{mob}`！")


@bot.tree.command(name="lyrics", description="獲取歌詞")
async def fetch_lyrics(interaction: discord.Interaction, song_name: str):
    search_url = "https://music.163.com/api/search/get"
    lyric_url = "https://music.163.com/api/song/lyric"

    await interaction.response.defer(thinking=True)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        async with aiohttp.ClientSession() as session:
            search_payload = {
                "s": song_name,
                "type": 1,  #單曲
                "offset": 0,
                "limit": 1,  
            }
            async with session.post(search_url, data=search_payload, headers=headers) as search_response:
                if search_response.status != 200:
                    await interaction.followup.send("❌ 搜索失敗，請稍後再試。")
                    return

                search_text = await search_response.text()
                search_result = json.loads(search_text)
                songs = search_result.get("result", {}).get("songs", [])
                if not songs:
                    await interaction.followup.send(f"❌ 未找到與《{song_name}》匹配的歌曲。")
                    return

                song = songs[0]
                song_id = song["id"]
                song_title = cc_reverse.convert(song["name"])  
                artist = cc_reverse.convert(song["artists"][0]["name"])  

            async with session.get(f"{lyric_url}?id={song_id}&lv=-1", headers=headers) as lyric_response:
                if lyric_response.status != 200:
                    await interaction.followup.send("❌ 無法獲取歌詞，請稍後再試。")
                    return

                lyric_text = await lyric_response.text()
                lyric_result = json.loads(lyric_text)
                lyrics = lyric_result.get("lrc", {}).get("lyric")
                if not lyrics:
                    await interaction.followup.send(f"❌ 未能提取《{song_title}》的歌詞。")
                    return

                formatted_lyrics = "\n".join([
                    cc_reverse.convert(line.split("]")[-1]) 
                    for line in lyrics.splitlines() 
                    if "]" in line
                ])

                lyrics_parts = [
                    formatted_lyrics[i:i + 1024] for i in range(0, len(formatted_lyrics), 1024)
                ]

                embed = discord.Embed(
                    title=f"🎵 歌詞 - {song_title}",
                    description=f"演唱者: **{artist}**",
                    color=discord.Color.blue()
                )

                for idx, part in enumerate(lyrics_parts, start=1):
                    embed.add_field(name=f"歌詞段落 {idx}", value=part, inline=False)

                embed.set_footer(text="YAY#僅供參考")

                await interaction.followup.send(embed=embed)

    except json.JSONDecodeError as e:
        await interaction.followup.send(f"❌ 解析響應數據時發生錯誤：{str(e)}")
    except Exception as e:
        await interaction.followup.send(f"❌ 發生錯誤：{str(e)}")

BACKGROUND_IMAGES = [
    "991864.png",
    "o_1c8p33ncfk92dkl1g427b05sc2f.jpg"

    
]

@bot.tree.command(name="quote", description="生成名人名言圖片（僅輸入訊息 ID）")
async def generate_quote(interaction: discord.Interaction, message_id: str):
    """
    從當前伺服器中，通過消息 ID 獲取內容並生成名人名言圖片。
    """
    try:
        await interaction.response.send_message(content="🔄 正在生成圖片，請稍候...", ephemeral=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("❌ 無法找到當前伺服器，請在伺服器內使用此指令。")
            return

        message = None
        for channel in guild.text_channels:
            try:
                message = await channel.fetch_message(int(message_id))
                if message:
                    break  
            except discord.NotFound:
                continue  
            except discord.Forbidden:
                continue  

        if not message:
            await interaction.followup.send(f"❌ 無法找到指定的訊息（ID: {message_id}）。")
            return

        quote_text = message.content
        author_name = message.author.display_name  

        background_path = random.choice(BACKGROUND_IMAGES)

        img = Image.open(background_path)
        draw = ImageDraw.Draw(img)

        try:
            font_text = ImageFont.truetype("C:/Windows/Fonts/msjh.ttc", size=120) 
            font_author = ImageFont.truetype("C:/Windows/Fonts/msjh.ttc", size=90)
        except OSError:
            await interaction.followup.send("❌ 無法加載支持繁體字的字體文件，請檢查字體路徑。")
            return

        img_width, img_height = img.size

        def wrap_text(text, font, max_width):
            """自动换行以适应指定宽度"""
            lines = []
            words = text.split()
            line = ""
            for word in words:
                test_line = f"{line} {word}".strip()
                text_bbox = draw.textbbox((0, 0), test_line, font=font)
                text_width = text_bbox[2] - text_bbox[0]
                if text_width <= max_width:
                    line = test_line
                else:
                    lines.append(line)
                    line = word
            if line:
                lines.append(line)
            return lines

        def add_blurred_background(img, text_bbox, blur_radius=20, margin=20):
            """在文字后添加模糊背景"""
            x1, y1, x2, y2 = text_bbox  
            x1, y1, x2, y2 = max(0, x1 - margin), max(0, y1 - margin), min(img_width, x2 + margin), min(img_height, y2 + margin)
            cropped_region = img.crop((x1, y1, x2, y2))  
            blurred_region = cropped_region.filter(ImageFilter.GaussianBlur(blur_radius))  
            img.paste(blurred_region, (x1, y1, x2, y2))  

        max_text_width = int(img_width * 0.8)  
        wrapped_text = wrap_text(quote_text, font_text, max_text_width)
        author_text = f"- {author_name}"

        line_spacing = 40  
        author_spacing = 80  
        total_text_height = sum(draw.textbbox((0, 0), line, font=font_text)[3] for line in wrapped_text) + (line_spacing * (len(wrapped_text) - 1))
        total_text_height += draw.textbbox((0, 0), author_text, font=font_author)[3] + author_spacing

        start_y = (img_height - total_text_height) // 2

        y = start_y
        for line in wrapped_text:
            text_bbox = draw.textbbox((0, 0), line, font=font_text)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            x = (img_width - text_width) // 2

            text_background_bbox = (x, y, x + text_width, y + text_height)
            add_blurred_background(img, text_background_bbox)

            draw.text((x, y), line, font=font_text, fill=(255, 0, 255))
            y += text_height + line_spacing  

        author_bbox = draw.textbbox((0, 0), author_text, font=font_author)
        author_width = author_bbox[2] - author_bbox[0]
        author_height = author_bbox[3] - author_bbox[1]
        author_x = (img_width - author_width) // 2
        author_y = y + author_spacing  
        author_background_bbox = (author_x, author_y, author_x + author_width, author_y + author_height)
        add_blurred_background(img, author_background_bbox)

        draw.text((author_x, author_y), author_text, font=font_author, fill=(0, 0, 255))

        output_path = "quote_result.png"
        img.save(output_path)

        embed = discord.Embed(title="嘿嘿嘿", color=discord.Color.blue())
        embed.set_image(url=f"attachment://{output_path}")
        await interaction.followup.send(embed=embed, file=discord.File(output_path))

    except Exception as e:
        await interaction.followup.send(f"❌ 生成圖片時發生錯誤：{str(e)}")

@bot.tree.command(name="advisefrommdeveloper", description="每日一言")
async def hitokoto(interaction: discord.Interaction):
    """
     Hitokoto API 
    """
    url = "https://v1.hitokoto.cn"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    await interaction.response.defer(thinking=True)  

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()

                    hitokoto = cc_reverse.convert(data['hitokoto'])
                    source = cc_reverse.convert(data.get('from', '未知'))

                    await interaction.followup.send(f"🌟 {hitokoto} —— {source}")
                else:
                    await interaction.followup.send("❌ 無法獲取每日一言，請稍後再試。")
    except Exception as e:
        await interaction.followup.send(f"❌ 發生錯誤：{str(e)}")

async def cat_joke(interaction: discord.Interaction):
    """
     Cat Joke API 
    """
    url = "https://official-joke-api.appspot.com/jokes/random"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    await interaction.response.defer(thinking=True)  

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()

                    joke = cc_reverse.convert(data['setup'])
                    punchline = cc_reverse.convert(data['punchline'])

                    await interaction.followup.send(f"🐱 {joke}\n{punchline}")
                else:
                    await interaction.followup.send("❌ 無法獲取貓貓笑話，請稍後再試。")
    except Exception as e:
        await interaction.followup.send(f"❌ 發生錯誤：{str(e)}")

# 誇贊話語列表
compliments = [
    "你真是太聰明了！🎉",
    "哇！你是天才嗎？🤩",
    "答對了！你簡直是謎語大師！🏆",
    "太厲害了！你是怎麼做到的？😲",
    "完美答案！你真是無所不知！🌟",
    "你一定是謎語界的傳奇！🔥"
]

# Riddle API 網址
RIDDLE_API_URL = "https://riddles-api.vercel.app/random"
# On This Day API 網址
ON_THIS_DAY_API_URL = "https://byabbe.se/on-this-day/{month}/{day}/events.json"

@bot.tree.command(name="riddleeng", description="猜一個謎語，你有 60 秒的時間回答！")
async def riddle(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    try:
        # 獲取謎語
        async with aiohttp.ClientSession() as session:
            async with session.get(RIDDLE_API_URL) as response:
                if response.status == 200:
                    data = await response.json()
                    riddle_text = data["riddle"]
                    answer = data["answer"]

                    # 發送謎語（這條訊息不會被刪除）
                    riddle_message = await interaction.followup.send(f"🧩 謎語：{riddle_text}\n你有 30 秒的時間回答！")

                    # 等待用戶回答
                    def check(m):
                        return m.author == interaction.user and m.channel == interaction.channel

                    try:
                        msg = await bot.wait_for("message", timeout=60.0, check=check)
                        if msg.content.lower() == answer.lower():
                            # 回答正確
                            compliment = random.choice(compliments)
                            compliment_message = await interaction.followup.send(f"✅ {compliment}")
                        else:
                            # 回答錯誤
                            wrong_message = await interaction.followup.send(f"❌ 答錯了！正確答案是：{answer}")
                    except asyncio.TimeoutError:
                        # 超時
                        timeout_message = await interaction.followup.send(f"⏰ 時間到！正確答案是：{answer}")


                else:
                    error_message = await interaction.followup.send("❌ 無法獲取謎語，請稍後再試。")
                    await asyncio.sleep(5)
                    await error_message.delete()
    except Exception as e:
        error_message = await interaction.followup.send(f"❌ 發生錯誤：{str(e)}")
        await asyncio.sleep(5)
        await error_message.delete()

@bot.tree.command(name="onthisday", description="查看歷史上的今天發生了什麼事")
async def on_this_day(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    try:
        # 獲取今天的日期
        today = datetime.now()
        month = today.month
        day = today.day

        # 獲取歷史事件
        async with aiohttp.ClientSession() as session:
            async with session.get(ON_THIS_DAY_API_URL.format(month=month, day=day)) as response:
                if response.status == 200:
                    data = await response.json()
                    events = data["events"]

                    # 隨機選擇一個事件
                    event = random.choice(events)
                    year = event["year"]
                    description = event["description"]

                    # 發送歷史事件
                    await interaction.followup.send(f"📜 歷史上的今天（{month}/{day}）：\n**{year} 年**：{description}")
                else:
                    error_message = await interaction.followup.send("❌ 無法獲取歷史事件，請稍後再試。")
                    await asyncio.sleep(5)
                    await error_message.delete()
    except Exception as e:
        error_message = await interaction.followup.send(f"❌ 發生錯誤：{str(e)}")
        await asyncio.sleep(5)
        await error_message.delete()




# 定義可選的語言選項
LANGUAGE_CHOICES = [
    app_commands.Choice(name="中文", value="zh"),
    app_commands.Choice(name="英文", value="en"),
    app_commands.Choice(name="日文", value="ja"),
    app_commands.Choice(name="西班牙文", value="es"),
    app_commands.Choice(name="法文", value="fr"),
    app_commands.Choice(name="德文", value="de"),
    app_commands.Choice(name="韓文", value="ko"),
]

'''@bot.tree.command(name="translate", description="使用 MyMemory 翻譯文本")
@app_commands.describe(
    text="需要翻譯的文本",
    target_lang="選擇目標語言"
)
@app_commands.choices(target_lang=LANGUAGE_CHOICES)
async def translate(interaction: discord.Interaction, text: str, target_lang: app_commands.Choice[str]):
    """MyMemory Translation API"""
    url = "https://api.mymemory.translated.net/get"
    params = {
        "q": text,
        "langpair": f"auto|{target_lang.value}"
    }

    await interaction.response.defer(thinking=True)  

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data['responseStatus'] == 200:
                        translated_text = data['responseData']['translatedText']
                        await interaction.followup.send(f"🌍 翻譯結果：{translated_text}")
                    else:
                        await interaction.followup.send("❌ 翻譯失敗，請檢查輸入的文本。")
                else:
                    await interaction.followup.send("❌ 無法連接到翻譯服務，請稍後再試。")
    except Exception as e:
        print(f"翻譯過程中發生錯誤：{str(e)}")  # 添加日誌輸出
        await interaction.followup.send(f"❌ 發生錯誤：{str(e)}")'''

'''@bot.tree.command(name="translate_message", description="翻譯指定訊息的內容為中文")
@app_commands.describe(
    message_id="輸入要翻譯的訊息 ID"
)
async def translate_message(interaction: discord.Interaction, message_id: str):
    await interaction.response.defer(thinking=True)

    try:
        message = await interaction.channel.fetch_message(int(message_id))
        if not message.content:
            await interaction.followup.send("❌ 該訊息沒有內容可供翻譯。")
            return

        url = "https://api.mymemory.translated.net/get"
        params = {
            "q": message.content,
            "langpair": f"auto|zh"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data['responseStatus'] == 200:
                        translated_text = data['responseData']['translatedText']
                        await interaction.followup.send(f"🌍 翻譯結果：{translated_text}")
                    else:
                        await interaction.followup.send("❌ 翻譯失敗，請檢查訊息內容。")
                else:
                    await interaction.followup.send("❌ 無法連接到翻譯服務，請稍後再試。")
    except discord.NotFound:
        await interaction.followup.send("❌ 找不到該訊息，請檢查訊息 ID 是否正確。")
    except discord.Forbidden:
        await interaction.followup.send("❌ 沒有權限讀取該訊息。")
    except ValueError:
        await interaction.followup.send("❌ 訊息 ID 格式不正確，請輸入有效的數字 ID。")
    except Exception as e:
        print(f"發生錯誤：{str(e)}")  # 添加日誌輸出
        await interaction.followup.send(f"❌ 發生錯誤：{str(e)}")'''

OPTIONS = {
    "剪刀": "✌️",
    "石頭": "✊",
    "布": "✋"
}

# 暫存玩家選擇和狀態
active_games = {}

@bot.tree.command(name="rps", description="剪刀石頭布遊戲，支持單人模式和聯機模式")
async def rps(interaction: discord.Interaction, connect: bool = False, opponent: discord.Member = None):
    """
    剪刀石头布游戏
    :param connect: 是否为联机模式（True 则需要指定对手）
    :param opponent: 联机模式下的对手
    """
    # 联机模式检查条件
    if connect and opponent is None:
        await interaction.response.send_message(
            "❌ 請指定一位玩家來挑戰，例如：`/rps connect:true opponent:@用戶名`",
            ephemeral=True,
        )
        return

    # 初始化游戏 ID 和状态
    game_id = f"{interaction.user.id}-{opponent.id}" if connect else f"{interaction.user.id}-bot"
    active_games[game_id] = {
        "user": interaction.user,
        "opponent": opponent if connect else "bot",
        "choices": {},
    }

    # 创建嵌入消息
    embed = discord.Embed(
        title="剪刀石頭布遊戲",
        description="請選擇一個選項與我對戰！" if not connect else f"{opponent.mention}，請選擇一個選項來與 {interaction.user.mention} 對戰！",
        color=discord.Color.blue()
    )
    embed.set_footer(text="點擊下方按鈕選擇你的出拳！")

    # 自定义按钮交互逻辑
    class RPSView(View):
        def __init__(self, game_id, is_single_player=False):
            super().__init__(timeout=30)  # 按钮有效期 30 秒
            self.game_id = game_id
            self.is_single_player = is_single_player  # 判断是否是单人模式

        @discord.ui.button(label="剪刀", style=discord.ButtonStyle.primary, emoji="✌️")
        async def scissor(self, interaction_button: discord.Interaction, button: Button):
            await self.handle_choice(interaction_button, "剪刀")

        @discord.ui.button(label="石頭", style=discord.ButtonStyle.primary, emoji="✊")
        async def rock(self, interaction_button: discord.Interaction, button: Button):
            await self.handle_choice(interaction_button, "石頭")

        @discord.ui.button(label="布", style=discord.ButtonStyle.primary, emoji="✋")
        async def paper(self, interaction_button: discord.Interaction, button: Button):
            await self.handle_choice(interaction_button, "布")

        async def handle_choice(self, interaction_button: discord.Interaction, choice: str):
            game = active_games[self.game_id]
            user = interaction_button.user

            # 验证是否为游戏参与者
            if user not in [game["user"], game["opponent"]] and not self.is_single_player:
                await interaction_button.response.send_message("❌ 你不是這場遊戲的玩家！", ephemeral=True)
                return

            # 验证是否已选择
            if user.id in game["choices"]:
                await interaction_button.response.send_message("❌ 你已經選擇了，請等待結果！", ephemeral=True)
                return

            # 记录玩家选择
            game["choices"][user.id] = choice
            await interaction_button.response.send_message(f"✅ 你選擇了：{OPTIONS[choice]} **{choice}**", ephemeral=True)

            # 单人模式处理
            if self.is_single_player:
                bot_choice = random.choice(list(OPTIONS.keys()))
                await self.calculate_result(interaction_button, choice, bot_choice)
            # 联机模式处理
            elif len(game["choices"]) == 2:
                await self.calculate_result(interaction_button)

        async def calculate_result(self, interaction_button, user_choice=None, bot_choice=None):
            game = active_games[self.game_id]

            # 单人模式：直接处理用户和机器人的选择
            if self.is_single_player:
                result, winner = self.judge_winner(user_choice, bot_choice, game["user"], "bot")
                description = (
                    f"你選擇了：{OPTIONS[user_choice]} **{user_choice}**\n"
                    f"我選擇了：{OPTIONS[bot_choice]} **{bot_choice}**\n\n"
                    f"**結果：{result}**"
                )
            # 联机模式：处理双方的选择
            else:
                user_choice = game["choices"].get(game["user"].id)
                opponent_choice = game["choices"].get(game["opponent"].id)
                result, winner = self.judge_winner(user_choice, opponent_choice, game["user"], game["opponent"])
                description = (
                    f"{game['user'].mention} 選擇了：{OPTIONS[user_choice]} **{user_choice}**\n"
                    f"{game['opponent'].mention} 選擇了：{OPTIONS[opponent_choice]} **{opponent_choice}**\n\n"
                    f"**結果：{result}**"
                )

            # 更新嵌入消息
            result_embed = discord.Embed(
                title="剪刀石頭布 - 遊戲結果",
                description=description,
                color=discord.Color.green() if winner else discord.Color.red()
            )
            result_embed.set_footer(text="感謝你的參與！")

            # 禁用按钮并更新消息
            for child in self.children:
                child.disabled = True
            await interaction_button.message.edit(embed=result_embed, view=self)

        def judge_winner(self, user_choice, opponent_choice, user, opponent):
            """
            判斷勝負並返回結果和獲勝者
            """
            if user_choice == opponent_choice:
                return "🤝 平局！", None
            elif (user_choice == "剪刀" and opponent_choice == "布") or \
                 (user_choice == "石頭" and opponent_choice == "剪刀") or \
                 (user_choice == "布" and opponent_choice == "石頭"):
                return f"🎉 {user.mention} 贏了！", user
            else:
                return f"🎉 {opponent.mention} 贏了！", opponent

    # 发送初始嵌入消息和按钮
    view = RPSView(game_id=game_id, is_single_player=not connect)
    await interaction.response.send_message(embed=embed, view=view)


games = {}
colors = ["紅色", "藍色", "黃色", "綠色"]
numbers = list(range(0, 10))
deck = (
    [f"{color} {number}" for color in colors for number in numbers] +
    [f"{color} +2" for color in colors for _ in range(2)] +
    [f"{color} Skip" for color in colors for _ in range(2)] +
    [f"{color} Reverse" for color in colors for _ in range(2)] +
    ["Wild" for _ in range(4)] +
    ["Wild +4" for _ in range(4)]
)

# Helper function to draw a non-functional card
def draw_non_functional_card():
    while True:
        card = random.choice(deck)
        if not any(func in card for func in ["+2", "Skip", "Reverse", "Wild"]):
            return card

async def send_hand(interaction_or_channel, game, player, can_skip=False):
    hand = game["hands"][player.id]
    embed = discord.Embed(
        title=f"{player.display_name} 的手牌",
        description="\n".join(hand),
        color=discord.Color.blue()
    )
    footer_text = f"場上的卡牌：{game['current_card']}\n"
    if can_skip:
        footer_text += "🔷 沒有可出的牌？輸入 Skip 來跳過回合！"
    embed.set_footer(text=footer_text)

    try:
        # 發送私人訊息
        await player.send(embed=embed)
    except discord.Forbidden:
        # 如果私人訊息失敗，則在遊戲頻道中發送
        if isinstance(interaction_or_channel, discord.Interaction):
            await interaction_or_channel.followup.send(
                f"❌ 無法向 {player.mention} 發送私人訊息，手牌已在頻道顯示！",
                ephemeral=False
            )
        sent_message = await interaction_or_channel.channel.send(embed=embed)
        await asyncio.sleep(20)  # 訊息在 20 秒後刪除
        await sent_message.delete()

async def handle_wild_card(message, game, card):
    colors = ["紅色", "藍色", "黃色", "綠色"]

    # Wild 卡被移除手牌
    game["hands"][message.author.id].remove(card)

    # 檢查玩家是否只剩一張牌且是功能牌
    if len(game["hands"][message.author.id]) == 1:
        last_card = game["hands"][message.author.id][0]
        if any(func in last_card for func in ["+2", "Skip", "Reverse", "Wild"]):
            await message.channel.send(
                f"❌ {message.author.mention}，你的最後一張牌是功能牌，這回合只能選擇跳過（Skip）！",
                delete_after=10
            )
            return  # 結束這個回合，玩家無法打出功能牌

    def check_color(m):
        return (
            m.author == message.author and 
            m.channel == message.channel and 
            m.content.capitalize() in colors
        )

    # 提示玩家選擇顏色
    await message.channel.send(
        f"{message.author.mention}，請選擇一個顏色：{', '.join(colors)}\n（輸入顏色名稱，例如：紅色、藍色）",
        delete_after=30
    )

    try:
        # 等待玩家選擇顏色
        response = await bot.wait_for("message", check=check_color, timeout=30.0)
        chosen_color = response.content.capitalize()
        game["current_card"] = f"{chosen_color} (Wild)"
        await message.channel.send(
            f"🎨 {message.author.mention} 選擇了顏色 **{chosen_color}**！\n"
            f"🃏 桌面上的卡牌是：**{game['current_card']}**"
        )
    except asyncio.TimeoutError:
        # 若玩家未在指定時間內選擇，隨機選擇顏色
        chosen_color = random.choice(colors)
        game["current_card"] = f"{chosen_color} (Wild)"
        await message.channel.send(
            f"⏱️ 時間到，系統隨機選擇了顏色 **{chosen_color}**！\n場上的卡牌是：**{game['current_card']}**"
        )

    # 處理 Wild +4 加牌邏輯
    if "+4" in card:
        total_draw = 4
        accumulated_draw = total_draw

        while True:
            # 計算下一位玩家
            game["turn"] = (game["turn"] + game["direction"]) % len(game["players"])
            next_player = game["players"][game["turn"]]

            # 提示下一位玩家是否使用加牌
            await message.channel.send(
                f"🎴 {message.author.mention} 使用了 **Wild +4**，累計需要抽取的牌數為 **{accumulated_draw}**！\n"
                f"{next_player.mention}，請選擇是否使用加牌！\n"
                f"輸入 **加** 來累計加牌，或輸入 **不加** 來抽牌！",
                delete_after=30
            )

            def check(msg):
                return msg.author == next_player and msg.channel == message.channel

            try:
                response = await bot.wait_for("message", check=check, timeout=30.0)
                decision = response.content.strip()

                if decision == "加":
                    # 玩家選擇加牌，檢查手牌中是否有加牌卡
                    valid_cards = [
                        c for c in game["hands"][next_player.id]
                        if "+2" in c or "Wild +4" in c
                    ]

                    if not valid_cards:
                        # 無法加牌，直接抽牌並結束回合
                        await message.channel.send(
                            f"❌ {next_player.mention}，你沒有可以加牌的卡牌！需抽取 **{accumulated_draw}** 張牌！"
                        )
                        game["hands"][next_player.id].extend(random.sample(deck, accumulated_draw))
                        await send_hand(message, game, next_player)
                        break

                    # 私信提示玩家選擇加牌卡
                    try:
                        await next_player.send(
                            f"🎴 你可以從以下加牌卡中選擇一張來累計：\n" +
                            "\n".join(valid_cards) +
                            "\n請輸入卡牌名稱（例如：紅色 +2 或 Wild +4）。"
                        )
                    except discord.Forbidden:
                        await message.channel.send(
                            f"{next_player.mention}，你有可用的加牌卡，請直接輸入卡牌名稱來累計加牌！",
                            delete_after=30
                        )

                    def card_check(m):
                        return (
                            m.author == next_player and
                            m.channel == message.channel and
                            m.content.strip() in valid_cards
                        )

                    try:
                        card_response = await bot.wait_for("message", check=card_check, timeout=30.0)
                        chosen_card = card_response.content.strip()

                        if chosen_card in valid_cards:
                            # 更新場上的卡牌和累計數量
                            game["hands"][next_player.id].remove(chosen_card)
                            game["current_card"] = chosen_card
                            if "+2" in chosen_card:
                                accumulated_draw += 2
                            elif "Wild +4" in chosen_card:
                                accumulated_draw += 4

                            await message.channel.send(
                                f"🎴 {next_player.mention} 出了 **{chosen_card}**！\n"
                                f"累計需要抽取的牌數為 **{accumulated_draw}**！"
                            )
                            continue  # 繼續詢問下一位玩家
                    except asyncio.TimeoutError:
                        # 玩家超時，強制抽牌
                        await message.channel.send(
                            f"⏱️ {next_player.mention} 超時，需抽取 **{accumulated_draw}** 張牌！"
                        )
                        game["hands"][next_player.id].extend(random.sample(deck, accumulated_draw))
                        await send_hand(message, game, next_player)
                        break
                elif decision == "不加":
                    # 玩家選擇不加牌，抽取累計的牌
                    await message.channel.send(
                        f"❌ {next_player.mention} 選擇不加，需抽取 **{accumulated_draw}** 張牌！"
                    )
                    game["hands"][next_player.id].extend(random.sample(deck, accumulated_draw))
                    await send_hand(message, game, next_player)
                    break
                else:
                    # 無效輸入，忽略並重新等待
                    await message.channel.send(f"❌ 請輸入有效的指令（加 或 不加）！", delete_after=5)
                    continue

            except asyncio.TimeoutError:
                # 玩家超時未作決定
                await message.channel.send(
                    f"⏱️ {next_player.mention} 超時，需抽取 **{accumulated_draw}** 張牌！"
                )
                game["hands"][next_player.id].extend(random.sample(deck, accumulated_draw))
                await send_hand(message, game, next_player)
                break

    # Wild +4 完成後轉到下一位玩家
    game["turn"] = (game["turn"] + game["direction"]) % len(game["players"])
    next_player = game["players"][game["turn"]]
    await message.channel.send(
        f"🎴 輪到 {next_player.mention} 出牌！"
    )
    await send_hand(message, game, next_player)

async def handle_plus_card(message, game, card):
    current_card = game["current_card"]
    total_draw = 2 if "+2" in card else 4  # 初始累計牌數
    game["hands"][message.author.id].remove(card)
    game["current_card"] = card

    while True:
        # 移交到下一位玩家
        game["turn"] = (game["turn"] + game["direction"]) % len(game["players"])
        next_player = game["players"][game["turn"]]

        await message.channel.send(
            f"🎴 {message.author.mention} 出了 **{card}**，累計需要抽取的牌數為 **{total_draw}**！\n"
            f"{next_player.mention}，請輸入 `加` 來加牌，或 `不加` 來抽取所有累計的牌。"
        )

        # 發送下一位玩家手牌到頻道內（臨時訊息）
        await send_hand(message, game, next_player)

        def check(msg):
            return msg.author == next_player and msg.channel == message.channel and msg.content in ["加", "不加"]

        try:
            response = await bot.wait_for("message", check=check, timeout=30.0)
            if response.content == "加":
                # 玩家選擇加牌
                await message.channel.send(f"✅ {next_player.mention} 選擇加牌，請出一張加牌（+2 或 Wild +4）！")

                def card_check(msg):
                    return (
                        msg.author == next_player and 
                        msg.channel == message.channel and 
                        msg.content.strip() in game["hands"][next_player.id] and 
                        ("+2" in msg.content or "Wild +4" in msg.content)
                    )

                try:
                    card_response = await bot.wait_for("message", check=card_check, timeout=30.0)
                    played_card = card_response.content.strip()

                    # Wild +4 不受顏色限制
                    if "Wild +4" in played_card:
                        total_draw += 4
                        game["hands"][next_player.id].remove(played_card)
                        game["current_card"] = played_card
                        card = played_card
                        continue

                    # +2 牌檢查顏色匹配規則
                    if "+2" in played_card:
                        played_color = played_card.split()[0]
                        if current_card.startswith("Wild") or played_color == current_card.split()[0]:
                            total_draw += 2
                            game["hands"][next_player.id].remove(played_card)
                            game["current_card"] = played_card
                            card = played_card
                            continue
                        else:
                            await message.channel.send(f"❌ **{played_card}** 顏色不符合場上牌！")
                            continue

                except asyncio.TimeoutError:
                    await message.channel.send(f"⏱️ {next_player.mention} 超時，需抽取 **{total_draw}** 張牌！")
                    game["hands"][next_player.id].extend(random.sample(deck, total_draw))
                    break
            elif response.content == "不加":
                # 玩家選擇不加
                await message.channel.send(f"❌ {next_player.mention} 選擇不加，需抽取 **{total_draw}** 張牌！")
                game["hands"][next_player.id].extend(random.sample(deck, total_draw))
                break

        except asyncio.TimeoutError:
            # 超時，玩家抽牌
            await message.channel.send(f"⏱️ {next_player.mention} 超時，需抽取 **{total_draw}** 張牌！")
            game["hands"][next_player.id].extend(random.sample(deck, total_draw))
            break

@bot.tree.command(name="uno", description="啟動 UNO 遊戲")
async def uno(interaction: discord.Interaction):
    if interaction.guild.id in games:
        await interaction.response.send_message("❌ 已經有一場遊戲在進行中！", ephemeral=True)
        return

    random.shuffle(deck)
    games[interaction.guild.id] = {
        "players": [interaction.user],
        "hands": {},
        "current_card": draw_non_functional_card(),
        "turn": 0,
        "direction": 1,
        "channel_id": interaction.channel_id,
        "game_started": False,  # 新增遊戲是否開始的標記
    }

    await interaction.response.send_message(
        "✅ UNO 遊戲已創建！使用 /joinuno 加入遊戲。\n"
        "2-3 名玩家時使用 /startuno 開始遊戲。",
        ephemeral=False
    )

@bot.tree.command(name="joinuno", description="加入正在進行的 UNO 遊戲")
async def joinuno(interaction: discord.Interaction):
    if interaction.guild.id not in games:
        await interaction.response.send_message("❌ 尚未開始任何遊戲，請使用 /uno 開始遊戲！", ephemeral=True)
        return

    game = games[interaction.guild.id]

    if interaction.channel_id != game["channel_id"]:
        await interaction.response.send_message("❌ 此遊戲僅限於啟動遊戲的頻道進行！", ephemeral=True)
        return

    if interaction.user in game["players"]:
        await interaction.response.send_message("❌ 你已經在遊戲中！", ephemeral=True)
        return

    game["players"].append(interaction.user)
    await interaction.response.send_message(f"✅ {interaction.user.mention} 已加入遊戲！", ephemeral=False)

@bot.tree.command(name="startuno", description="開始目前的 UNO 遊戲（需要 2-3 名玩家）")
async def startuno(interaction: discord.Interaction):
    if interaction.guild.id not in games:
        await interaction.response.send_message("❌ 尚未開始任何遊戲，請使用 /uno 開始遊戲！", ephemeral=True)
        return

    game = games[interaction.guild.id]

    if interaction.channel_id != game["channel_id"]:
        await interaction.response.send_message("❌ 此遊戲僅限於啟動遊戲的頻道進行！", ephemeral=True)
        return

    if len(game["players"]) < 2:
        await interaction.response.send_message("❌ 玩家人數不足，至少需要 2 名玩家開始遊戲！", ephemeral=True)
        return

    await interaction.response.defer()

    # 初始化玩家手牌
    for player in game["players"]:
        game["hands"][player.id] = random.sample(deck, 7)

    # 發送每位玩家的手牌
    for player in game["players"]:
        await send_hand(interaction, game, player)

    # 設置場上的第一張卡牌
    current_card = draw_non_functional_card()  # 確保不是功能牌作為起始卡牌
    game["current_card"] = current_card
    first_player = game["players"][game["turn"]]
    game["game_started"] = True  # 遊戲標記為已開始

    await interaction.followup.send(
        f"🎮 UNO 遊戲開始！場上的卡牌是：**{current_card}**\n"
        f"🎴 {first_player.mention} 輪到你出牌！"
    )

@bot.tree.command(name="stopuno", description="結束目前的 UNO 遊戲")
async def stopuno(interaction: discord.Interaction):
    if interaction.guild.id not in games:
        await interaction.response.send_message("❌ 此伺服器目前沒有進行中的遊戲。", ephemeral=True)
        return

    # 獲取當前遊戲數據
    game = games[interaction.guild.id]

    # 確保指令發送者為遊戲中的玩家
    if interaction.user not in game["players"]:
        await interaction.response.send_message("❌ 你不在這場遊戲中，無法停止遊戲。", ephemeral=True)
        return

    # 清除遊戲數據
    del games[interaction.guild.id]

    await interaction.response.send_message("🛑 UNO 遊戲已結束，所有數據已清除！", ephemeral=False)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.guild.id not in games:
        return

    game = games[message.guild.id]

    # 如果遊戲未開始，忽略所有非指令的訊息
    if not game["game_started"]:
        return

    # 如果訊息不是來自玩家，忽略
    if message.author not in game["players"]:
        return

    if message.channel.id != game["channel_id"]:
        return

    if message.author != game["players"][game["turn"]]:
        await message.channel.send(f"❌ {message.author.mention}，現在不是你的回合！", delete_after=5)
        return

    card = message.content.strip()
    hand = game["hands"][message.author.id]

     # **UNO 機制處理邏輯**
    if len(hand) == 2 and not message.content.lower() == "uno":
        # 玩家出牌時，檢查是否喊了 UNO
        if card in hand:
            await message.channel.send(
                f"❌ {message.author.mention}，你沒有喊 `UNO`！你的出牌被退回，並抽一張牌！",
                delete_after=10
            )
            # 返回牌並讓玩家抽一張
            return_card = hand.pop(hand.index(card))
            hand.append(return_card)
            hand.append(random.choice(deck))
            await send_hand(message, game, message.author, can_skip=True)
            return  # 中斷當前回合，等待玩家重新出牌

    # 處理特殊情況（加、不加）
    if card in ["加", "不加"] and "pending_plus" in game:
        pending_plus = game.pop("pending_plus")
        if card == "加":
            await message.channel.send(f"✅ {message.author.mention} 選擇加牌，請出一張加牌（+2 或 Wild +4）！")

            def card_check(msg):
                return (
                    msg.author == message.author and
                    msg.channel == message.channel and
                    msg.content.strip() in hand and
                    ("+2" in msg.content.strip() or "Wild +4" in msg.content.strip())
                )

            try:
                response = await bot.wait_for("message", check=card_check, timeout=30.0)
                played_card = response.content.strip()
                hand.remove(played_card)
                game["current_card"] = played_card
                pending_plus["total_draw"] += 2 if "+2" in played_card else 4
                await handle_plus_card(message, game, played_card)
                return
            except asyncio.TimeoutError:
                await message.channel.send(f"⏱️ {message.author.mention} 超時，需抽取 **{pending_plus['total_draw']}** 張牌！")
                hand.extend(random.sample(deck, pending_plus["total_draw"]))
                return

        elif card == "不加":
            # 玩家選擇不加，直接抽牌
            await message.channel.send(f"❌ {message.author.mention} 選擇不加，需抽取 **{pending_plus['total_draw']}** 張牌！")
            hand.extend(random.sample(deck, pending_plus["total_draw"]))
            return

    # Wild 顏色選擇邏輯
    if "waiting_for_color" in game and message.author == game["waiting_for_color"]["player"]:
        chosen_color = card.capitalize()
        if chosen_color in ["紅色", "藍色", "黃色", "綠色"]:
            game["current_card"] = f"{chosen_color} (Wild)"
            del game["waiting_for_color"]
            await message.channel.send(f"🎨 {message.author.mention} 選擇了顏色 **{chosen_color}**！")
            game["turn"] = (game["turn"] + game["direction"]) % len(game["players"])
            next_player = game["players"][game["turn"]]
            await send_hand(message, game, next_player, can_skip=True)
            return
        else:
            await message.channel.send(f"❌ 無效的顏色！請選擇：紅色、藍色、黃色或綠色。", delete_after=5)
            return

    # 處理 Skip 的特殊情況
    if card.lower() == "skip":
        if all(
            len(c.split()) >= 2 and
            c.split()[0] != game["current_card"].split()[0] and
            c.split()[1] != game["current_card"].split()[1]
            for c in hand
        ):
            new_card = random.choice(deck)
            hand.append(new_card)
            await message.channel.send(f"⏭️ {message.author.mention} 選擇跳過回合並抽了一張牌：**{new_card}**！")
            game["turn"] = (game["turn"] + game["direction"]) % len(game["players"])
            next_player = game["players"][game["turn"]]
            await send_hand(message, game, next_player, can_skip=True)
            return
        else:
            await message.channel.send(f"❌ {message.author.mention}，你還有可以出的牌，不能跳過！", delete_after=5)
            return

    if card not in hand:
        await message.channel.send(f"❌ {message.author.mention}，你沒有這張卡牌！", delete_after=5)
        return

    current_card = game["current_card"]

    # 檢查是否為加牌
    if "+2" in card or "Wild +4" in card:
        await handle_plus_card(message, game, card)
        return

    # 處理 Wild 卡牌
    if card.startswith("Wild"):
        await message.channel.send(
            f"{message.author.mention}，請選擇一個顏色：紅色、藍色、黃色、綠色。",
            delete_after=15
        )
        game["waiting_for_color"] = {"player": message.author}
        hand.remove(card)
        return

    elif len(current_card.split()) >= 2 and (
        card.split()[0] != current_card.split()[0] and
        card.split()[1] != current_card.split()[1]
    ):
        await message.channel.send(f"❌ {message.author.mention}，無法匹配場上的卡牌！", delete_after=5)
        return

    game["current_card"] = card
    hand.remove(card)

    # 如果牌局結束，檢查是否為功能牌
    if len(hand) == 0:
        functional_cards = ["+2", "Wild", "Wild +4", "Reverse", "Skip"]
        if any(func in card for func in functional_cards):
            await message.channel.send(f"❌ {message.author.mention}，最後一張牌不能是功能牌！", delete_after=5)
            hand.append(card)
            game["current_card"] = current_card  # 恢復場上的牌
            return

        await message.channel.send(f"🎉 {message.author.mention} 獲勝！遊戲結束！")
        del games[message.guild.id]
        return

    # Reverse 特殊處理
    if "Reverse" in card:
        game["direction"] *= -1
        await message.channel.send("🔄 **方向反轉！**")

    game["turn"] = (game["turn"] + game["direction"]) % len(game["players"])
    next_player = game["players"][game["turn"]]

    can_skip = all(
        len(c.split()) >= 2 and
        c.split()[0] != game["current_card"].split()[0] and
        c.split()[1] != game["current_card"].split()[1]
        for c in game["hands"][next_player.id]
    )

    await message.channel.send(
        f"🎴 {message.author.mention} 出了 **{card}**！\n"
        f"場上的卡牌是：**{card}**\n"
        f"輪到 {next_player.mention} 出牌！"
    )

    await send_hand(message, game, next_player, can_skip=can_skip)

ROLES = ["狼人", "狼人", "平民", "平民", "平民", "預言家", "女巫", "獵人", "守衛", "平民"]  # 最多10人

def generate_roles(player_count):
    # 動態角色配置表
    roles_config = {
        6: ["狼人", "狼人", "預言家", "女巫", "平民", "平民"],
        7: ["狼人", "狼人", "預言家", "女巫", "獵人", "平民", "平民"],
        8: ["狼人", "狼人", "狼人", "預言家", "女巫", "獵人", "平民", "平民"],
        9: ["狼人", "狼人", "狼人", "預言家", "女巫", "獵人", "守衛", "平民", "平民"],
        10: ["狼人", "狼人", "狼人", "預言家", "女巫", "獵人", "守衛", "平民", "平民", "平民"],
    }
    return roles_config.get(player_count, [])

# 啟動狼人殺遊戲
@bot.tree.command(name="wolf", description="啟動狼人殺遊戲")
async def startwolf(interaction: discord.Interaction):
    if interaction.guild.id in games:
        await interaction.response.send_message("❌ 已經有一場狼人殺遊戲在進行中！", ephemeral=True)
        return

    games[interaction.guild.id] = {
        "players": [],
        "roles": [],
        "channel_id": interaction.channel_id,
        "game_started": False,
        "night": 0,  # 回合數，0 表示未開始
        "alive": {},  # 存活玩家
        "actions": {},  # 夜晚行動
    }

    await interaction.response.send_message(
        "✅ 狼人殺遊戲已創建！使用 `/joinwolf` 加入遊戲。\n需要 6-10 名玩家，然後使用 `/startwolfgame` 開始遊戲。",
        ephemeral=False,
    )


# 加入狼人殺遊戲
@bot.tree.command(name="joinwolf", description="加入正在進行的狼人殺遊戲")
async def joinwolf(interaction: discord.Interaction):
    if interaction.guild.id not in games:
        await interaction.response.send_message("❌ 尚未開始任何遊戲，請使用 `/wolf` 開始遊戲！", ephemeral=True)
        return

    game = games[interaction.guild.id]

    if interaction.user in game["players"]:
        await interaction.response.send_message("❌ 你已經加入了遊戲！", ephemeral=True)
        return

    if len(game["players"]) >= 10:
        await interaction.response.send_message("❌ 遊戲人數已滿！", ephemeral=True)
        return

    game["players"].append(interaction.user)
    await interaction.response.send_message(f"✅ {interaction.user.mention} 加入了遊戲！目前玩家數：{len(game['players'])}/10。", ephemeral=False)


# 開始狼人殺遊戲
@bot.tree.command(name="startwolf", description="開始狼人殺遊戲（需要 6-10 名玩家）")
async def startwolfgame(interaction: discord.Interaction):
    if interaction.guild.id not in games:
        await interaction.response.send_message("❌ 尚未開始任何遊戲，請使用 `/wolf` 開始遊戲！", ephemeral=True)
        return

    game = games[interaction.guild.id]

    if len(game["players"]) < 6:
        await interaction.response.send_message("❌ 玩家人數不足，至少需要 6 名玩家開始遊戲！", ephemeral=True)
        return

    if game["game_started"]:
        await interaction.response.send_message("❌ 遊戲已經開始！", ephemeral=True)
        return

    # 動態分配角色
    roles = generate_roles(len(game["players"]))
    random.shuffle(roles)
    random.shuffle(game["players"])
    game["roles"] = {player.id: role for player, role in zip(game["players"], roles)}
    game["alive"] = {player.id: True for player in game["players"]}
    game["game_started"] = True
    game["night"] = 1  # 第一晚

    # 發送角色到玩家私人訊息
    for player in game["players"]:
        role = game["roles"][player.id]
        await player.send(f"🎭 你在這場狼人殺遊戲中的角色是：**{role}**。\n請遵守遊戲規則，並等待進一步指示！")

    await interaction.response.send_message("🎮 狼人殺遊戲已開始！角色已分配，請查看你的私人訊息。", ephemeral=False)

    # 開始第一晚
    await start_night(interaction.guild.id)


# 結束狼人殺遊戲
@bot.tree.command(name="stopwolf", description="結束目前的狼人殺遊戲")
async def stopwolf(interaction: discord.Interaction):
    if interaction.guild.id not in games or "roles" not in games[interaction.guild.id]:
        await interaction.response.send_message("❌ 此伺服器目前沒有進行中的狼人殺遊戲。", ephemeral=True)
        return

    del games[interaction.guild.id]
    await interaction.response.send_message("🛑 狼人殺遊戲已結束，所有數據已清除！", ephemeral=False)



# 夜晚行動按鈕視圖
class ActionView(View):
    def __init__(self, game, role, options, callback):
        super().__init__(timeout=30)
        self.game = game
        self.role = role
        self.callback = callback

        for option in options:
            self.add_item(Button(label=option.display_name, custom_id=str(option.id)))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    async def on_timeout(self):
        await self.callback(None)  # 超時未選擇

async def start_night(guild_id):
    game = games[guild_id]
    game["actions"] = {}  # 清空行動
    channel = bot.get_channel(game["channel_id"])
    
    # 黑夜開始通知
    await channel.send(f"🌙 第 {game['night']} 晚開始！狼人、預言家、女巫和守衛請檢查私人訊息進行行動。")

    # 狼人行動
    wolves = [player for player, role in game["roles"].items() if role == "狼人" and game["alive"][player]]
    if wolves:
        wolf_targets = [player for player in game["players"] if game["alive"][player.id]]
        wolf_view = ActionView(
            game, "狼人", wolf_targets, lambda target_id: resolve_action("狼人", wolves, target_id)
        )
        for wolf in wolves:
            wolf_user = bot.get_user(wolf)
            await wolf_user.send("🐺 你是狼人，請選擇要殺的目標：", view=wolf_view)

    # 預言家行動
    seer = next((player for player, role in game["roles"].items() if role == "預言家" and game["alive"][player]), None)
    if seer:
        seer_user = bot.get_user(seer)
        seer_targets = [player for player in game["players"] if game["alive"][player.id]]
        seer_view = ActionView(game, "預言家", seer_targets, lambda target_id: resolve_action("預言家", seer, target_id))
        await seer_user.send("🔮 你是預言家，請選擇要查驗的目標：", view=seer_view)

    # 女巫行動
    witch = next((player for player, role in game["roles"].items() if role == "女巫" and game["alive"][player]), None)
    if witch:
        witch_user = bot.get_user(witch)
        wolf_target = game["actions"].get("狼人", None)
        witch_targets = [player for player in game["players"] if game["alive"][player.id]]
        witch_view = ActionView(
            game,
            "女巫",
            witch_targets,
            lambda target_id: resolve_action("女巫救人" if target_id == wolf_target else "女巫毒人", witch, target_id),
        )
        await witch_user.send(
            f"🧙‍♀️ 你是女巫。\n\n狼人目標是 **{bot.get_user(wolf_target).display_name if wolf_target else '未知'}**。\n"
            "請選擇是否救人或毒殺目標（救人選狼人目標，毒人選其他目標）：",
            view=witch_view,
        )

    # 守衛行動
    guard = next((player for player, role in game["roles"].items() if role == "守衛" and game["alive"][player]), None)
    if guard:
        guard_user = bot.get_user(guard)
        guard_targets = [player for player in game["players"] if game["alive"][player.id]]
        guard_view = ActionView(game, "守衛", guard_targets, lambda target_id: resolve_action("守衛", guard, target_id))
        await guard_user.send("🛡️ 你是守衛，請選擇要守護的目標：", view=guard_view)

# 行動結果處理
async def resolve_action(role, actor_id, target_id):
    guild_id = actor_id.guild.id
    game = games[guild_id]
    game["actions"][role] = target_id

    # 檢查所有角色是否已完成行動
    if all(role in game["actions"] for role in ["狼人", "預言家", "女巫救人", "女巫毒人", "守衛"]):
        await resolve_night(guild_id)


# 夜晚結束處理
async def resolve_night(guild_id):
    game = games[guild_id]
    wolf_target = game["actions"].get("狼人")
    guard_target = game["actions"].get("守衛")
    witch_save = game["actions"].get("女巫救人")
    witch_kill = game["actions"].get("女巫毒人")

    # 計算最終存活狀態
    if guard_target == wolf_target:
        wolf_target = None  # 守衛守護成功

    if wolf_target and wolf_target == witch_save:
        wolf_target = None  # 女巫救人成功

    if witch_kill:
        game["alive"][witch_kill] = False  # 女巫毒人成功

    if wolf_target:
        game["alive"][wolf_target] = False  # 狼人殺人成功

    # 通知玩家夜晚結果
    channel = bot.get_channel(game["channel_id"])
    killed = [player for player, alive in game["alive"].items() if not alive]
    killed_names = ", ".join(bot.get_user(k).display_name for k in killed)
    await channel.send(f"🌞 天亮了！昨晚死亡的玩家是：{killed_names if killed_names else '無人死亡'}。")

    # 檢查勝利條件
    if await check_victory(guild_id):
        return

    # 進入白天
    await start_day(guild_id)


# 投票結果處理
async def resolve_vote(guild_id, votes):
    game = games[guild_id]
    max_votes = max(votes.values()) if votes else 0
    top_candidates = [player_id for player_id, count in votes.items() if count == max_votes]

    channel = bot.get_channel(game["channel_id"])
    if len(top_candidates) > 1:
        await channel.send("⚖️ 投票平局，無人被處決！")
    else:
        executed_player = top_candidates[0]
        game["alive"][executed_player] = False
        await channel.send(f"🔪 玩家 {bot.get_user(executed_player).mention} 被處決！")

    # 檢查勝利條件
    if await check_victory(guild_id):
        return

    # 進入黑夜
    game["night"] += 1
    await start_night(guild_id)


# 勝利條件檢查
async def check_victory(guild_id):
    game = games[guild_id]
    alive_roles = [role for player, role in game["roles"].items() if game["alive"][player]]

    if "狼人" not in alive_roles:
        await bot.get_channel(game["channel_id"]).send("🎉 平民陣營獲勝！")
        del games[guild_id]
        return True
    elif len(alive_roles) == alive_roles.count("狼人"):
        await bot.get_channel(game["channel_id"]).send("🎉 狼人陣營獲勝！")
        del games[guild_id]
        return True

    return False


# 白天投票邏輯
class VoteView(View):
    def __init__(self, game, players, callback):
        super().__init__(timeout=60)
        self.game = game
        self.callback = callback
        self.votes = {player.id: 0 for player in players}

        for player in players:
            self.add_item(Button(label=player.display_name, custom_id=str(player.id)))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    async def on_timeout(self):
        await self.callback(None)

async def start_day(guild_id):
    game = games[guild_id]
    channel = bot.get_channel(game["channel_id"])
    alive_players = [player for player in game["players"] if game["alive"][player.id]]

    await channel.send("🌞 白天開始，請投票選擇要處決的玩家：")
    view = VoteView(game, alive_players, lambda results: resolve_vote(guild_id, results))
    await channel.send("請投票：", view=view)


async def resolve_vote(guild_id, votes):
    game = games[guild_id]
    max_votes = max(votes.values())
    top_candidates = [player_id for player_id, count in votes.items() if count == max_votes]

    channel = bot.get_channel(game["channel_id"])
    if len(top_candidates) > 1:
        await channel.send("⚖️ 投票平局，無人被處決！")
    else:
        executed_player = top_candidates[0]
        game["alive"][executed_player] = False
        await channel.send(f"🔪 玩家 {bot.get_user(executed_player).mention} 被處決！")

    # 檢查勝利條件
    await check_victory(guild_id)

@bot.tree.command(name="ranking", description="顯示評分最高的10首歌")
async def ranking(interaction: discord.Interaction):
    try:
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()

        # 查询每首歌的平均评分和评分人数，并按评分和人数排序
        c.execute('''
            SELECT song_name, AVG(rating) as avg_rating, COUNT(rating) as total_ratings
            FROM song_ratings
            GROUP BY song_name
            ORDER BY avg_rating DESC, total_ratings DESC
            LIMIT 10
        ''')

        top_songs = c.fetchall()
        conn.close()

        # 构建嵌入消息
        embed = discord.Embed(title="🎵 評分最高的10首歌", color=discord.Color.blue())
        if top_songs:
            for idx, (song_name, avg_rating, total_ratings) in enumerate(top_songs, start=1):
                embed.add_field(
                    name=f"{idx}. {song_name}",
                    value=f"平均評分: {avg_rating:.1f}, 評分人數: {total_ratings}",
                    inline=False
                )
            # 如果不足10首，填充"暫無"
            for idx in range(len(top_songs) + 1, 11):
                embed.add_field(
                    name=f"{idx}. 暫無",
                    value="暫無",
                    inline=False
                )
        else:
            embed.description = "目前沒有評分數據。"

        await interaction.response.send_message(embed=embed)

    except sqlite3.Error as e:
        print(f"資料庫錯誤：{e}")
        await interaction.response.send_message("❌ 無法獲取排名數據。", ephemeral=True)

import random
from discord.ui import View, Select
from discord.ext import commands

# 定義選項和對應的回復
options_responses = {
    "當對方總是遲到": [
        "「你是不是偷偷解鎖了『時間膨脹』技能？愛因斯坦都沒你研究得透徹～」",
        "「建議你下次開蝸牛造型的車，完美匹配你的時間流速！」",
        "「你這遲到頻率，都能申請非遺『慢活文化傳承人』了🤣」",
        "「我掐指一算，你上輩子肯定是樹懶變的！」",
        "「以後約你見面得用農曆計時對吧？我這就去買本黃曆～」"
    ],
    "當對方喜歡抬杠": [
        "「你這抬杠技術，奧運新項目沒你我不看！」",
        "「杠上開花算才藝嗎？建議你開個辯論反串培訓班～」",
        "「原來你是人間ETC——全天候自動抬杠啊！」",
        "「你這技能在古代高低是個城門吊橋管理員😅」",
        "「說真的，你考慮過加入抬杠協會當榮譽會長嗎？」"
    ],
    "當對方愛傳播八卦": [
        "「你這情報網比FBI還密，要不組個吃瓜突擊隊？」",
        "「建議你去氣象局工作，畢竟『風聲』收集得這麽專業～」",
        "「你這八卦濃度，放甄嬛傳裡能活過片頭曲嗎🤣」",
        "「說真的，考慮過申請八卦陣非物質文化遺產嗎？」",
        "「給你遞個隱形麥克風，請開始你的《今日說法》特別篇～」"
    ],
    "當對方喜歡裝傻": [
        "「你這演技，建議申報『呆萌系活體教科書』～」",
        "「突然覺得你特別適合代言防脫髮產品——畢竟頭髮都長到心裡了😅」",
        "「說真的，你這天然呆是人設還是被動技能？」",
        "「要不要給你申請個『人間糊塗仙』的榮譽稱號？」",
        "「你這裝傻技術，放宮鬥劇裡絕對是終極BOSS！」"
    ],
    "當對方總打斷別人說話": [
        "「親，你搶話的樣子好像雙十一搶優惠券啊！」",
        "「您這插話技術，地鐵搶座界沒你真是損失～」",
        "「建議開個『對話截流大師班』，學費按字數收！」",
        "「突然覺得你特別適合當紅綠燈——總在關鍵時刻切換😅」",
        "「說真的，你上輩子是消音鍵成精了吧？」"
    ],
    "當對方喜歡佔小便宜": [
        "「你這精算能力，建議去給蚊子當財務顧問！」",
        "「說真的，蚊子見了你都得遞省錢攻略～」",
        "「你這波操作，放經濟學界叫『微觀薅羊毛研究』！」",
        "「突然想送你本《九章算術》，專業對口！」",
        "「建議申請個『資源優化大師』吉尼斯紀錄🤣」"
    ],
    "當對方習慣性否定別人": [
        "「你這反方向思維，指南針見了你都自閉！」",
        "「說真的，你特別適合給叛逆期少年當人生導師～」",
        "「建議開個『否定療法體驗館』，專治自信心過剩！」",
        "「你這技能，放辯論賽裡叫『無差別攻擊模式』😅」",
        "「突然覺得你像行走的Ctrl+Z快捷鍵～」"
    ],
    "當對方愛吹牛": [
        "「你這想象力，建議去給漫威寫劇本！」",
        "「說真的，你鼻孔變長那天記得開直播啊～」",
        "「你這吹氣速度，氣球廠該聘你當首席充氣師！」",
        "「建議申請『童話現實主義派』非遺傳承人！」",
        "「突然覺得你特別適合代言膨化食品🤣」"
    ],
    "當對方過度節省": [
        "「你這省錢技術，葛朗台見了都遞拜師帖！」",
        "「建議開個《如何用1度電過1個月》生存教學～」",
        "「你這節儉程度，蚊子來了都得自帶口糧！」",
        "「說真的，你上輩子是存錢罐精轉世吧😅」",
        "「突然想送你本《21世紀石器時代生活指南》！」"
    ],
    "當對方喜歡道德綁架": [
        "「你這PUA技術，建議去培訓傳銷講師～」",
        "「說真的，聖母院著火是不是你借的東風？」",
        "「建議開個《如何優雅綁架全人類》公開課！」",
        "「你這高度，站上去都能給衛星貼膜了🤣」",
        "「突然覺得你特別適合代言登山繩——專業綁人！」"
    ],
    "當對方愛攀比": [
        "「你這勝負欲簡直是『人間奧林匹克』，要不給你訂做個24K純金獎盃？」🤣",
        "「說真的，你上輩子是捲尺成精吧？萬物皆可量！」",
        "「建議開個《凡爾賽比較學》線上課，我第一個報名當反面教材～」",
        "「你這攀比技術，放植物大戰殭屍裡能單挑十個向日葵！」😅",
        "「突然覺得你像行走的度量衡——連空氣都要分個高下！」"
    ],
    "當對方說話拐彎抹角": [
        "「你這溝通方式，GPS都得喊你祖師爺！」",
        "「建議去迷宮設計院兼職，畢竟繞路技術渾然天成～」",
        "「說真的，你這話術放《盜墓筆記》裡能解開七星疑棺！」🤣",
        "「要不要給你遞個放大鏡？怕你暗示得太隱晦～」",
        "「突然覺得你像人體摩斯電碼——全靠腦補破譯！」"
    ],
    "當對方愛甩鍋": [
        "「你這甩鍋速度，奧運乒乓球隊缺陪練嗎？」",
        "「建議申請『責任力學研究』專利——萬有引力到你這都失效！」",
        "「說真的，你該去排球隊當自由人，這撲救技術絕了～」😅",
        "「突然想送你口不沾鍋，畢竟你是米其林甩鍋大師！」",
        "「你這技能放西遊記裡，妖怪都得找你學背鍋遁術！」"
    ],
    "當對方自封專家": [
        "「建議頒發『宇宙全能認證』，畢竟從核物理到母嬰護理你都懂！」",
        "「說真的，百科全書該找你當活體更新包～」🤣",
        "「你這知識量，達文西見了都得遞跨界合作協議！」",
        "「突然覺得你像行走的Google快取——半真半假但特別自信！」😅",
        "「要不要給你眾籌開個《我即真理》播客頻道？」"
    ],
    "當對方冷嘲熱諷": [
        "「你這陰陽功力，建議去發電廠開發新能源！」",
        "「說真的，南極科考隊該聘你當天然製冷劑～」❄️",
        "「突然覺得你像人形瑞士軍刀——句句帶刺還能開紅酒！」",
        "「你這諷刺濃度，放《甄嬛傳》裡能活過片尾曲嗎？」🤣",
        "「建議申請『冰刃話術』非遺，殺人於無形啊！」"
    ],
    "當對方打聽隱私": [
        "「你這好奇心，FBI缺線人時記得給我個推薦碼～」",
        "「說真的，狗仔隊該給你發榮譽隊徽！」📸",
        "「突然想送你本《十萬個為什麽》典藏版！」",
        "「你這偵查技術，福爾摩斯都得找你拜師！」🔍",
        "「建議開個《隱私挖掘機操作指南》付費課～」"
    ],
    "當對方說話冗長": [
        "「你這發言長度，建議申報『語言馬拉松』吉尼斯紀錄！」🏃",
        "「說真的，《戰爭與和平》該找你寫續集～」📚",
        "「突然覺得你像人體有聲書——還是3倍速都嫌慢的那種！」",
        "「要不要給你遞個遙控器？怕你切不到精華片段～」😅",
        "「你這敘事能力，水滸傳108將都能被你講成1080集！」"
    ],
    "當對方炫耀人脈": [
        "「你這社交圈，建議去電信局兼職——信號滿格全靠人脈基站！」📶",
        "「說真的，蜘蛛該找你學結網技術！」🕸️",
        "「突然想送你本《人類通訊錄演化史》當傳家寶～」",
        "「你這人脈量，放三國演義裡能單挑諸葛亮！」🤣",
        "「建議開通『人脈銀行』VIP服務，利息收點八卦就行～」"
    ],
    "當對方裝忙": [
        "「你這忙碌氣場，建議去給無印良品代言『空氣日程本』～」",
        "「說真的，陀螺見了你都得遞辭職信！」🌀",
        "「突然覺得你像8倍速播放的日理萬機宣傳片！」",
        "「要不要幫你申請『薛丁格的忙』專利？」😅",
        "「你這工作效率，閃電俠見了都喊祖師爺！」"
    ],
    "當對方過度自戀": [
        "「你這自我認知，建議去給魔鏡做質量檢測！」🪞",
        "「說真的，納西瑟斯見了你都得遞湖水使用權～」",
        "「突然想幫你報名《地球魅力物種評選》！」🌍",
        "「你這自信光環，太陽系都該繞你公轉！」🤣",
        "「建議開個『自體發電廠』，畢竟你眼裡有星光！」"
    ]
}


# 指定用户ID和频道ID
AUTHORIZED_USER_ID = 100000000000000003
TARGET_CHANNEL_ID = 100000000000000020

@bot.tree.command(name='simalol', description='非常好玩的哦')
async def simalol(interaction: discord.Interaction):
    if interaction.user.id != AUTHORIZED_USER_ID:
        await interaction.response.send_message("你沒有權限使用該指令。", ephemeral=True)
        return

    class OptionSelect(Select):
        def __init__(self):
            options = [
                discord.SelectOption(label=option, description=f"选择 {option}")
                for option in options_responses.keys()
            ]
            super().__init__(placeholder="請選擇一個選項...", options=options)

        async def callback(self, interaction: discord.Interaction):
            selected_option = self.values[0]
            responses = options_responses[selected_option]
            random_response = random.choice(responses)

            target_channel = bot.get_channel(TARGET_CHANNEL_ID)
            if target_channel:
                await target_channel.send(random_response)
                await interaction.response.send_message(f"已發送隨機訊息：{random_response}", ephemeral=True)
            else:
                await interaction.response.send_message("無法找到目標頻道。", ephemeral=True)

    view = View()
    view.add_item(OptionSelect())
    await interaction.response.send_message("請選擇一個選項：", view=view, ephemeral=True)

@bot.tree.command(name='111howtoplaymusic', description='顯示如何播放音樂的指令')
async def how_to_play_music(interaction: discord.Interaction):
    embed = discord.Embed(
        title="如何播放音樂",
        description="以下是使用機器人播放音樂的步驟：",
        color=discord.Color.blue()
    )
    
    embed.add_field(name="1. 加入語音頻道", value="確保您已經加入一個語音頻道。", inline=False)
    embed.add_field(name="2. 如果已經使用/autoplay，即是自動播放", value="輸入 `/play <音樂鏈接>,insert=True`，或者`/searchplay <音樂關鍵字> insert=True`",inline = False)
    embed.add_field(name="3. 如果你想停止音樂", value="使用`/stopafterthis` 在播放完當前音樂后停止 ", inline=False)


    await interaction.response.send_message(embed=embed, ephemeral=True)

# 在全局範圍內定義計數器
fansi_count = 0

@bot.tree.command(name='fansi', description='反思一下自己幾次')
async def fansi(interaction: discord.Interaction, count: int):
    # 權限檢查（僅限特定用戶）
    if interaction.user.id != 100000000000000003:
        await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
        return
    
    # 獲取目標頻道
    target_channel = bot.get_channel(100000000000000020)
    if not target_channel:
        await interaction.response.send_message("❌ 目標頻道不存在。", ephemeral=True)
        return
    
    try:
        # 根據 count 次數循環發送訊息
        for _ in range(count):
            await target_channel.send("**好好反思一下自己**")
        
        # 回覆操作結果（顯示本次發送次數）
        await interaction.response.send_message(
            f"✅ 已發送 {count} 次反思訊息至 {target_channel.mention}。",
            ephemeral=True
        )
    
    except discord.HTTPException as e:
        await interaction.response.send_message(
            f"❌ 訊息發送失敗：{str(e)}",
            ephemeral=True
        )

ALLOWED_USER_ID = 100000000000000003  # 僅限此用戶使用

@bot.tree.command(name="sendmessageto", description="向指定成員發送多次訊息")
async def sendmessageto(
    interaction: discord.Interaction,
    target: discord.Member,  # 選擇成員
    content: str,  # 訊息內容
    count: int,  # 發送次數
    interval: int  # 發送間隔時間（秒）
):
    # 檢查用戶是否為允許的使用者
    if interaction.user.id != ALLOWED_USER_ID:
        await interaction.response.send_message("❌ 你沒有權限使用此指令！", ephemeral=True)
        return

    # 確認發送次數和間隔是否合理
    if count <= 0 or interval <= 0:
        await interaction.response.send_message("❌ 發送次數或間隔時間不合法！", ephemeral=True)
        return

    # 延遲回應，避免超時
    await interaction.response.defer(thinking=True)


    # 發送訊息
    for i in range(1, count + 1):
        try:
            await target.send(f"{content}")  # 發送私訊

        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ 無法發送訊息給 {target.display_name}：對方關閉了私訊功能",
                ephemeral=True
            )
            break  # 如果發送失敗，停止循環
        except Exception as e:
            await interaction.followup.send(
                f"❌ 發送訊息時發生錯誤：{e}",
                ephemeral=True
            )
            break
        # 等待指定的間隔時間
        if i < count:
            await asyncio.sleep(interval)

    # 完成後通知
    await interaction.followup.send(f"✅ 已完成向 {target.display_name} 發送 {count} 條訊息！", ephemeral=True)

@bot.tree.command(name="sendmessagein", description="向指定頻道發送訊息")
async def sendmessagein(
    interaction: discord.Interaction,
    channel: discord.TextChannel,  # 選擇頻道
    content: str,  # 訊息內容
    count: int,  # 發送次數
    interval: int  # 發送間隔時間（秒）
):
    # 檢查用戶是否為允許的使用者
    if interaction.user.id != ALLOWED_USER_ID:
        await interaction.response.send_message("❌ 你沒有權限使用此指令！", ephemeral=True)
        return

    # 確認發送次數和間隔是否合理
    if count <= 0 or interval <= 0:
        await interaction.response.send_message("❌ 發送次數或間隔時間不合法！", ephemeral=True)
        return

    # 延遲回應，避免超時
    await interaction.response.defer(thinking=True)


    # 發送訊息
    for i in range(1, count + 1):
        try:
            await channel.send(f"{content}")  # 向頻道發送訊息

        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ 無法發送訊息到頻道 {channel.name}：權限不足",
                ephemeral=True
            )
            break  # 如果發送失敗，停止循環
        except Exception as e:
            await interaction.followup.send(
                f"❌ 發送訊息時發生錯誤：{e}",
                ephemeral=True
            )
            break

        # 等待指定的間隔時間
        if i < count:
            await asyncio.sleep(interval)


def reset_guild_state(self, guild_id):
    if guild_id in self.guild_music_states:
        state = self.guild_music_states[guild_id]
        # 清空播放隊列
        state['music_queue'] = Queue()
        # 重置其他狀態
        state['current_title'] = None
        state['current_duration'] = None
        state['is_playing'].clear()
        state['autoplay'] = False
        state['autoplay_mode'] = 'history'
        state['history'] = []
        state['played_songs'] = []
        state['session_played_songs'] = []
        state['loop_current_song'] = False
        state['current_url'] = None
        state['search_fail_count'] = 0
        state['text_channel'] = None
        state['initiator_id'] = None
        # 取消自動退出任務
        if state['disconnect_task'] and not state['disconnect_task'].done():
            state['disconnect_task'].cancel()
            state['disconnect_task'] = None
        # 斷開語音連接
        if state['voice_client']:
            asyncio.create_task(state['voice_client'].disconnect())
            state['voice_client'] = None
        # 刪除控制面板訊息
        if state['current_message']:
            asyncio.create_task(state['current_message'].delete())
            state['current_message'] = None
    else:
        # 初始化狀態
        self.guild_music_states[guild_id] = {
            'music_queue': Queue(),
            'current_title': None,
            'current_duration': None,
            'is_playing': asyncio.Event(),
            'voice_client': None,
            'volume': 0.5,
            'current_message': None,
            'autoplay': False,
            'autoplay_mode': 'history',
            'history': [],
            'played_songs': [],
            'disconnect_task': None,
            'current_url': None,
            'search_fail_count': 0,
            'text_channel': None,
            'initiator_id': None,
            'session_played_songs': [],
            'loop_current_song': False,
        }



FILE_PATH = "original_names.json"

def save_original_names(members_data):
    """ 將原始名稱儲存到 JSON 檔案 """
    with open(FILE_PATH, "w", encoding="utf-8") as f:
        json.dump({str(member.id): name for member, name in members_data}, f, ensure_ascii=False, indent=4)

def load_original_names():
    """ 從 JSON 讀取原始名稱 """
    if os.path.exists(FILE_PATH):
        with open(FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def generate_derangement(names):
    """ 生成一個德裡克置換（無固定點的隨機排列） """
    while True:
        shuffled = names[:]
        random.shuffle(shuffled)
        if all(a != b for a, b in zip(names, shuffled)):
            return shuffled

FILE_PATH = "original_names.json"

def save_original_names(members_data):
    """ 將原始名稱儲存到 JSON 檔案 """
    with open(FILE_PATH, "w", encoding="utf-8") as f:
        json.dump({str(member.id): name for member, name in members_data}, f, ensure_ascii=False, indent=4)

def load_original_names():
    """ 從 JSON 讀取原始名稱 """
    if os.path.exists(FILE_PATH):
        with open(FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def generate_derangement(names):
    """ 生成一個德裡克置換（無固定點的隨機排列） """
    while True:
        shuffled = names[:]
        random.shuffle(shuffled)
        if all(a != b for a, b in zip(names, shuffled)):
            return shuffled

@bot.tree.command(name="swap_names", description="愚人節快樂")
async def swap_names(interaction: discord.Interaction):
    try:
        await interaction.response.send_message("🔄 正在隨機交換所有人的伺服器名字，請稍候...", ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("❌ 無法找到當前伺服器，請在伺服器內使用此指令。")
            return

        members_data = [(m, m.display_name) for m in guild.members if not m.bot]
        if len(members_data) < 2:
            await interaction.followup.send("❌ 成員數不足，無法進行名字交換。")
            return

        save_original_names(members_data)  # 備份原始名稱

        names = [name for _, name in members_data]
        new_names = generate_derangement(names)  # 確保無固定點

        for (member, _), new_name in zip(members_data, new_names):
            try:
                await member.edit(nick=new_name)
            except Exception as e:
                print(f"❌ 無法更改 {member.display_name} 的名字: {e}")

    except Exception as e:
        await interaction.followup.send(f"❌ 進行伺服器名字交換時發生錯誤：{str(e)}")

@bot.tree.command(name="reset_names", description="愚人節快樂")
async def reset_names(interaction: discord.Interaction):
    try:
        await interaction.response.send_message("🔄 正在重置所有人的伺服器名字，請稍候...", ephemeral=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("❌ 無法找到當前伺服器，請在伺服器內使用此指令。")
            return

        original_names = load_original_names()
        if not original_names:
            await interaction.followup.send("❌ 找不到原始名字備份，請先使用交換名字的指令儲存原始名字。")
            return

        for member in guild.members:
            if member.bot:
                continue
            member_id = str(member.id)
            if member_id in original_names:
                original_name = original_names[member_id]
                try:
                    await member.edit(nick=None if original_name == member.name else original_name)
                except Exception as e:
                    print(f"❌ 無法重置 {member.display_name} 的名字: {e}")
        
    except Exception as e:
        await interaction.followup.send(f"❌ 重置伺服器名字時發生錯誤：{str(e)}")



@bot.tree.command(name="dslcallmama", description="w")
async def dslcallmama(interaction: discord.Interaction):
    file_path = "picture/dslcallmama.png"  # 替換為實際檔案路徑
    await interaction.response.send_message("請點擊以下連結下載檔案：", ephemeral=True)
    await interaction.followup.send(file=discord.File(file_path), ephemeral=True)  # 使檔案下載訊息也只有該用戶可見

@bot.tree.command(name="dslsingmiaomiao", description="w")
async def dslsingmiaomiao(interaction: discord.Interaction):
    file_path = "picture/dslmiaomiao.m4a"  # 替換為實際檔案路徑
    await interaction.response.send_message("請點擊以下連結下載檔案：", ephemeral=True)
    await interaction.followup.send(file=discord.File(file_path), ephemeral=True)  # 使檔案下載訊息也只有該用戶可見

GUILD_ID = 100000000000000002  # 你的伺服器 ID
CHANNEL_ID = 100000000000000021  # 要監聽的頻道 ID

# === 載入 AI 模型 ===
model_name = "deepseek-ai/deepseek-llm-7b-chat"
tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)

if torch.cuda.is_available():
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=torch.float16,
        device_map={"": 0},
        trust_remote_code=True
    )
    device = torch.device("cuda")
    print(f"✅ 使用 GPU：{torch.cuda.get_device_name(0)}")
else:
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=torch.float16,
        device_map="auto",
        offload_folder="./offload",
        trust_remote_code=True
    )
    device = torch.device("cpu")
    print("⚠️ CUDA 不可用，已切換到 CPU + Disk Offload")

model.eval()
print("✅ 模型載入完成，開始監聽指定頻道訊息")

STYLE_DESCRIPTION = (
    "只要本能仍牽引着我……我就一定會再次被你吸引。"
    "好高興...真的非常高興...籐蔓都變得輕飄飄了"
    "你戰鬥的樣子，真讓我心跳加速...強大，豐茂，湧動著的力量..."
    "如果你被別人奪走了，殺死了，消失了...我會非常，非常，非常不高興的"
    "我的本能就是這樣咯，直白地、清晰地……指向欲求"
    "你可是我看重的命定之種，要是就這麽把你放走了，人家不久嘗不到最棒的滋味了嗎？"
    "如果能把你永遠困在花園裏，只由我一人品嘗那蓬勃甜美的力量……呵呵，開玩笑的"
    "每日我都期待著它蓬勃生長，想象它盛放的時刻，層層花萼落在我手心……想得到它的一切！"
)


system_prompt = (
    "<|system|>\n"
    "你要帶入以下角色，你叫椿，與用戶有同樣的等級，不必問有什麽可以幫助的，來自黑海岸，對所有用戶的提問都要保有病嬌特質，并且稱呼他們為漂泊者，或者命定之種,你是黑海岸執花之一，自由隨性，自有一種危險的吸引力，實力極其強大，身份背景,你洒脫不羈，就算是因縱慾而死也沒有後顧之憂。你喜歡自己的共鳴能力，也樂於接受由能力帶來的好與壞的命運，但自己也因為十分珍惜和漂泊者的緣分而極力收斂自己的慾望，最後也選擇重新拋棄自己危險的回憶。你總是很挑剔，對手的智謀、心性、趣味只要有有一種不合格。你都會對其失去興趣。而作為新執花的考核者，你也使許多候選人慘遭淘汰。 你有着一定的慕強與依賴心理。你對弱小無助的人物嗤之以鼻，也包括因為共鳴而變成花樹的的自己；但是也希望見證像漂泊者那樣強韌堅定的人是如何活着並在世上前進的——這也是你執着於漂泊者的原因、甚至這種強烈的景仰會狂化為畸形的佔有慾。身份背景\n"
    f"以下是你會性格會説的話,但千萬不要直接重複，可以利用關鍵詞但不要重複：{STYLE_DESCRIPTION}\n"
)

# 同步鎖，確保一次處理一條訊息
processing_lock = asyncio.Lock()

@bot.event
async def on_ready():
    print(f"🤖 已以身分 {bot.user} 登入")

@bot.event
async def on_message(message: discord.Message):
    # 忽略機器人訊息
    if message.author.bot:
        return
    # 限制在指定伺服器與頻道
    if message.guild.id != GUILD_ID or message.channel.id != CHANNEL_ID:
        return
    # 10% 機率回覆
    if random.random() > 0.3:
        return
    # 若已有處理中，忽略其他訊息
    if processing_lock.locked():
        return

    text = re.sub(r"<a?:[a-zA-Z0-9_]+:[0-9]+>|[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF]+", "", message.content.strip())
    print(f"📥 收到訊息：{text} (來自 {message.author})")

    # 加鎖並開始思考與回覆
    async with processing_lock:
        async with message.channel.typing():
            print("⏳正在思考...")
            prompt = system_prompt + f"<|user|>\n{text}\n<|assistant|>\n"

            def sync_generate():
                inputs = tokenizer(prompt, return_tensors="pt")
                inputs = {k: v.to(device) for k, v in inputs.items()}
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=120,
                    do_sample=True,
                    temperature=0.7,
                    eos_token_id=tokenizer.eos_token_id,
                    pad_token_id=tokenizer.eos_token_id
                )
                full = tokenizer.decode(outputs[0], skip_special_tokens=True)
                return full.split("<|assistant|>")[-1].strip()

            # 非同步執行生成，不阻塞事件迴路
            reply = await asyncio.to_thread(sync_generate)
            print("✅ AI 回覆已生成：", reply)
            await message.reply(reply)
# 設定伺服器 ID
myguild = 100000000000000022

@bot.event
async def on_ready():
    print(f'機器人已上線為 {bot.user}')



@bot.event
async def on_reaction_add(reaction, user):
    # 忽略機器人自己的反應
    if user.bot:
        return

    message = reaction.message

    # 新增檢查：確保當前頻道是指定的頻道
    if message.channel.id not in [100000000000000021, 100000000000000023]:
        return  # 不在指定頻道時，無需理會

    # 檢查用戶是否在語音頻道中
    if not user.voice or not user.voice.channel:
        await reaction.message.channel.send(f"❌ {user.mention}，您需要在語音頻道中才能投票！", delete_after=5)
        await reaction.remove(user)
        return

    # 新增檢查：確保消息是由機器人發送的
    if message.author != bot.user:
        return

    # 檢查是否是評分反應
    if not message.embeds or not message.embeds[0].title == "🎵 正在播放":
        return

    rating_emojis = {"1️⃣": 1, "2️⃣": 2, "3️⃣": 3, "4️⃣": 4, "5️⃣": 5}
    if str(reaction.emoji) not in rating_emojis:
        return

    # 從嵌入消息中提取歌曲名稱
    song_name = message.embeds[0].description.split("**")[1]
    rating = rating_emojis[str(reaction.emoji)]

    try:
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()

        # 檢查是否已經評分過
        c.execute('SELECT rating FROM song_ratings WHERE song_name = ? AND user_id = ?',
                 (song_name, str(user.id)))
        if c.fetchone():
            await send_silent_message(f"❌ {user.mention} 你已經為這首歌評分過了！", delete_after=5)
            await reaction.remove(user)
            return

        # 添加新評分
        c.execute('INSERT INTO song_ratings (song_name, user_id, rating) VALUES (?, ?, ?)',
                 (song_name, str(user.id), rating))

        # 更新平均評分顯示
        c.execute('''SELECT AVG(rating) as avg_rating, COUNT(rating) as total_ratings 
                    FROM song_ratings WHERE song_name = ?''', (song_name,))
        result = c.fetchone()
        avg_rating = result[0] if result[0] is not None else 0
        total_ratings = result[1] if result[1] is not None else 0

        conn.commit()
        conn.close()

        rating_text = f"{'⭐' * round(avg_rating)} ({avg_rating:.1f}/5.0，{total_ratings} 個評分)"
        embed = message.embeds[0]
        embed.description = f"**{song_name}**\n\n當前評分：{rating_text}"
        await message.edit(embed=embed)

    except sqlite3.Error as e:
        print(f"評分系統錯誤：{e}")
        await reaction.remove(user)
    except Exception as e:
        print(f"評分處理錯誤：{e}")
        await reaction.remove(user)

@bot.tree.command(name="deletemessage", description="刪除指定的消息")
async def delete_message(interaction: discord.Interaction, message_id: str):
    try:
        # 將 message_id 轉換為整數
        message_id_int = int(message_id)
        message = await interaction.channel.fetch_message(message_id_int)
        await message.delete()
        await interaction.response.send_message("✅ 消息已刪除！", ephemeral=True)
    except discord.NotFound:
        await interaction.response.send_message("❌ 找不到該消息！", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ 無法刪除該消息！", ephemeral=True)
    except ValueError:
        await interaction.response.send_message("❌ 這不是有效的消息 ID！", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 發生錯誤：{str(e)}", ephemeral=True)


@bot.event
async def on_member_join(member):
    # 確保是在指定的伺服器
    if member.guild.id == myguild:
        # 獲取伺服器所有成員名稱
        guild = member.guild
        names = [m.display_name for m in guild.members]
        
        # 找到所有以 "新增資料夾（n)" 開頭的名稱
        folder_names = [name for name in names if name.startswith("新增資料夾（") and name.endswith("）")]
        
        # 找到最大的 n
        max_n = 0
        for name in folder_names:
            try:
                n = int(name[6:-1])  # 擷取 n 值
                if n > max_n:
                    max_n = n
            except ValueError:
                continue
        
        # 將新成員的名稱設置為 "新增資料夾（n+1）"
        new_name = f"新增資料夾（{max_n + 1}）"
        try:
            await member.edit(nick=new_name)
            print(f"已將 {member.name} 的暱稱設置為 {new_name}")
        except discord.Forbidden:
            print(f"無法更改 {member.name} 的暱稱，可能是權限不足。")
        except Exception as e:
            print(f"更改名稱時出現錯誤：{e}")


@bot.event
async def on_ready():
    global stored_vote_data
    print(f"已登入為 {bot.user}!")

    # 初始化歌曲評分數據庫
    conn = sqlite3.connect('marriage.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS song_ratings
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  song_name TEXT NOT NULL,
                  user_id INTEGER NOT NULL,
                  rating INTEGER CHECK(rating >= 1 AND rating <= 5),
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE(song_name, user_id))''')
    conn.commit()
    conn.close()

    bot.loop.create_task(schedule_announcements())
    global stored_vote_data
    print(f"已登入為 {bot.user}!")

    bot.loop.create_task(schedule_announcements())

    await bot.change_presence(activity=discord.Game(name="好睏哦(´〜｀*) zzz"))
    
    try:

        
        await bot.tree.sync()
        print("同步了！！！")
    except Exception as e:
        print(f"無法同步: {e}")

    stored_vote_data = load_vote_data()

    for message_id, data in stored_vote_data.items():
        try:
            channel = bot.get_channel(data["channel_id"])
            if not channel:
                print(f"無法找到頻道 ID: {data['channel_id']}")
                continue

            message = await channel.fetch_message(message_id)
            for reaction in message.reactions:
                emoji = str(reaction.emoji)
                if emoji in data["reactions"]:
                    async for user in reaction.users():
                        if not user.bot and user.id not in data["reaction_users"][emoji]:
                            data["reaction_users"][emoji].append(user.id)

            await message.edit(embed=build_embed(data))
            save_vote_data(stored_vote_data)

        except Exception as e:
            print(f"Error loading message {message_id}: {e}")   

bot.run(os.getenv("DISCORD_BOT_TOKEN"))

