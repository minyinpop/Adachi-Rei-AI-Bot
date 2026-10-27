"""QQ 官方機器人 WebSocket adapter。

以官方 QQ Bot API 收取群聊 @ 訊息，沿用專案既有的 Ollama、短期記憶與
OpenUTAU 生成器。執行前需在專案根目錄 .env 設定：
QQ_OWARI_BOT_ID、QQ_OWARI_BOT_SECRET。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import sys
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageSequence
from dotenv import load_dotenv

ROOT_PATH = Path(__file__).resolve().parents[1]
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))
load_dotenv(ROOT_PATH / ".env")

try:
    from qqbot_agent_sdk import (
        EventParser,
        MEDIA_TYPE_VOICE,
        MediaInfo,
        MediaUploader,
        MessageToCreate,
        QQApiClient,
        QQMessageType,
        QQWebSocket,
        WSCallbacks,
    )
except ImportError as exc:
    raise SystemExit(
        "缺少 QQ Bot SDK。請在專案虛擬環境執行："
        "python -m pip install -r qq_adapter/requirements_official.txt"
    ) from exc

import data.qq_repository as qq_db
from ai_adapter.ollama_adapter.ollama_bot import ask_ollama_with_audio_decision
from openutau_adapter.openutau_bot import (
    generate_openutau_audio,
    generate_openutau_song_excerpt,
    get_available_song_titles,
    is_audio_enabled,
)
from qq_adapter.qq_bot_legacy import (
    _is_explicit_singing_request,
    _is_short_confirmation,
    _is_song_capability_question,
    _is_song_catalog_question,
    _is_song_duration_followup,
    _last_played_song_from_memory,
    _previous_assistant_offered_playback,
    _requested_duration_seconds,
    _requested_song_title,
    _selected_song_from_recent_catalog,
)


APP_ID = os.getenv("QQ_OWARI_BOT_ID", "").strip()
APP_SECRET = os.getenv("QQ_OWARI_BOT_SECRET", "").strip()
OLLAMA_CONFIG_PATH = ROOT_PATH / "ai_adapter" / "ollama_adapter" / "ollama_configs.json"
MODEL_SWITCH_COMMANDS = {
    "/model_set_27b": "gemma3:27b",
    "/model_set_e4b": "gemma4:e4b",
    # Keep the previous command names working for existing command panels.
    "/set_gemma3:27b": "gemma3:27b",
    "/set_gemma4:e4b": "gemma4:e4b",
}
ALLOWED_GROUP_OPENIDS = {
    group_id.strip()
    for group_id in os.getenv("QQ_OWARI_ALLOWED_GROUP_OPENIDS", "").split(",")
    if group_id.strip()
}
def _clean_message_content(content: str) -> str:
    """移除 QQ Bot 的 @提及標記，留下交給 Ollama 的文字。"""
    content = re.sub(r"<@!?[^>]+>", "", content)
    return content.strip()


def _get_message_value(event, key: str, default=""):
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)


def _is_image_attachment(attachment) -> bool:
    content_type = str(_get_message_value(attachment, "content_type", "")).lower()
    filename = str(_get_message_value(attachment, "filename", "")).lower()
    url = str(_get_message_value(attachment, "url", "")).lower().split("?", 1)[0]
    return content_type.startswith("image/") or any(
        filename.endswith(ext) or url.endswith(ext)
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
    )


def _set_ollama_response_model(model_name: str) -> str:
    if model_name not in MODEL_SWITCH_COMMANDS.values():
        raise ValueError(f"不允許切換到未登記的模型：{model_name}")
    config = json.loads(OLLAMA_CONFIG_PATH.read_text(encoding="utf-8"))
    previous_model = str(config.get("response_model", ""))
    config["response_model"] = model_name
    temp_path = OLLAMA_CONFIG_PATH.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(OLLAMA_CONFIG_PATH)
    return previous_model


def _get_ollama_response_model() -> str:
    config = json.loads(OLLAMA_CONFIG_PATH.read_text(encoding="utf-8"))
    return str(config.get("response_model", "")).strip()


def _extract_gif_frames(image_bytes: bytes, frame_limit: int = 4) -> list[bytes] | None:
    """Return representative GIF frames as PNG bytes, or None for non-GIFs."""
    with Image.open(BytesIO(image_bytes)) as image:
        if image.format != "GIF":
            return None

        frame_count = getattr(image, "n_frames", 1)
        if frame_count <= 1:
            selected_indices = [0]
        else:
            durations = []
            for frame in ImageSequence.Iterator(image):
                durations.append(max(1, int(frame.info.get("duration", 100))))
            total_duration = sum(durations)
            targets = [0, total_duration * 0.34, total_duration * 0.67, total_duration * 0.95]
            selected_indices = []
            for target in targets[:frame_limit]:
                elapsed = 0
                selected = frame_count - 1
                for index, duration in enumerate(durations):
                    elapsed += duration
                    if elapsed >= target:
                        selected = index
                        break
                if selected not in selected_indices:
                    selected_indices.append(selected)

        png_frames = []
        for frame_index in selected_indices:
            image.seek(frame_index)
            frame = image.convert("RGBA")
            background = Image.new("RGBA", frame.size, "white")
            background.alpha_composite(frame)
            frame = background.convert("RGB")
            frame.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            output = BytesIO()
            frame.save(output, format="PNG", optimize=True)
            png_frames.append(output.getvalue())
        return png_frames


class OwariOfficialBot:
    def __init__(self, api: QQApiClient, http_client: httpx.AsyncClient):
        self.api = api
        self.http_client = http_client
        self.event_parser = EventParser()
        self.media_uploader = MediaUploader(api_client=api, http_client=http_client)
        self._seen_message_ids: set[str] = set()
        self._inference_state_lock = asyncio.Lock()
        self._active_group_openid: str | None = None

    async def on_message_event(self, event_type: str, raw_event: dict) -> None:
        event = self.event_parser.parse(event_type, raw_event)
        if event is None:
            return

        chat_scope = str(_get_message_value(event, "chat_scope", ""))
        if chat_scope != "group":
            return

        group_openid = str(_get_message_value(event, "chat_id", ""))
        if not group_openid:
            print("【⚠】官方 QQ 群事件沒有 group_openid，略過。")
            return
        if ALLOWED_GROUP_OPENIDS and group_openid not in ALLOWED_GROUP_OPENIDS:
            print("【🔒】此群不在 QQ_OWARI_ALLOWED_GROUP_OPENIDS 白名單，略過。")
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

        user_text = _clean_message_content(str(_get_message_value(event, "content", "")))
        inbound_attachments = list(_get_message_value(event, "attachments", []) or [])
        for element in (_get_message_value(event, "msg_elements", []) or []):
            inbound_attachments.extend(_get_message_value(element, "attachments", []) or [])
        image_attachments = [
            attachment
            for attachment in inbound_attachments
            if _is_image_attachment(attachment)
        ]
        unique_images = {}
        for attachment in image_attachments:
            image_url = str(_get_message_value(attachment, "url", ""))
            if image_url:
                unique_images.setdefault(image_url, attachment)
        image_attachments = list(unique_images.values())
        if not user_text and not image_attachments:
            print("【ℹ】收到空白或只有 @ 提及的訊息，略過。")
            return

        command = user_text.strip().casefold()
        if command in {"/model_show", "/model"}:
            try:
                current_model = _get_ollama_response_model()
                response = (
                    f"当前使用的 Ollama 模型：{current_model}。"
                    if current_model
                    else "当前尚未设置 Ollama 对话模型。"
                )
                await self.api.send_text(
                    "group", group_openid, response,
                    reply_to=message_id,
                    markdown=False,
                )
                print(f"【🧠】{command}：{current_model or '未設定'}")
            except Exception as exc:
                print(f"【❌】讀取目前 Ollama 模型失敗：{exc}")
                try:
                    await self.api.send_text(
                        "group", group_openid,
                        "暂时无法读取当前模型，请稍后再试。",
                        reply_to=message_id,
                        markdown=False,
                    )
                except Exception as send_exc:
                    print(f"【❌】目前模型讀取失敗提示發送失敗：{send_exc}")
            return

        identity_command_prefix = "/identity_set"
        is_set_identity_command = (
            command == identity_command_prefix
            or (
                command.startswith(identity_command_prefix)
                and command[len(identity_command_prefix):len(identity_command_prefix) + 1].isspace()
            )
        )
        # Backward-compatible alias for the earlier command name.
        if not is_set_identity_command:
            identity_command_prefix = "/set_identity"
            is_set_identity_command = (
                command == identity_command_prefix
                or (
                    command.startswith(identity_command_prefix)
                    and command[len(identity_command_prefix):len(identity_command_prefix) + 1].isspace()
                )
            )
        if is_set_identity_command:
            identity = user_text.strip()[len(identity_command_prefix):].strip()
            user_id = str(_get_message_value(event, "user_id", "unknown"))
            if not identity:
                response = f"请在 {identity_command_prefix} 后输入你的身份介绍，最多 300 个字。"
            elif len(identity) > qq_db.MAX_USER_IDENTITY_LENGTH:
                response = (
                    f"身份介绍有 {len(identity)} 个字，超过 300 字上限，请缩短后重试。"
                )
            else:
                try:
                    qq_db.set_user_identity(group_openid, user_id, identity)
                    response = "已记下你的身份设定。"
                except Exception as exc:
                    print(f"【❌】儲存 QQ 使用者身份失敗：{exc}")
                    response = "身份设定暂时保存失败，请稍后再试。"
            try:
                await self.api.send_text(
                    "group", group_openid, response,
                    reply_to=message_id,
                    markdown=False,
                )
                print(
                    f"【🪪】{identity_command_prefix}：群 {group_openid}、使用者 {user_id}，"
                    f"輸入 {len(identity)} 字"
                )
            except Exception as exc:
                print(f"【❌】身份設定結果發送失敗：{exc}")
            return

        if command in {"/identity_clean", "/forget_identity"}:
            user_id = str(_get_message_value(event, "user_id", "unknown"))
            try:
                removed = qq_db.clear_user_identity(group_openid, user_id)
                response = (
                    "已清除你在本群设置的身份。"
                    if removed
                    else "本群还没有保存你的身份设定。"
                )
                await self.api.send_text(
                    "group", group_openid, response,
                    reply_to=message_id,
                    markdown=False,
                )
                print(
                    f"【🪪】/identity_clean：群 {group_openid}、使用者 {user_id}，"
                    f"已清除 {removed}"
                )
            except Exception as exc:
                print(f"【❌】清除 QQ 使用者身份失敗：{exc}")
                try:
                    await self.api.send_text(
                        "group", group_openid,
                        "身份设定暂时无法清除，请稍后再试。",
                        reply_to=message_id,
                        markdown=False,
                    )
                except Exception as send_exc:
                    print(f"【❌】身份清除失敗提示發送失敗：{send_exc}")
            return

        if command in MODEL_SWITCH_COMMANDS:
            selected_model = MODEL_SWITCH_COMMANDS[command]
            try:
                previous_model = _set_ollama_response_model(selected_model)
                response = (
                    f"已将全局 Ollama 模型切换为 {selected_model}。"
                    if previous_model != selected_model
                    else f"当前全局 Ollama 模型已经是 {selected_model}。"
                )
                await self.api.send_text(
                    "group", group_openid, response,
                    reply_to=message_id,
                    markdown=False,
                )
                print(
                    f"【🧠】收到模型切換指令，已切換全局 Ollama 模型："
                    f"{previous_model or '未設定'} -> {selected_model}"
                )
            except Exception as exc:
                print(f"【❌】切換 Ollama 模型失敗：{exc}")
                try:
                    await self.api.send_text(
                        "group", group_openid,
                        "模型切换失败，请稍后再试。",
                        reply_to=message_id,
                        markdown=False,
                    )
                except Exception as send_exc:
                    print(f"【❌】模型切換失敗提示發送失敗：{send_exc}")
            return

        if command in {
            "/forget", "forget", "/clean_message", "clean_message",
            "/clean_memory", "clean_memory", "/chat_clean", "chat_clean",
        }:
            removed_count = qq_db.clear_short_memory(target_id=group_openid)
            try:
                await self.api.send_text(
                    "group",
                    group_openid,
                    "已清除本群的聊天记忆。",
                    reply_to=message_id,
                    markdown=False,
                )
                print(
                    f"【🧹】已清除此群短期記憶 {removed_count} 則，並回覆清除完成。"
                )
            except Exception as exc:
                print(f"【❌】此群記憶已清除，但清除完成訊息發送失敗：{exc}")
            return

        if command in {"/songs", "songs", "/song_show", "song_show"}:
            song_titles = get_available_song_titles()
            user_id = str(_get_message_value(event, "user_id", "unknown"))
            user_name = str(_get_message_value(event, "user_name", "群友"))
            response = (
                "目前收录的歌曲有：\n"
                + "\n".join(
                    f"{index}. {title}"
                    for index, title in enumerate(song_titles, start=1)
                )
                if song_titles else "目前没有收录歌曲。"
            )
            try:
                await self.api.send_text(
                    "group",
                    group_openid,
                    response,
                    reply_to=message_id,
                    markdown=False,
                )
                qq_db.insert_short_memory(
                    ai_provider="ollama",
                    target_id=group_openid,
                    user_id=user_id,
                    user_name=user_name,
                    user_role="user",
                    user_message_type="command",
                    user_message=user_text,
                )
                qq_db.insert_short_memory(
                    ai_provider="ollama",
                    target_id=group_openid,
                    user_id=-1,
                    user_name="足立レイ",
                    user_role="assistant",
                    user_message_type="command_response",
                    user_message=response,
                )
                print(f"【🎵】/songs 已列出全部曲目，共 {len(song_titles)} 首。")
            except Exception as exc:
                print(f"【❌】/songs 曲目清單發送失敗：{exc}")
            return

        user_id = str(_get_message_value(event, "user_id", "unknown"))
        user_name = str(_get_message_value(event, "user_name", "群友"))
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
            try:
                await self.api.send_text(
                    "group",
                    group_openid,
                    busy_notice,
                    reply_to=message_id,
                    markdown=False,
                )
                print(
                    "【📡】本機回覆流程忙碌，已回覆："
                    f"{'同群稍候' if busy_group_openid == group_openid else '其他群電波中'}"
                )
            except Exception as exc:
                print(f"【⚠】無法傳送忙碌提示：{exc}")
            return

        try:
            await self._handle_message(
                group_openid=group_openid,
                message_id=message_id,
                user_id=user_id,
                user_name=user_name,
                user_text=user_text,
                image_attachments=image_attachments,
            )
        except Exception as exc:
            print(f"【❌】處理 QQ 官方群訊息失敗：{exc}")
            try:
                await self.api.send_text(
                    "group", group_openid,
                    "刚刚处理消息时出了点问题，请稍后再试。",
                    reply_to=message_id,
                    markdown=False,
                )
            except Exception as send_exc:
                print(f"【❌】連錯誤提示也無法送出：{send_exc}")
        finally:
            async with self._inference_state_lock:
                if self._active_group_openid == group_openid:
                    self._active_group_openid = None

    async def _handle_message(
        self,
        group_openid: str,
        message_id: str,
        user_id: str,
        user_name: str,
        user_text: str,
        image_attachments: list | None = None,
    ) -> None:
        image_attachments = image_attachments or []
        thinking_sent = False

        async def send_thinking() -> None:
            nonlocal thinking_sent
            if thinking_sent:
                return
            try:
                await self.api.send_text(
                    "group", group_openid, "分析电波中 ......",
                    reply_to=message_id,
                    markdown=False,
                )
                thinking_sent = True
            except Exception as exc:
                print(f"【⚠】無法傳送思考提示，繼續處理：{exc}")

        prompt_text = user_text
        image_data = []
        gif_frames_added = 0
        for attachment in image_attachments[:4]:
            if len(image_data) >= 4:
                break
            image_url = str(_get_message_value(attachment, "url", "")).strip()
            if image_url.startswith("//"):
                image_url = "https:" + image_url
            parsed_url = urlparse(image_url)
            host = (parsed_url.hostname or "").lower()
            trusted_image_host = any(
                host == domain or host.endswith("." + domain)
                for domain in ("qq.com", "qq.com.cn", "qpic.cn", "gtimg.cn")
            )
            if parsed_url.scheme != "https" or not trusted_image_host:
                print(f"【⚠】圖片附件網址主機不在允許清單（{host or '無主機'}），已略過。")
                continue
            try:
                async with self.http_client.stream(
                    "GET", image_url, follow_redirects=False, timeout=30.0
                ) as response:
                    response.raise_for_status()
                    content_length = int(response.headers.get("content-length", "0") or 0)
                    if content_length > 15 * 1024 * 1024:
                        raise ValueError("圖片超過 15 MB 上限")
                    image_bytes = bytearray()
                    async for chunk in response.aiter_bytes():
                        image_bytes.extend(chunk)
                        if len(image_bytes) > 15 * 1024 * 1024:
                            raise ValueError("圖片超過 15 MB 上限")
                downloaded_image = bytes(image_bytes)
                gif_frames = _extract_gif_frames(downloaded_image)
                if gif_frames is None:
                    image_data.append(downloaded_image)
                else:
                    frames_to_add = gif_frames[: 4 - len(image_data)]
                    image_data.extend(frames_to_add)
                    gif_frames_added += len(frames_to_add)
                    print(
                        f"【🎞】GIF 已抽取 {len(frames_to_add)} 張代表畫格。"
                    )
            except Exception as exc:
                print(f"【⚠】QQ 圖片下載失敗，略過此圖：{exc}")
        if image_attachments and not image_data:
            print("【⚠】沒有成功讀取任何圖片，改以文字內容處理。")
            if not user_text:
                try:
                    await self.api.send_text(
                        "group", group_openid,
                        "抱歉，我暂时没能读取这张图片，请重新发送。",
                        reply_to=message_id,
                        markdown=False,
                    )
                except Exception as exc:
                    print(f"【❌】圖片讀取失敗提示發送失敗：{exc}")
                return
        elif len(image_data) < len(image_attachments):
            prompt_text = (
                (prompt_text + "\n") if prompt_text else ""
            ) + "（有部分图片未能读取，请只根据成功读取的图片和文字回答，不要猜测未读取的图片。）"
        if image_data:
            print(f"【🖼】已下載 {len(image_data)} 張圖片，準備交給 Ollama 分析。")
            if not prompt_text:
                prompt_text = "请结合图片内容自然地回复；如果用户没有提出具体问题，请简短描述图片。"
            if gif_frames_added:
                prompt_text += (
                    f"\n其中有 {gif_frames_added} 张图是按时间顺序抽取的 GIF 代表画格；"
                    "请结合画格顺序判断可见的动作变化，不要推测画格之间未显示的内容。"
                )
        memory_user_text = user_text or ("[圖片]" if image_attachments else "")
        user_identity = qq_db.get_user_identity(group_openid, user_id)
        print(
            f"【🪪】長期身份查詢：群 {group_openid[:8]}…、"
            f"使用者 {user_id[:8]}…、命中 {bool(user_identity)}、"
            f"字數 {len(user_identity)}"
        )
        sender_message = {
            "id": user_id,
            "name": user_name,
            "role": "user",
            "message": prompt_text,
            "attachments": image_data,
            "user_identity": user_identity,
        }
        recent_messages = qq_db.get_short_memory(target_id=group_openid, limit=32)
        print(f"【🧠】載入此群最近 {len(recent_messages)} 則短期記憶")

        ai_result = await ask_ollama_with_audio_decision(
            sender_message=sender_message,
            short_memory_messages=recent_messages,
            long_memory=[],
            think_callback=send_thinking,
        )
        ai_response = ai_result["reply_text"]
        voice_mode = ai_result.get("voice_mode", "speech")
        song_query = ai_result.get("song_query", "")
        song_min_duration = ai_result.get("song_min_duration_seconds", 0)
        skip_audio = False
        user_id_for_memory = user_id

        selected_song = _selected_song_from_recent_catalog(
            user_text, recent_messages, user_id_for_memory
        )
        previous_song = _last_played_song_from_memory(
            recent_messages, user_id_for_memory
        )
        previous_offer = _previous_assistant_offered_playback(
            recent_messages, user_id_for_memory
        )
        song_titles = get_available_song_titles()
        named_song = _requested_song_title(user_text, song_titles)
        named_song_request = bool(
            named_song
            and re.search(r"唱|播放|演唱", user_text)
            and not _is_song_catalog_question(user_text)
            and not _is_song_capability_question(user_text)
        )

        if previous_song and _is_song_duration_followup(user_text):
            voice_mode = "sing"
            song_query = previous_song
            song_min_duration = max(
                song_min_duration, _requested_duration_seconds(user_text)
            )
            print(
                f"【↩】識別到歌曲續唱要求：{song_query}；"
                f"指定時長：{song_min_duration or '使用預設時長'} 秒"
            )
        elif selected_song:
            voice_mode = "sing"
            song_query = selected_song
            song_min_duration = max(
                song_min_duration, _requested_duration_seconds(user_text)
            )
        elif _is_song_catalog_question(user_text):
            skip_audio = True
            voice_mode = "speech"
            choices = (
                random.SystemRandom().sample(song_titles, len(song_titles))
                if song_titles else []
            )
            ai_response = (
                "目前收錄的歌曲有：\n"
                + "\n".join(f"{index}. {title}" for index, title in enumerate(choices, 1))
                if choices else "目前沒有收錄歌曲。"
            )
        elif previous_offer and _is_short_confirmation(user_text):
            if song_titles:
                voice_mode = "sing"
                song_query = random.SystemRandom().choice(song_titles)
            else:
                skip_audio = True
                ai_response = "目前沒有收錄歌曲。"
        elif _is_explicit_singing_request(user_text) or named_song_request:
            if not song_titles:
                skip_audio = True
                ai_response = "目前沒有收錄歌曲。"
            else:
                voice_mode = "sing"
                model_song_title = _requested_song_title(song_query, song_titles)
                if named_song:
                    song_query = named_song
                elif model_song_title and not any(
                    word in user_text for word in ("隨機", "随机", "一首")
                ):
                    song_query = model_song_title
                else:
                    song_query = random.SystemRandom().choice(song_titles)
                song_min_duration = max(
                    song_min_duration, _requested_duration_seconds(user_text)
                )
                print(f"【🎵】官方 Bot 偵測到唱歌要求，選曲：{song_query}")
        elif _is_song_capability_question(user_text) and voice_mode == "sing":
            voice_mode = "speech"
            song_query = ""
            song_min_duration = 0
            ai_result["voice_text"] = ai_result.get("voice_text") or "ききたいきょくがあったら、おしえてね"

        audio_path = None
        print(
            f"【🧭】回覆模式：{voice_mode}；歌曲：{song_query or '未指定'}；"
            f"略過音訊：{skip_audio}；可用歌譜：{len(song_titles)} 首"
        )
        if voice_mode == "speech":
            print(
                f"【🎭】語音表情：情緒 {ai_result.get('emotion', 'neutral')}；"
                f"語氣 {ai_result.get('delivery', 'normal')}；"
                f"句尾 {ai_result.get('intonation', 'falling')}；"
                f"強調詞 {ai_result.get('emphasis_text') or '無'}"
            )
        if voice_mode == "sing" and not skip_audio:
            # Keep group-visible song status in Simplified Chinese even if
            # Ollama accidentally returns its Japanese voice draft as reply_text.
            ai_response = f"正在准备演唱：{song_query or '你选的歌曲'}。"
        if not skip_audio and is_audio_enabled():
            try:
                if voice_mode == "sing":
                    print(f"【🎼】正在用 OpenUTAU 生成歌曲片段：{song_query}")
                    audio_path, song_title = await generate_openutau_song_excerpt(
                        song_query,
                        minimum_duration_seconds=song_min_duration,
                    )
                    ai_response = f"正在播放：{song_title}"
                else:
                    audio_path = await generate_openutau_audio(
                        ai_result["voice_text"], prosody=ai_result
                    )
            except Exception as exc:
                print(f"【⚠】OpenUTAU 生成失敗，改送文字：{exc}")
                if voice_mode == "sing":
                    ai_response = (
                        f"抱歉，{song_query or '这首歌'}暂时没有成功生成，请稍后再试。"
                    )
        elif not skip_audio:
            print("【🔇】OpenUTAU 語音功能已關閉，這次只送文字。")
            if voice_mode == "sing":
                ai_response = "歌曲语音功能目前不可用，请稍后再试。"

        if audio_path:
            try:
                print(f"【📤】上傳 QQ 語音：{audio_path}")
                file_info = await self.media_uploader.upload(
                    chat_type="group",
                    chat_id=group_openid,
                    source=str(audio_path),
                    file_type=MEDIA_TYPE_VOICE,
                )
                media_message = MessageToCreate(
                    msg_type=QQMessageType.RICH_MEDIA,
                    msg_seq=self.api.next_msg_seq(),
                    msg_id=message_id,
                    media=MediaInfo(file_info=file_info),
                )
                await self.api.post_group_message(group_openid, media_message)
                print("【✅】QQ 群語音已送出")
            except Exception as exc:
                print(f"【⚠】QQ 語音上傳／傳送失敗，改送文字：{exc}")

        try:
            await self.api.send_text(
                "group", group_openid, ai_response,
                reply_to=message_id,
                markdown=False,
            )
            print("【✅】QQ 群文字回覆已送出")
        except Exception as exc:
            print(f"【❌】QQ 群文字回覆失敗：{exc}")
            return

        qq_db.insert_short_memory(
            ai_provider="ollama",
            target_id=group_openid,
            user_id=user_id,
            user_name=user_name,
            user_role="user",
            user_message_type="",
            user_message=memory_user_text,
        )
        qq_db.insert_short_memory(
            ai_provider="ollama",
            target_id=group_openid,
            user_id=-1,
            user_name="足立レイ",
            user_role="assistant",
            user_message_type="",
            user_message=ai_response,
        )


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

        try:
            await asyncio.Event().wait()
        finally:
            websocket.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("【🛑】QQ_OWARI_BOT 已停止。")
