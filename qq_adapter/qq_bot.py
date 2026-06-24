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

            match message["post_type"]:
                case "message_sent":
                    match message["message_type"]:
                        case "group":
                            if message["group_id"] != 689904439:
                                continue

                            for segment in message["message"]:
                                match segment["type"]:
                                    case "at":
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

                                        if int(segment["data"]["qq"]) == message["self_id"]:
                                            pass

                                    case "text":
                                        pass

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
                                                "text": "訊息發送成功！"
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