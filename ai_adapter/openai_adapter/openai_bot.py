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
        ai_identity = f.read()

    with open(Path(__file__).parent.parent/"ai_soul.md", "r", encoding="utf-8") as f:
        ai_soul = f.read()

    with open(Path(__file__).parent/"openai_configs.json", "r", encoding="utf-8") as f:
        openai_configs = json.load(f)

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
    system_prompt = f"""
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