import os

import discord
import asyncio

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

current_target = None

@client.event
async def on_ready():
    global current_target
    print(f'Logged in as {client.user}')

    channel_id = 100000000000000004
    current_target = client.get_channel(channel_id)

    client.loop.create_task(send_message())

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    print(f"{message.author}: {message.content}")



async def send_message():
    global current_target
    while True:
        message = await asyncio.get_event_loop().run_in_executor(None, input, "")

        if message.startswith("cd "):
            target_name = message.split(" ")[1]
            if target_name.startswith("<@") and target_name.endswith(">"):
                try:
                    user_id = int(target_name[2:-1].replace('!', ''))
                    user = await client.fetch_user(user_id)
                    
                    if user:
                        current_target = user
                        print(f"已切換到與使用者 {user.name} 的對話")
                    else:
                        print("無法找到該使用者。")
                except Exception as e:
                    print(f"錯誤: {e}")
            else:
                try:
                    new_channel_id = int(target_name)
                    new_channel = client.get_channel(new_channel_id)

                    if new_channel:
                        current_target = new_channel
                        print(f"已切換到頻道 {new_channel.name}")
                    else:
                        print("無效的頻道 ID。")
                except ValueError:
                    print("請輸入有效的頻道 ID 或使用者標籤。")
        else:
            if current_target:
                try:
                    if isinstance(current_target, discord.TextChannel):
                        await current_target.send(message)
                    elif isinstance(current_target, discord.User):
                        await current_target.send(message)
                except Exception as e:
                    print(f"無法發送訊息: {e}")
            else:
                print("請先設定一個頻道或使用者。")

client.run(os.getenv("DISCORD_BOT_TOKEN"))
