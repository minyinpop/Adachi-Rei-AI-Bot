import discord_adapter.discord_events as discord_events
import data.x_repository as x_db
import asyncio
import aiohttp
import discord
import json
import os

from datetime import datetime
from pathlib import Path

async def start_system(client: discord.Client):
    #串流啟動通知
    print("=== X 串流啟動 ===")
    print(f"串流識別碼：{os.getpid()}")
    print(f"串流啟動時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    # ===

    # 資料庫檢查
    x_db.init_x_post()
    # ===

    async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(
                total=None,
                connect=60,
                sock_connect=60,
                sock_read=None
            )
    ) as session:
        while True:
            print("=== 連線時間 ===")
            print(f"【⌚】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("【🔄️】正在嘗試連線到 X")

            async with session.get(
                url=(
                    "https://api.x.com/2/tweets/search/stream"
                    "?tweet.fields=author_id,created_at,referenced_tweets"
                ),
                headers={
                    "Authorization": f"Bearer {os.getenv('X_BEARER_TOKEN')}"
                }
            ) as response:

                # https://docs.x.com/x-api/fundamentals/response-codes-and-errors
                match response.status:
                    case 200 | 201 | 204:
                        print(f"【✅】成功連線到 X，狀態碼：{response.status}")

                    case 400 | 401 | 403 | 404 | 409 | 429:
                        print(f"【❌】無法連線到 X，客戶端狀態碼：{response.status}")
                        print("【⌛】30 秒後嘗試重新連線")
                        await asyncio.sleep(30)
                        continue

                    case 500 | 502 | 503 | 504:
                        print(f"【❌】無法連線到 X，伺服器端狀態碼：{response.status}")
                        print("【⌛】30 秒後嘗試重新連線")
                        await asyncio.sleep(30)
                        continue

                    case _:
                        print(f"【❓】連線到 X 時發現了未被登記的狀態碼：{response.status}")
                        print("【⌛】30 秒後嘗試重新連線")
                        await asyncio.sleep(30)
                        continue

                try:
                    with open(Path(__file__).parent/"x_configs.json", "r", encoding="utf-8") as f:
                        x_configs = json.load(f)

                    async for line in response.content:
                        line = line.decode("utf-8").strip()

                        if not line:
                            continue

                        data = json.loads(line)

                        if "data" not in data:
                            print("【❓】收到非推文事件：")
                            print(json.dumps(
                                data,
                                ensure_ascii=False,
                                indent=4
                            ))

                            continue

                        print(json.dumps(
                            data,
                            ensure_ascii=False,
                            indent=4
                        ))

                        if x_db.check_post_exists(data["data"]["id"]):
                            continue

                        for config in x_configs:
                            if int(data["data"]["author_id"]) != config["account_id"]:
                                continue

                            post_id = data["data"]["id"]
                            post_url = f"https://x.com/i/web/status/{data['data']['id']}"
                            post_title = data["data"]["text"]

                            x_db.insert_x_post(
                                post_id=post_id,
                                post_url=post_url,
                                post_title=post_title
                            )

                            await discord_events.send_message(
                                channel=client.get_channel(config["channel_id"]),
                                content=post_url
                            )

                except Exception as e:
                    print(f"【❌】向 X 請求時發生錯誤")
                    print(f"例外類型：{type(e).__name__}")
                    print(f"例外內容：{repr(e)}")

                finally:
                    print("【⌛】10 秒後嘗試重新連線")
                    await asyncio.sleep(10)