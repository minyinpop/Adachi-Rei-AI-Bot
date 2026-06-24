import os

import websockets
import requests
import asyncio
import json

from dotenv import load_dotenv

load_dotenv()

async def handle_onebot(websocket):
    print("【✅】NapCat 連線成功")

    try:
        async for message in websocket:
            message = json.loads(message)

            print("【💬】訊息傳入")
            print(json.dumps(
                message,
                ensure_ascii=False,
                indent=4
            ))

            has_at = False

            sender_message = {
                "id": int,
                "name": str,
                "role": "user",
                "message": []
            }

            match message["post_type"]:
                case "message_sent":
                    match message["message_type"]:
                        case "group":
                            if message["group_id"] != 689904439:
                                continue

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
                                continue

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
                                                "text": "".join(sender_message["message"])
                                            }
                                        }
                                    ]
                                }
                            )

                            continue

                        case "private":
                            print("【❗】私人回覆尚未實作")
                            continue

                        case _:
                            print(f"【❗】未被登記的 message_type：{message['message_type']}")
                            continue

                case _:
                    print(f"【❗】未被登記的 post_type：{message['post_type']}")
                    continue

    except websockets.ConnectionClosed:
        print("【🛑】NapCat 連線中斷")

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