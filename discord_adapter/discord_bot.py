import discord_events
import discord
import asyncio
import json

from discord_create_room_view import CreateRoomView
from ollama_adapter.ollama_bot import ask_ollama
from openai_adapter.openai_bot import ask_openai
from configs import DISCORD_BOT_TOKEN
from pathlib import Path
from enum import Enum

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

message_queue = asyncio.Queue()

@client.event
async def on_ready():
    client.loop.create_task(message_worker())

    with open(Path(__file__).parent/"settings/discord_configs.json", "r", encoding="utf-8") as f:
        discord_configs = json.load(f)

    client.add_view(
        CreateRoomView(),
        message_id=discord_configs["create_room_view_message_id"]
    )

    if discord_configs["create_room_view_message_id"] == -1:
        message = await client.get_channel(discord_configs["create_room_view_channel_id"]).send(
            view=CreateRoomView()
        )

        discord_configs["create_room_view_message_id"] = message.id

        with open(Path(__file__).parent/"settings/discord_configs.json", "w", encoding="utf-8") as f:
            json.dump(
                discord_configs,
                fp=f,
                ensure_ascii=False,
                indent=4
            )
    """
    with open(Path(__file__).parent.parent/"ai_adapter/openai_adapter/openai_configs.json", "r", encoding="utf-8") as f:
        openai_configs = json.load(f)

    with open(Path(__file__).parent.parent/"ai_adapter/ollama_adapter/ollama_configs.json", "r", encoding="utf-8") as f:
        ollama_configs = json.load(f)
        
    await discord_events.send_message(
        channel=client.get_channel(discord_configs["state_channel_id"]),
        content="\n".join(
            [
                "【啟動日誌】啟動檢查報告",
                "",
                "【系統檢查】私聊頻道 OK",
                "【系統檢查】聊天頻道 OK",
                "【系統檢查】聊地頻道 OK",
                "",
                "【系統訊息】足立レイ - 啟動",
                "",
                f"【聊天模型】{openai_configs['response_model']}",
                f"【聊地模型】{ollama_configs['response_model']}",
                "",
                "【系統訊息】所有系統 OK - 歡迎聊天"
            ]
        )
    )
    """

@client.event
async def on_message(message: discord.Message):
    with open(Path(__file__).parent/"settings/discord_configs.json", "r", encoding="utf-8") as f:
        discord_configs = json.load(f)

    if message.author.bot:
        return

    if message.author.id == client.user.id:
        return

    if not message.mentions:
        return

    if client.user not in message.mentions:
        return

    if message.channel.id == discord_configs["public_openai_chat_channel_id"]:
        await message_queue.put((True, message))
        return

    if message.channel.id == discord_configs["public_ollama_chat_channel_id"]:
        await message_queue.put((False, message))
        return

    for content in discord_configs["private_ollama_chat_channel_id"]:
        if message.channel.id != content["channel_id"]:
            continue

        await message_queue.put((False, message))
        return

async def message_worker():
    while True:
        (is_openai, message) = await message_queue.get()

        await message_handler(is_openai, message)

        message_queue.task_done()

async def message_handler(is_openai: bool, message: discord.Message):
    match message.type:
        case discord.MessageType.default | discord.MessageType.reply:
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
                    if "attachments" in sender_message:
                        sender_message["attachments"].append(attachment)
                    else:
                        sender_message["attachments"] = [attachment]

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

                with open(Path(__file__).parent/"settings/short_memories.json", "r", encoding="utf-8") as f:
                    short_memories = json.load(f)

                with open(Path(__file__).parent/"settings/long_memory.json", "r", encoding="utf-8") as f:
                    long_memory = json.load(f)

                is_new_memory = True

                ai_response = ""
                short_memory_messages = []

                target_channel_index = 0

                for i in range(len(short_memories)):
                    target_channel_index = i

                    if short_memories[target_channel_index]["channel_id"] == message.channel.id:
                        is_new_memory = False

                        short_memory_messages = short_memories[target_channel_index]["messages"]
                        break

                if is_new_memory:
                    short_memory_messages = []

                if is_openai:
                    (ai_response, short_memory_messages) = await ask_openai(
                        sender_message=sender_message,
                        short_memory_messages=short_memory_messages,
                        long_memory=long_memory,
                        think_callback=think_callback,
                        done_callback=done_callback
                    )
                else:
                    (ai_response, short_memory_messages) = await ask_ollama(
                        sender_message=sender_message,
                        short_memory_messages=short_memory_messages,
                        long_memory=long_memory,
                        think_callback=think_callback,
                        done_callback=done_callback
                    )

                await discord_events.reply_message(
                    message=message,
                    content=ai_response
                )

                if is_new_memory:
                    short_memories.append(
                        {
                            "channel_id": message.channel.id,
                            "messages": short_memory_messages
                        }
                    )

                else:
                    short_memories[target_channel_index]["messages"] = short_memory_messages

                with open(Path(__file__).parent/"settings/short_memories.json", "w", encoding="utf-8") as f:
                    json.dump(
                        obj=short_memories,
                        fp=f,
                        ensure_ascii=False,
                        indent=4
                    )

        case discord.MessageType.call:
            await discord_events.reply_message(
                message=message,
                content="【系統提示】當前還無法處裡來電訊息。"
            )

            print(f"【❗】當前還無法處裡來電訊息。")

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