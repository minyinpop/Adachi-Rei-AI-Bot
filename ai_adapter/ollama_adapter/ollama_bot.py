import textwrap
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
        ai_identity = textwrap.dedent(f.read()).strip()

    with open(Path(__file__).parent.parent/"ai_soul.md", "r", encoding="utf-8") as f:
        ai_soul = textwrap.dedent(f.read()).strip()

    with open(Path(__file__).parent/"ollama_configs.json", "r", encoding="utf-8") as f:
        ollama_configs = json.load(f)

    # 長期記憶提示詞
    long_memory_prompts = []

    for memory in long_memory:
        long_memory_prompts.append(
            textwrap.dedent(f"""
                 我認識 {memory['name']}
                 {memory['name']} 的姓別是 {memory['gender']}
                 {memory['name']} 是我的 {memory['relationship']}
             """).strip()
        )

    long_memory_prompts = "\n\n".join(long_memory_prompts)
    # ===

    # 系統提示詞
    system_prompt = {
        "role": "system",
        "content": "\n".join(
            [
                "=== 自我身份 ===",
                ai_identity,
                "",
                "=== 核心人格 ===",
                ai_soul,
                "",
                "=== 環境參數 ===",
                f"正在跟你對話的使用者名稱：{sender_message['name']}",
                "",
                "=== 與其他使用者的關係 ===",
                long_memory_prompts
            ]
        )
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

    print(f"Token 傳入：{response['prompt_eval_count']}")
    print(f"Token 回覆：{response['eval_count']}")
    print(f"Token 總共：{response['prompt_eval_count'] + response['eval_count']}")
    print(f"Token 使用：{response['prompt_eval_count'] / 16384 * 100:.2f} %")
    print(f"Token 速度：{response['eval_count'] / response['eval_duration'] * 1e9:.2f} toks/s")

    return response["message"]["content"], short_memory_messages