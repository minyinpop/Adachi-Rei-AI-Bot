import discord_events
import discord
import asyncio
import json

from ollama_adapter.ollama_bot import ask_ollama
from configs import DISCORD_BOT_TOKEN
from pathlib import Path
from enum import Enum

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

message_queue = asyncio.Queue()

@client.event
async def on_ready():
    with open(Path(__file__).parent/"short_memory.json", "w", encoding="utf-8") as f:
        json.dump(
            obj=[],
            fp=f,
            ensure_ascii=False,
            indent=4
        )

    with open(Path(__file__).parent/"discord_configs.json", "r", encoding="utf-8") as f:
        discord_configs = json.load(f)

    with open(Path(__file__).parent.parent/"ai_adapter/ollama_adapter/ollama_configs.json", "r", encoding="utf-8") as f:
        ollama_configs = json.load(f)

    for channel_id in discord_configs["target_channel_id"]:
        await discord_events.send_message(
            channel=client.get_channel(channel_id),
            content=f"【啟動狀態】足立レイ - 啟動\n【思考模型】{ollama_configs['response_model']}"
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
            await discord_events.add_reaction(
                message=message,
                emoji=Reaction.read.value
            )

            sender_message = {
                "id": message.author.id,
                "name": message.author.name,
                "role": "user",
                "message": message.clean_content
            }

            for attachment in message.attachments:
                if attachment.content_type.startswith("image/"):
                    image_data = await attachment.read()

                    if "attachments" in sender_message:
                        sender_message["attachments"].append(image_data)

                    else:
                        sender_message["attachments"] = [image_data]

                else:
                    await discord_events.reply_message(
                        message=message,
                        content=f"【系統提示】當前還無法偵測 {attachment.content_type} 類型的附件。"
                    )

            async with message.channel.typing():
                async def think_callback():
                    await discord_events.remove_reaction(
                        message=message,
                        emoji=Reaction.read.value,
                        member=client.user
                    )

                    await discord_events.add_reaction(
                        message=message,
                        emoji=Reaction.think.value
                    )

                async def done_callback():
                    await discord_events.remove_reaction(
                        message=message,
                        emoji=Reaction.think.value,
                        member=client.user
                    )

                    await discord_events.add_reaction(
                        message=message,
                        emoji=Reaction.done.value
                    )

                with open(Path(__file__).parent/"short_memory.json", "r", encoding="utf-8") as f:
                    short_memory = json.load(f)

                with open(Path(__file__).parent/"long_memory.json", "r", encoding="utf-8") as f:
                    long_memory = json.load(f)

                ollama_response = await ask_ollama(
                    sender_message=sender_message,
                    short_memory=short_memory,
                    long_memory=long_memory,
                    think_callback=think_callback,
                    done_callback=done_callback
                )

                await discord_events.reply_message(
                    message=message,
                    content=ollama_response["message"]["content"]
                )

                short_memory.append(
                    {
                        "role": "user",
                        "content": message.clean_content
                    }
                )

                short_memory.append(
                    {
                        "role": "assistant",
                        "content": ollama_response["message"]["content"]
                    }
                )

                with open(Path(__file__).parent/"short_memory.json", "w", encoding="utf-8") as f:
                    json.dump(
                        obj=short_memory,
                        fp=f,
                        ensure_ascii=False,
                        indent=4
                    )

                print("=== 回應報告 ===")
                print(f"Token 傳入：{ollama_response['prompt_eval_count']}")
                print(f"Token 回覆：{ollama_response['eval_count']}")
                print(f"Token 總共：{ollama_response['prompt_eval_count'] + ollama_response['eval_count']}")
                print(f"Token 使用：{ollama_response['prompt_eval_count'] / 16384 * 100:.2f} %")
                print(f"Token 速度：{ollama_response['eval_count'] / ollama_response['eval_duration'] * 1e9:.2f} toks/s")

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

class Reaction(Enum):
    read  = "👀"
    think = "🤔"
    done  = "💬"

client.run(DISCORD_BOT_TOKEN)