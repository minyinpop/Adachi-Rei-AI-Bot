import asyncio
import json

from typing import Callable, Awaitable
from pathlib import Path
from ollama import chat

async def ask_ollama(sender_message: dict,
                     short_memory_messages: list,
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
        "role": sender_message["role"],
        "content": sender_message["message"]
    }

    if "attachments" in sender_message:
        for attachment in sender_message["attachments"]:
            attachment = await attachment.read()

            if "images" in chat_prompt:
                chat_prompt["images"].append(attachment)
            else:
                chat_prompt["images"] = [attachment]
    # ===

    message_prompts = [system_prompt]
    message_prompts.extend(short_memory_messages)
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

    # 儲存提示詞
    short_memory_messages.append(
        {
            "role": "user",
            "content": sender_message["message"]
        }
    )

    short_memory_messages.append(
        {
            "role": "assistant",
            "content": response["message"]["content"]
        }
    )
    # ===

    if done_callback:
        await done_callback()

    print(f"\n")
    print(f"Token 傳入：{response['prompt_eval_count']}")
    print(f"Token 回覆：{response['eval_count']}")
    print(f"Token 總共：{response['prompt_eval_count'] + response['eval_count']}")
    print(f"Token 使用：{response['prompt_eval_count'] / 16384 * 100:.2f} %")
    print(f"Token 速度：{response['eval_count'] / response['eval_duration'] * 1e9:.2f} toks/s")

    return response["message"]["content"], short_memory_messages