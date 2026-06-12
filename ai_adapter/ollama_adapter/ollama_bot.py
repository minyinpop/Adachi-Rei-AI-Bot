from pathlib import Path
from ollama import chat

async def ask_ollama(sender_message: dict):
    with open(Path(__file__).parent.parent/"ai_identity.md", "r", encoding="utf-8") as f:
        ai_identity = f.read()

    with open(Path(__file__).parent.parent/"ai_soul.md", "r", encoding="utf-8") as f:
        ai_soul = f.read()

    chat_messages = [
        {
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
        },
        {
            "role": "user",
            "content": sender_message["message"]
        }
    ]

    if "attachments" in sender_message:
        chat_messages[1]["images"] = sender_message["attachments"]

    response = chat(
        model="gemma3:12b",
        messages=chat_messages,
        options={
            "num_ctx": 16384
        }
    )

    # 短期記憶的 assistant 範例格式
    short_memory_example = [
        {
            "role": "assistant",
            "content": f"足立レイ：{response['message']['content']}"
        }
    ]

    return response["message"]["content"]