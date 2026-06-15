import textwrap
import asyncio
import json
import os

from dotenv import load_dotenv
from openai import OpenAI
from pathlib import Path

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def ask_openai(sender_message: dict,
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

    with open(Path(__file__).parent/"openai_configs.json", "r", encoding="utf-8") as f:
        openai_configs = json.load(f)

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
    system_prompt = "\n".join(
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
    # ===

    # 對話提示詞
    chat_prompt = {
        "role": sender_message["role"],
        "content": [
            {
                "type": "input_text",
                "text": sender_message["message"]
            }
        ]
    }

    if "attachments" in sender_message:
        for attachment in sender_message["attachments"]:
            chat_prompt["content"].append(
                {
                    "type": "input_image",
                    "image_url": attachment.url
                }
            )
    # ===

    message_prompts = []
    message_prompts.extend(short_memory_messages)
    message_prompts.append(chat_prompt)

    response = await asyncio.to_thread(
        client.responses.create,
        model=openai_configs["response_model"],
        instructions=system_prompt,
        input=message_prompts
    )

    # 儲存提示詞
    short_memory_messages.append(chat_prompt)

    short_memory_messages.append(
        {
            "role": "assistant",
            "content": response.output_text
        }
    )
    # ===

    if done_callback:
        await done_callback()

    return response.output_text, short_memory_messages