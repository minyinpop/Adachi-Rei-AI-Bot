import discord_events
import discord
import asyncio
import json

from configs import DISCORD_BOT_TOKEN
from pathlib import Path

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

message_queue = asyncio.Queue()

@client.event
async def on_ready():
    with open(Path(__file__).parent/"discord_configs.json", "r", encoding="utf-8") as f:
        configs = json.load(f)

    for channel_id in configs["target_channel_id"]:
        await discord_events.send_message(
            channel=client.get_channel(channel_id),
            content="【啟動狀態】足立レイ - 啟動\n【思考模型】gemma3:12b"
        )

    client.loop.create_task(message_worker())

    print("【✅】Discord 機器人啟動成功。")

@client.event
async def on_message(message: discord.Message):
    with open(Path(__file__).parent/"discord_configs.json", "r", encoding="utf-8") as f:
        configs = json.load(f)

    if message.channel.id not in configs["target_channel_id"]:
        return

    if message.author.bot:
        return

    if message.author.id == client.user.id:
        return

    if not message.mentions:
        return

    if client.user not in message.mentions:
        return

    await message_queue.put(message)

async def message_worker():
    while True:
        message = await message_queue.get()

        await message_handler(message)

        message_queue.task_done()

async def message_handler(message: discord.Message):
    match message.type:
        case discord.MessageType.default:
            pass

        case discord.MessageType.reply:
            await discord_events.reply_message(
                message=message,
                content="【系統提示】當前還無法處裡回覆類型的訊息。"
            )

            print(f"【❗】當前還無法處裡回覆類型的訊息。")

        case _:
            await discord_events.reply_message(
                message=message,
                content=f"【系統提示】當前還無法處裡 {message.type} 類型的訊息"
            )

            print(f"【❗】在 message_handler 裡傳入了未被登記的訊息類型：{message.type}")

client.run(DISCORD_BOT_TOKEN)