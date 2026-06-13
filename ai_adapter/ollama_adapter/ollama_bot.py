import asyncio
import json

from pathlib import Path
from typing import Callable, Awaitable

from ollama import chat

async def ask_ollama(sender_message: dict,
                     short_memory: dict,
                     think_callback: Callable[[], Awaitable[None]] | None = None,
                     done_callback: Callable[[], Awaitable[None]] | None = None):
    if think_callback:
        await think_callback()

    with open(Path(__file__).parent.parent/"ai_identity.md", "r", encoding="utf-8") as f:
        ai_identity = f.read()

    with open(Path(__file__).parent.parent/"ai_soul.md", "r", encoding="utf-8") as f:
        ai_soul = f.read()

    with open(Path(__file__).parent/"ollama_configs.json", "r", encoding="utf-8") as f:
        ollama_configs = json.load(f)

    system_prompt = {
        "role": "system",
        "content":
            f"""
             === 自我身份 ===
             {ai_identity}
             
             === 核心人格 ===
             {ai_soul}
             
             === 環境參數 ===
             使用者名稱：{sender_message["name"]}
             """
    }

    chat_prompt = {
        "role": "user",
        "content": sender_message["message"]
    }

    if "attachments" in sender_message:
        chat_prompt["images"] = sender_message["attachments"]

    message_prompts = [system_prompt]
    message_prompts.extend(short_memory)
    message_prompts.append(chat_prompt)

    response = await asyncio.to_thread(
        chat,
        model=ollama_configs["response_model"],
        messages=message_prompts,
        options={
            "num_ctx": 16384
        },
        keep_alive="1h"
    )

    if done_callback:
        await done_callback()

    return response