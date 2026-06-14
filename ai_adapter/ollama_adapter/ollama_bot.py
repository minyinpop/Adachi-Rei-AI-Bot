import asyncio
import json

from typing import Callable, Awaitable
from pathlib import Path
from ollama import chat

async def ask_ollama(sender_message: dict,
                     short_memory: dict,
                     long_memory: list,
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

    # 長期記憶提示詞
    relationship_prompts = []
    event_prompts = []

    for memory in long_memory:
        if "relationship" in memory:
            relationship_prompts.append(memory["relationship"])

        if "event" in memory:
            event_prompts.append(memory["event"])

    relationship_prompts = "\n".join(relationship_prompts)
    event_prompts = "\n".join(event_prompts)

    print("=== 與他人的關係 ===")
    print(relationship_prompts)
    print("=== 正在發生的事件 ===")
    print(event_prompts)
    # ===

    # 系統提示詞
    system_prompt = {
        "role": "system",
        "content":
            f"""
             === 自我身份 ===
             {ai_identity}
             
             === 核心人格 ===
             {ai_soul}
             
             === 環境參數 ===
             正在跟你對話的使用者名稱：{sender_message["name"]}
             
             === 與他人的關係 ===
             {relationship_prompts}
            
             === 正在發生的事件 ===
             {event_prompts}
             """
    }
    # ===

    # 對話提示詞
    chat_prompt = {
        "role": "user",
        "content": sender_message["message"]
    }

    if "attachments" in sender_message:
        chat_prompt["images"] = sender_message["attachments"]
    # ===

    message_prompts = []
    message_prompts.extend(short_memory)
    message_prompts.append(system_prompt)
    message_prompts.append(chat_prompt)

    response = await asyncio.to_thread(
        chat,
        model=ollama_configs["response_model"],
        messages=message_prompts,
        options={
            "num_ctx": 32768
        },
        keep_alive="1h"
    )

    if done_callback:
        await done_callback()

    return response