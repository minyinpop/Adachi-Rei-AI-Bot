import data.qq_repository as qq_db
import websockets
import requests
import asyncio
import json
import os

from ai_adapter.ollama_adapter.ollama_bot import ask_ollama
from dotenv import load_dotenv

load_dotenv()

MESSAGE_QUEUE = asyncio.Queue()

async def handle_onebot(websocket):
    print("【✅】NapCat 連線成功")

    # 檢查並初始化 SQLite
    qq_db.init_short_memory()
    # ===

    asyncio.create_task(message_worker())

    try:
        async for message in websocket:
            await MESSAGE_QUEUE.put(message)

    except websockets.ConnectionClosed:
        print("【🛑】NapCat 連線中斷")

async def message_worker():
    while True:
        message = await MESSAGE_QUEUE.get()

        await message_handler(message)

        MESSAGE_QUEUE.task_done()

async def message_handler(message):
    message = json.loads(message)

    print("【💬】訊息傳入")
    print(json.dumps(
        message,
        ensure_ascii=False,
        indent=4
    ))

    match message["post_type"]:
        case "message_sent":
            has_at = False

            sender_message = {
                "id": message["sender"]["user_id"],
                "name": message["sender"]["nickname"],
                "role": "user",
                "message": []
            }

            match message["message_type"]:
                case "group":
                    if message["group_id"] != 689904439:
                        return

                    for segment in message["message"]:
                        match segment["type"]:
                            case "at":
                                if int(segment["data"]["qq"]) == message["self_id"]:
                                    if has_at:
                                        # Bot 已經被 @ 但又被 @，就把 Bot 的名字給加入到文字裡
                                        sender_message["message"].append(message["sender"]["nickname"])

                                    else:
                                        # Bot 第一次被 @
                                        has_at = True

                                    continue

                                else:
                                    # 其他人被 @，就去查詢那個人的名稱，然後添加到文字裡
                                    response = requests.post(
                                        url="http://127.0.0.1:3001/get_group_member_info",
                                        headers={
                                            "Authorization": os.getenv("QQ_BOT_TOKEN")
                                        },
                                        json={
                                            "group_id": message["group_id"],
                                            "user_id": int(segment["data"]["qq"])
                                        }
                                    )

                                    print("=== 獲取成員資料 ===")
                                    print(json.dumps(
                                        response.json(),
                                        ensure_ascii=False,
                                        indent=4
                                    ))

                                    sender_message["message"].append(response.json()["data"]["nickname"])

                            case "text":
                                sender_message["message"].append(segment["data"]["text"])

                            case _:
                                print(f"【❗】未被登記的 message：{segment['type']}")
                                continue

                    if not has_at:
                        return

                    sender_message["message"] = "".join(sender_message["message"])

                    requests.post(
                        url="http://127.0.0.1:3001/send_group_msg",
                        headers={
                            "Authorization": os.getenv("QQ_BOT_TOKEN")
                        },
                        json={
                            "group_id": message["group_id"],
                            "message": [
                                {
                                    "type": "text",
                                    "data": {
                                        "text": "正在思考中......"
                                    }
                                }
                            ]
                        }
                    )

                    ai_response = await ask_ollama(
                        sender_message=sender_message,
                        short_memory_messages=[],
                        long_memory=[]
                    )

                    requests.post(
                        url="http://127.0.0.1:3001/send_group_msg",
                        headers={
                            "Authorization": os.getenv("QQ_BOT_TOKEN")
                        },
                        json={
                            "group_id": message["group_id"],
                            "message": [
                                {
                                    "type": "reply",
                                    "data": {
                                        "id": str(message["message_id"])
                                    }
                                },
                                {
                                    "type": "text",
                                    "data": {
                                        "text": ai_response
                                    }
                                }
                            ]
                        }
                    )

                    qq_db.insert_short_memory(
                        ai_provider="ollama",
                        target_id=message["target_id"],
                        user_id=sender_message["id"],
                        user_name=sender_message["name"],
                        user_role=sender_message["role"],
                        user_message_type="",
                        user_message=sender_message["message"]
                    )

                    qq_db.insert_short_memory(
                        ai_provider="ollama",
                        target_id=message["target_id"],
                        user_id=-1,
                        user_name="足立レイ",
                        user_role="assistant",
                        user_message_type="",
                        user_message=ai_response
                    )

                    return

                case "private":
                    print("【❗】私人回覆尚未實作")
                    return

                case _:
                    print(f"【❗】未被登記的 message_type：{message['message_type']}")
                    return

        case _:
            print(f"【❗】未被登記的 post_type：{message['post_type']}")
            return

async def main():
    server = await websockets.serve(
        handle_onebot,
        host="127.0.0.1",
        port=3000
    )

    print("【✅】OneBot WebSocket Server 啟動")
    await server.wait_closed()

if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("【🛑】OneBot WebSocket Server 關閉")