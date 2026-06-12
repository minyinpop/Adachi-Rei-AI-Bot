from pathlib import Path
from ollama import chat

async def ask_ollama(sender_message: dict):
    with open(Path(__file__).parent.parent/"ai_identity.md", "r", encoding="utf-8") as f:
        ai_identity = f.read()

    with open(Path(__file__).parent.parent/"ai_soul.md", "r", encoding="utf-8") as f:
        ai_soul = f.read()

    # 使用者傳入的訊息格式
    sender_message_example = {
        "id": 338558312079687680,
        "name": "minyinpop",
        "role": "user",
        "message": "@足立レイ 今天天氣真不錯，對吧？",
        "attachments": [bytes]
    }

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
                 使用者ID：{sender_message["id"]}
                 使用者名稱：{sender_message["name"]}
                 """
        },
        {
            "role": "user",
            "content": sender_message["message"],
            "images": sender_message["attachments"]
        }
    ]

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