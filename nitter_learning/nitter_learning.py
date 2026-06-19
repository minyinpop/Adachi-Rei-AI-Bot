import data.nitter_repository as nitter_db
import discord

from playwright.async_api import async_playwright
from configs import DISCORD_BOT_TOKEN

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    async with async_playwright() as p:
        # 檢查資料庫的資料表
        nitter_db.init_nitter_post()
        # ===

        browser = await p.chromium.launch(headless=False)

        page = await browser.new_page()

        await page.goto(url="https://nitter.poast.org/adachirei0")

        await page.wait_for_selector(".timeline-item", timeout=30000)

        title = await page.content()

        with open("page.html", "w", encoding="utf-8") as f:
            f.write(title)

        # 獲取貼文
        posts = page.locator(".timeline-item")

        post_count = await posts.count()

        for i in range(post_count):
            post = posts.nth(i)

            # 檢查重複貼文
            tweet_link = post.locator(".tweet-link")

            href = await tweet_link.get_attribute("href")

            tweet_id = href.split("/status/")[1].split("#")[0]
            tweet_url = f"https://x.com/adachirei0/status/{tweet_id}"

            if nitter_db.check_post_exists(tweet_id):
                continue

            else:
                nitter_db.insert_nitter_post(tweet_id, tweet_url)
            # ===

            title = await post.locator(".tweet-content").text_content()

            content = "\n".join(
                [
                    f"【貼文標題】{title}",
                    f"【網址】{tweet_url}",
                    "==="
                ]
            )

            await client.get_channel(1517217520505851984).send(
                content=content
            )
        # ===

        await page.pause()

client.run(DISCORD_BOT_TOKEN)