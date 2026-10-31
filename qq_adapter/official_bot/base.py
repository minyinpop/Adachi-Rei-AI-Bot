"""Shared state and reply helper for the official QQ bot mixins."""
from __future__ import annotations

from datetime import datetime

import httpx

from .sdk import MediaUploader, QQApiClient


class OwariOfficialBotBase:
    api: QQApiClient
    http_client: httpx.AsyncClient
    media_uploader: MediaUploader
    started_at: datetime
    replies_sent: int
    _active_group_openid: str | None

    async def _reply(
        self,
        group_openid: str,
        message_id: str,
        text: str,
        purpose: str,
        markdown: bool = False,
    ) -> bool:
        """Send a passive group text reply; log failures instead of raising."""
        try:
            await self.api.send_text(
                "group", group_openid, text,
                reply_to=message_id,
                markdown=markdown,
            )
            return True
        except Exception as exc:
            print(f"【❌】{purpose}發送失敗：{exc}")
            return False
