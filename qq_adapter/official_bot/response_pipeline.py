"""Ollama, OpenUTAU, and QQ response pipeline."""
from __future__ import annotations

import random
import re
from urllib.parse import urlparse

import httpx

import data.qq_repository as qq_db
from ai_adapter.ollama_adapter.ollama_bot import ask_ollama_with_audio_decision
from openutau_adapter.openutau_bot import (
    generate_openutau_audio,
    generate_openutau_full_song,
    generate_openutau_song_excerpt,
    get_available_song_titles,
    is_audio_enabled,
)
from qq_adapter.song_intent import (
    format_song_catalog,
    is_explicit_singing_request,
    is_full_song_request,
    is_short_confirmation,
    is_song_capability_question,
    is_song_catalog_question,
    is_song_duration_followup,
    last_played_song_from_memory,
    previous_assistant_offered_playback,
    requested_duration_seconds,
    requested_song_title,
    selected_song_from_recent_catalog,
)
from .base import OwariOfficialBotBase
from .media import _extract_gif_frames
from .message_utils import _get_message_value, _link_member_mentions, to_simplified
from .sdk import MEDIA_TYPE_VOICE, MediaInfo, MessageToCreate, QQMessageType


# Common two-character words that say nothing about who is being discussed.
# Written in Simplified Chinese; text is normalized to Simplified before use.
_GENERIC_BIGRAMS = {
    "我是", "你是", "他是", "她是", "是谁", "什么", "可以", "一下", "知道", "我们",
    "你们", "这个", "那个", "名字", "喜欢", "不是", "就是", "没有", "的人", "一个",
    "自己", "现在", "还是", "因为", "所以", "如果", "但是", "这样", "怎么", "请问",
    "大家", "群友", "称呼", "叫我", "我的", "你的", "妳的", "他的", "她的", "帮我",
    "谢谢", "意义", "某种", "来说", "上来", "义上", "种意",
}
_CJK_RUN_RE = re.compile(r"[぀-ヿ一-鿿]+")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9]{3,}")
MAX_RELATED_MEMBERS = 5


def _keywords(text: str) -> set[str]:
    text = to_simplified(text)
    words = {word.casefold() for word in _LATIN_WORD_RE.findall(text)}
    for run in _CJK_RUN_RE.findall(text):
        words.update(run[index:index + 2] for index in range(len(run) - 1))
    return words - _GENERIC_BIGRAMS


def _select_related_members(
    text: str,
    group_members: list[dict],
    exclude_ids: set[str],
) -> list[dict[str, str]]:
    """Pick members whose identity the model needs to answer this message.

    A member is related when their name appears in the message, or when the
    message shares a word with their identity, e.g. asking about "博士" brings
    in the member whose identity says "可以叫我博士".
    """
    if not text:
        return []
    lowered = to_simplified(text).casefold()
    message_words = _keywords(text)
    by_name, by_keyword = [], []
    for member in group_members:
        if member["user_id"] in exclude_ids or not member["identity"]:
            continue
        name = member["name"]
        entry = {"name": name or "未知名称的群成员", "identity": member["identity"]}
        if len(name) >= 2 and to_simplified(name).casefold() in lowered:
            by_name.append(entry)
        elif message_words & _keywords(member["identity"]):
            by_keyword.append(entry)
    return (by_name + by_keyword)[:MAX_RELATED_MEMBERS]


def _known_member_ids(
    recent_messages: list,
    mentioned_members: list[dict[str, str]],
    group_members: list[dict],
    sender_id: str,
    sender_name: str,
) -> dict[str, str]:
    """Map display names to OpenIDs for members the bot can @ in its reply.

    Names shared by more than one member are dropped, since the bot cannot
    tell which person "@name" means.
    """
    ids_by_name: dict[str, set[str]] = {}
    candidates = [(member["name"], member["user_id"]) for member in group_members]
    candidates += [(row[5], row[4]) for row in recent_messages if row[6] == "user"]
    candidates += [(member["name"], member["user_id"]) for member in mentioned_members]
    candidates.append((sender_name, sender_id))
    for name, member_id in candidates:
        name = str(name or "").strip()
        member_id = str(member_id or "").strip()
        if not name or not member_id or name in {"群友", "被@的群成员"}:
            continue
        ids_by_name.setdefault(name, set()).add(member_id)
    return {
        name: next(iter(ids))
        for name, ids in ids_by_name.items()
        if len(ids) == 1
    }


# QQ only accepts a passive reply within 5 minutes of the message, so stop
# waiting for Ollama early enough to still send voice and text in time.
OLLAMA_REPLY_TIMEOUT_SECONDS = 240


class OwariOfficialBotResponse(OwariOfficialBotBase):
    async def _handle_message(
        self,
        group_openid: str,
        message_id: str,
        user_id: str,
        user_name: str,
        user_text: str,
        image_attachments: list | None = None,
        mentioned_members: list[dict[str, str]] | None = None,
    ) -> None:
        image_attachments = image_attachments or []
        mentioned_members = mentioned_members or []
        thinking_sent = False

        async def send_thinking() -> None:
            nonlocal thinking_sent
            if thinking_sent:
                return
            thinking_sent = await self._reply(
                group_openid, message_id, "分析电波中 ......", "思考提示"
            )

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
                await self._reply(
                    group_openid, message_id,
                    "抱歉，我暂时没能读取这张图片，请重新发送。",
                    "圖片讀取失敗提示",
                )
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
        memory_user_text = user_text or ("[图片]" if image_attachments else "")
        group_members = qq_db.get_group_members(group_openid)
        related_users = _select_related_members(
            user_text,
            group_members,
            exclude_ids={user_id, *(member["user_id"] for member in mentioned_members)},
        )
        if related_users:
            print(
                f"【🔗】依訊息內容帶入 {len(related_users)} 位相關群友身份："
                f"{'、'.join(member['name'] for member in related_users)}"
            )
        user_identity = qq_db.get_user_identity(group_openid, user_id)
        mentioned_users = []
        for member in mentioned_members:
            member_identity = qq_db.get_user_identity(
                group_openid, member["user_id"]
            )
            mentioned_users.append({
                "name": member["name"],
                "identity": member_identity,
            })
        if mentioned_users:
            print(
                f"【👥】解析到 {len(mentioned_users)} 位被@群友，"
                f"身份命中 {sum(bool(member['identity']) for member in mentioned_users)} 位"
            )
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
            "mentioned_users": mentioned_users,
            "related_users": related_users,
        }
        recent_messages = qq_db.get_short_memory(target_id=group_openid, limit=32)
        print(f"【🧠】載入此群最近 {len(recent_messages)} 則短期記憶")

        try:
            ai_result = await ask_ollama_with_audio_decision(
                sender_message=sender_message,
                short_memory_messages=recent_messages,
                long_memory=[],
                think_callback=send_thinking,
                timeout_seconds=OLLAMA_REPLY_TIMEOUT_SECONDS,
            )
        except (TimeoutError, httpx.TimeoutException):
            print(
                f"【⏱】Ollama 超過 {OLLAMA_REPLY_TIMEOUT_SECONDS} 秒仍未完成，"
                "已中止本次生成並通知群友。"
            )
            await self._reply(
                group_openid, message_id,
                "这次思考太久了，热音终先停下来。请换个说法再问一次。",
                "逾時提示",
            )
            return
        ai_response = ai_result["reply_text"]
        voice_mode = ai_result.get("voice_mode", "speech")
        song_query = ai_result.get("song_query", "")
        song_min_duration = ai_result.get("song_min_duration_seconds", 0)
        skip_audio = False
        user_id_for_memory = user_id
        # Song follow-ups look at the last exchange with the bot, so group
        # chatter in between must not break "reply with a number" flows.
        bot_exchanges = [
            message for message in recent_messages
            if message[7] != qq_db.GROUP_CHAT_MESSAGE_TYPE
        ]

        selected_song = selected_song_from_recent_catalog(
            user_text, bot_exchanges, user_id_for_memory
        )
        previous_song = last_played_song_from_memory(
            bot_exchanges, user_id_for_memory
        )
        previous_offer = previous_assistant_offered_playback(
            bot_exchanges, user_id_for_memory
        )
        song_titles = get_available_song_titles()
        named_song = requested_song_title(user_text, song_titles)
        named_song_request = bool(
            named_song
            and re.search(r"唱|播放|演唱", user_text)
            and not is_song_catalog_question(user_text)
            and not is_song_capability_question(user_text)
        )

        if previous_song and is_song_duration_followup(user_text):
            voice_mode = "sing"
            song_query = previous_song
            song_min_duration = max(
                song_min_duration, requested_duration_seconds(user_text)
            )
            print(
                f"【↩】識別到歌曲續唱要求：{song_query}；"
                f"指定時長：{song_min_duration or '使用預設時長'} 秒"
            )
        elif selected_song:
            voice_mode = "sing"
            song_query = selected_song
            song_min_duration = max(
                song_min_duration, requested_duration_seconds(user_text)
            )
        elif is_song_catalog_question(user_text):
            skip_audio = True
            voice_mode = "speech"
            ai_response = format_song_catalog(
                random.SystemRandom().sample(song_titles, len(song_titles))
            )
        elif previous_offer and is_short_confirmation(user_text):
            if song_titles:
                voice_mode = "sing"
                song_query = random.SystemRandom().choice(song_titles)
            else:
                skip_audio = True
                ai_response = format_song_catalog([])
        elif is_explicit_singing_request(user_text) or named_song_request:
            if not song_titles:
                skip_audio = True
                ai_response = format_song_catalog([])
            else:
                voice_mode = "sing"
                model_song_title = requested_song_title(song_query, song_titles)
                if named_song:
                    song_query = named_song
                elif model_song_title and not any(
                    word in user_text for word in ("隨機", "随机", "一首")
                ):
                    song_query = model_song_title
                else:
                    song_query = random.SystemRandom().choice(song_titles)
                song_min_duration = max(
                    song_min_duration, requested_duration_seconds(user_text)
                )
                print(f"【🎵】官方 Bot 偵測到唱歌要求，選曲：{song_query}")
        elif is_song_capability_question(user_text) and voice_mode == "sing":
            voice_mode = "speech"
            song_query = ""
            song_min_duration = 0
            ai_result["voice_text"] = ai_result.get("voice_text") or "ききたいきょくがあったら、おしえてね"

        if ai_result.get("_text_only_fallback"):
            # The structured voice response failed twice; preserve the plain
            # Ollama answer instead of attempting synthesis with missing fields.
            skip_audio = True
            voice_mode = "speech"
            song_query = ""
            ai_response = ai_result["reply_text"]
            print("【⚠】本輪採用純文字備援，不執行 OpenUTAU 合成。")

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
        full_song = voice_mode == "sing" and is_full_song_request(user_text)
        if not skip_audio and is_audio_enabled():
            try:
                if full_song:
                    # A whole song takes about a minute to render, so say so
                    # first instead of leaving the group waiting in silence.
                    print(f"【🎼】正在用 OpenUTAU 生成完整歌曲：{song_query}")
                    await self._reply(
                        group_openid, message_id,
                        f"正在准备完整演唱：{song_query or '你选的歌曲'}，大约需要 1～2 分钟。",
                        "全曲準備提示",
                    )
                    audio_path, song_title = await generate_openutau_full_song(song_query)
                    ai_response = f"正在播放：{song_title}"
                elif voice_mode == "sing":
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
                if full_song:
                    ai_response = (
                        f"抱歉，{song_title}的完整版语音没能发送，"
                        "可能超过了 QQ 语音的长度或大小限制。"
                    )

        outgoing_text, has_mentions = _link_member_mentions(
            ai_response,
            _known_member_ids(
                recent_messages, mentioned_members, group_members, user_id, user_name
            ),
        )
        if not await self._reply(
            group_openid, message_id, outgoing_text, "QQ 群文字回覆",
            markdown=has_mentions,
        ):
            return
        self.replies_sent += 1
        print(f"【✅】QQ 群文字回覆已送出{'（含 @ 群友）' if has_mentions else ''}")

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
