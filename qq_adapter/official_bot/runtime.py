"""Process startup and WebSocket lifecycle."""
from __future__ import annotations

import asyncio
import httpx
import data.qq_repository as qq_db
from .bot import OwariOfficialBot
from .scheduler import load_scheduled_messages, run_scheduled_messages
from .sdk import QQApiClient, QQWebSocket, WSCallbacks
from .settings import APP_ID, APP_SECRET, ALLOWED_GROUP_OPENIDS


async def main() -> None:
    if not APP_ID or not APP_SECRET:
        raise SystemExit(
            "請在專案根目錄 .env 設定 QQ_OWARI_BOT_ID 和 QQ_OWARI_BOT_SECRET。"
        )

    qq_db.init_short_memory()
    qq_db.init_long_memory()
    print("【🧹】已清空 QQ 短期記憶 JSON")

    timeout = httpx.Timeout(60.0, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout) as http_client:
        api = QQApiClient(app_id=APP_ID, client_secret=APP_SECRET)
        api.setup(http_client)
        bot = OwariOfficialBot(api, http_client)

        await api.ensure_token()
        gateway_url = await api.get_gateway_url()
        callbacks = WSCallbacks(
            on_message_event=bot.on_message_event,
            on_connected=lambda: print("【✅】QQ_OWARI_BOT 官方 WebSocket 已連線"),
            on_disconnected=lambda: print("【⚠】QQ 官方 WebSocket 已中斷"),
            on_fatal_error=lambda code, message: print(
                f"【❌】QQ 官方 WebSocket 致命錯誤 {code}：{message}"
            ),
            get_token=api.ensure_token_sync,
            get_gateway_url=api.get_gateway_url_sync,
            get_session=lambda: (None, None),
            set_session=lambda session_id, sequence: None,
            set_heartbeat_interval=lambda interval: None,
            clear_token=api.clear_token,
            fail_pending=lambda reason: print(f"【⚠】未完成的 QQ Bot 請求已終止：{reason}"),
        )
        websocket = QQWebSocket(callbacks=callbacks, log_tag="QQ_OWARI_BOT")
        websocket.start(gateway_url, asyncio.get_running_loop())
        print("【🤖】QQ_OWARI_BOT 已啟動，等待官方群聊 @ 訊息。")
        if ALLOWED_GROUP_OPENIDS:
            print(f"【🔒】群白名單已啟用，共 {len(ALLOWED_GROUP_OPENIDS)} 個群。")
        else:
            print("【ℹ】目前未設定群白名單；僅官方平台推送給機器人的群事件會處理。")

        scheduler_task = asyncio.create_task(
            run_scheduled_messages(api, bot.media_uploader, load_scheduled_messages())
        )
        try:
            await asyncio.Event().wait()
        finally:
            scheduler_task.cancel()
            await websocket.async_stop()
