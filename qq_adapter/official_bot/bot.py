"""Official QQ event adapter and inference concurrency control."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

import httpx
import data.qq_repository as qq_db
from .commands import OwariOfficialBotCommands
from .message_utils import (
    BOT_DISPLAY_NAME,
    _clean_message_content,
    _extract_mentioned_members,
    _get_message_value,
    _get_sender_name,
    _collect_image_attachments,
    _is_bot_mentioned,
    _resolve_bot_ids,
    _strip_leading_bot_mention,
)
from .response_pipeline import OwariOfficialBotResponse
from .sdk import GROUP_MESSAGE_CREATE, EventParser, MediaUploader, QQApiClient
from .settings import ALLOWED_GROUP_OPENIDS


class OwariOfficialBot(OwariOfficialBotCommands, OwariOfficialBotResponse):
    def __init__(self, api: QQApiClient, http_client: httpx.AsyncClient):
        self.api = api
        self.http_client = http_client
        self.event_parser = EventParser()
        self.media_uploader = MediaUploader(api_client=api, http_client=http_client)
        self._seen_message_ids: set[str] = set()
        self._inference_state_lock = asyncio.Lock()
        self._active_group_openid: str | None = None
        # Shown by /status.
        self.started_at = datetime.now()
        self.replies_sent = 0

    async def on_message_event(self, event_type: str, raw_event: dict) -> None:
        # The bot runs in "receive all group messages" mode; only messages that
        # @ the bot are answered, the rest are kept as short-term context.
        if event_type != GROUP_MESSAGE_CREATE:
            return
        event = self.event_parser.parse(event_type, raw_event)
        if event is None:
            return

        chat_scope = str(_get_message_value(event, "chat_scope", ""))
        if chat_scope != "group":
            return

        bot_mentioned = _is_bot_mentioned(raw_event)
        group_openid = str(_get_message_value(event, "chat_id", ""))
        if not group_openid:
            if bot_mentioned:
                print("【⚠】官方 QQ 群事件沒有 group_openid，略過。")
            return
        if ALLOWED_GROUP_OPENIDS and group_openid not in ALLOWED_GROUP_OPENIDS:
            if bot_mentioned:
                print("【🔒】此群不在 QQ_OWARI_ALLOWED_GROUP_OPENIDS 白名單，略過。")
            return
        self._update_member_roster(event, raw_event, group_openid)
        if not bot_mentioned:
            self._remember_group_chat(event, raw_event, group_openid)
            return

        message_id = str(_get_message_value(event, "message_id", ""))
        if not message_id:
            print("【⚠】官方 QQ 群事件沒有 message_id，無法安全建立被動回覆，略過。")
            return
        if message_id and message_id in self._seen_message_ids:
            return
        if message_id:
            self._seen_message_ids.add(message_id)
            if len(self._seen_message_ids) > 2048:
                self._seen_message_ids.clear()
                self._seen_message_ids.add(message_id)

        bot_ids = _resolve_bot_ids(raw_event)
        raw_content = str(raw_event.get("content") or "")
        if raw_content:
            # Keep other members' raw mentions; only the bot's own leading
            # mention is removed so slash commands still match.
            message_content = _strip_leading_bot_mention(raw_content, bot_ids)
        else:
            # event.content is already cleaned by EventParser.
            message_content = str(_get_message_value(event, "content", ""))
        sender_user_id = str(_get_message_value(event, "user_id", "unknown"))
        mentioned_members = _extract_mentioned_members(event, bot_ids)
        user_text = _clean_message_content(message_content, mentioned_members, bot_ids)
        if mentioned_members:
            names = "、".join(member["name"] for member in mentioned_members)
            print(
                f"【👥】QQ 事件中解析到 {len(mentioned_members)} 位被@群友：{names}。"
            )
        elif re.search(r"@\S+", user_text.replace(f"@{BOT_DISPLAY_NAME}", "")):
            print("【⚠】訊息含有@文字，但 QQ 事件未提供可識別的被@成員 ID。")
        image_attachments = _collect_image_attachments(event)
        if not user_text and not image_attachments:
            print("【ℹ】收到空白或只有 @ 提及的訊息，略過。")
            return

        command = user_text.strip().casefold()
        if await self.handle_command(
            event=event,
            group_openid=group_openid,
            message_id=message_id,
            user_text=user_text,
            command=command,
        ):
            return

        user_id = sender_user_id
        user_name = _get_sender_name(event)
        print(f"【💬】官方 QQ 群訊息：{user_name}: {user_text}")

        # Reserve the single local Ollama/voice pipeline atomically. The QQ
        # gateway may deliver callbacks concurrently, so a plain boolean can
        # allow two messages to pass the check at the same time.
        async with self._inference_state_lock:
            busy_group_openid = self._active_group_openid
            if busy_group_openid is None:
                self._active_group_openid = group_openid

        if busy_group_openid is not None:
            busy_notice = (
                "消息处理中，请稍后。"
                if busy_group_openid == group_openid
                else "正在接收其他群的电波……请稍后再呼叫。"
            )
            if await self._reply(group_openid, message_id, busy_notice, "忙碌提示"):
                print(
                    "【📡】本機回覆流程忙碌，已回覆："
                    f"{'同群稍候' if busy_group_openid == group_openid else '其他群電波中'}"
                )
            return

        try:
            await self._handle_message(
                group_openid=group_openid,
                message_id=message_id,
                user_id=user_id,
                user_name=user_name,
                user_text=user_text,
                image_attachments=image_attachments,
                mentioned_members=mentioned_members,
            )
        except Exception as exc:
            print(f"【❌】處理 QQ 官方群訊息失敗：{exc}")
            await self._reply(
                group_openid, message_id,
                "刚刚处理消息时出了点问题，请稍后再试。",
                "錯誤提示",
            )
        finally:
            async with self._inference_state_lock:
                if self._active_group_openid == group_openid:
                    self._active_group_openid = None

    def _update_member_roster(self, event, raw_event: dict, group_openid: str) -> None:
        """Record the sender's and @-mentioned members' current display names."""
        members = [(
            str(_get_message_value(event, "user_id", "")),
            _get_sender_name(event),
        )]
        members += [
            (member["user_id"], member["name"])
            for member in _extract_mentioned_members(event, _resolve_bot_ids(raw_event))
            if member["name"] != "被@的群成员"
        ]
        try:
            for user_id, user_name in members:
                qq_db.upsert_group_member(group_openid, user_id, user_name)
        except Exception as exc:
            print(f"【⚠】群成員名冊更新失敗：{exc}")

    def _remember_group_chat(self, event, raw_event: dict, group_openid: str) -> None:
        """Store a message that did not @ the bot so later replies have context."""
        bot_ids = _resolve_bot_ids(raw_event)
        mentioned_members = _extract_mentioned_members(event, bot_ids)
        content = str(raw_event.get("content") or _get_message_value(event, "content", ""))
        text = _clean_message_content(content, mentioned_members, bot_ids)
        if _collect_image_attachments(event):
            text = f"{text} [图片]".strip()
        if not text:
            return
        try:
            qq_db.insert_short_memory(
                ai_provider="ollama",
                target_id=group_openid,
                user_id=str(_get_message_value(event, "user_id", "unknown")),
                user_name=_get_sender_name(event),
                user_role="user",
                user_message_type=qq_db.GROUP_CHAT_MESSAGE_TYPE,
                user_message=text,
            )
        except Exception as exc:
            print(f"【⚠】群聊訊息寫入短期記憶失敗：{exc}")

