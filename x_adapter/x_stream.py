import requests
import discord
import asyncio
import json
import time
import os

from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    asyncio.create_task(
        asyncio.to_thread(
            start_system
        )
    )

def start_system():
    while True:
        try:
            print("【🔄️】正在嘗試連線到 X")

            with requests.get(
                url="https://api.x.com/2/tweets/search/stream",
                headers={
                    "Authorization": f"Bearer {os.getenv('X_BEARER_TOKEN')}"
                },
                stream=True,
                timeout=60
            ) as response:

                response.raise_for_status()

                print("【✅】成功連線到 X")

                for line in response.iter_lines():
                    if line:
                        data = json.loads(line)

                        print(json.dumps(
                            data,
                            ensure_ascii=False,
                            indent=4
                        ))

        except Exception as e:
            print(f"【❌】向 X 請求時發生錯誤：{e}")

        finally:
            print("【⌛】10 秒後嘗試重新連線")
            time.sleep(10)

client.run(os.getenv("DISCORD_BOT_TOKEN"))