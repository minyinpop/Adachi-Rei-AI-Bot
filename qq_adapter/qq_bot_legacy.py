import data.qq_repository as qq_db
import websockets
import requests
import asyncio
import json
import os
import random
import re
import math

from ai_adapter.ollama_adapter.ollama_bot import ask_ollama_with_audio_decision
from openutau_adapter.openutau_bot import (
    generate_openutau_audio,
    generate_openutau_song_excerpt,
    get_available_song_titles,
    is_audio_enabled,
)
from dotenv import load_dotenv

load_dotenv()

MESSAGE_QUEUE = asyncio.Queue()


def _is_song_catalog_question(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text).casefold()
    explicitly_requests_song = any(phrase in normalized for phrase in (
        "唱給我聽", "唱给我听", "唱一首", "唱一段", "唱一下", "現在唱", "现在唱"
    ))
    if explicitly_requests_song:
        return False
    patterns = (
        r"(?:會|能|可以|会).{0,2}唱.{0,8}(?:什麼|什么|哪些|哪幾|哪几)",
        r"(?:曲庫|曲目清單|曲目列表|歌單|歌单)",
        r"(?:有哪些|有什麼|有什么|收錄了哪些|收录了哪些).{0,6}(?:歌|歌曲)",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _is_song_capability_question(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text).casefold()
    asks_capability = re.search(r"(?:會不會|会不会|會|会|能不能|能|可以).{0,8}唱.{0,20}(?:嗎|吗|呢|\?|？)", normalized)
    asks_to_hear = any(phrase in normalized for phrase in (
        "唱給我聽", "唱给我听", "唱一段", "唱一下", "現在唱", "现在唱", "唱出來", "唱出来"
    ))
    return bool(asks_capability) and not asks_to_hear


def _is_explicit_singing_request(text: str) -> bool:
    if _is_song_catalog_question(text) or _is_song_capability_question(text):
        return False
    normalized = re.sub(r"\s+", "", text).casefold()
    cues = (
        "唱給我聽", "唱给我听", "唱一首", "唱首歌", "唱一段", "唱一下",
        "隨機唱", "随机唱", "播放一首", "隨機播放", "随机播放",
        "開始唱", "开始唱", "開始播放", "开始播放", "播一首",
    )
    return any(cue in normalized for cue in cues)


def _requested_song_title(text: str, titles: list[str]) -> str | None:
    normalized_text = re.sub(r"[\W_]+", "", text.casefold(), flags=re.UNICODE)
    matches = [
        title for title in titles
        if re.sub(r"[\W_]+", "", title.casefold(), flags=re.UNICODE) in normalized_text
    ]
    return max(matches, key=len) if matches else None


def _is_short_confirmation(text: str) -> bool:
    normalized = re.sub(r"[\s!！。,.，?？]+", "", text).casefold()
    return normalized in {"請", "好", "好啊", "可以", "嗯", "來吧", "来吧", "開始", "开始"}


def _previous_assistant_offered_playback(recent_messages: list, user_id: int) -> bool:
    if len(recent_messages) < 2:
        return False
    previous_user = recent_messages[-2]
    previous_assistant = recent_messages[-1]
    if previous_user[6] != "user" or previous_user[4] != user_id:
        return False
    if previous_assistant[6] != "assistant":
        return False
    response = previous_assistant[8]
    return any(phrase in response for phrase in (
        "隨機播放一首", "随机播放一首", "就開始唱", "就開始播放", "就開始囉", "就开始了"
    ))


def _selected_song_from_recent_catalog(user_text: str, recent_messages: list, user_id: int) -> str | None:
    if len(recent_messages) < 2:
        return None

    previous_user = recent_messages[-2]
    previous_assistant = recent_messages[-1]
    if previous_user[6] != "user" or str(previous_user[4]) != str(user_id):
        return None
    if previous_assistant[6] != "assistant" or not any(
        marker in str(previous_assistant[8])
        for marker in ("目前收錄的歌曲有：", "目前收录的歌曲有：")
    ):
        return None

    offered_titles = [
        title.strip()
        for _, title in re.findall(r"(?m)^\s*(\d+)[.、)）]\s*(.+?)\s*$", previous_assistant[8])
    ]
    number_token = re.search(r"\d+", user_text)
    if number_token:
        choice_index = int(number_token.group())
        if 1 <= choice_index <= len(offered_titles):
            return offered_titles[choice_index - 1]
        # Preserve the typo-tolerant behavior for out-of-range digit strings:
        # "32321" maps to item 3 when the full number is not a valid choice.
        first_digit = number_token.group()[0]
        fallback_index = int(first_digit)
        if 1 <= fallback_index <= len(offered_titles):
            return offered_titles[fallback_index - 1]

    normalized_user_text = re.sub(r"[\W_]+", "", user_text.casefold(), flags=re.UNICODE)
    for title in offered_titles:
        normalized_title = re.sub(r"[\W_]+", "", title.casefold(), flags=re.UNICODE)
        if normalized_title and normalized_title in normalized_user_text:
            return title.strip()
    return None


def _last_played_song_from_memory(recent_messages: list, user_id: int) -> str | None:
    if not recent_messages:
        return None
    previous_user = next(
        (message for message in reversed(recent_messages) if message[6] == "user"),
        None,
    )
    if previous_user is None or str(previous_user[4]) != str(user_id):
        return None
    # A reply may have failed to enter singing mode and been stored as normal
    # prose. Keep the most recent explicit playback marker available so a
    # follow-up can still refer to the song the bot just played.
    for message in reversed(recent_messages[-12:]):
        if message[6] != "assistant":
            continue
        match = re.match(r"^正在播放：(.+?)\s*$", str(message[8]))
        if match:
            return match.group(1)
    return None


def _is_song_duration_followup(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？!?.,~～]+", "", text).casefold()
    has_duration = re.search(r"\d+(?:\.\d+)?(?:秒鐘|秒)", normalized)
    asks_for_longer = any(phrase in normalized for phrase in (
        "唱久一點", "唱久一点",
        "唱的久一點", "唱得久一點", "唱的久一点", "唱得久一点",
        "唱長一點", "唱长一点", "唱的長一點", "唱得長一點",
        "唱的长一点", "唱得长一点", "再長一點", "再长一点",
        "久一點", "久一点",
    ))
    asks_for_longer = asks_for_longer or bool(re.search(
        r"(?:唱|演唱|播放).{0,4}(?:的|得)?(?:更|再)?(?:久|長|长)(?:一點|一点|一些)?",
        normalized,
    ))
    mentions_singing = bool(re.search(r"唱|演唱|播放", normalized))
    states_minimum = any(phrase in normalized for phrase in (
        "至少", "最少", "不少於", "不低於",
    ))
    return bool(asks_for_longer or (has_duration and (states_minimum or mentions_singing)))


def _requested_duration_seconds(text: str) -> int:
    match = re.search(r"(\d+(?:\.\d+)?)(?:秒鐘|秒)", text)
    return max(0, math.ceil(float(match.group(1)))) if match else 0

async def handle_onebot(websocket):
    print("【✅】NapCat 連線成功")

    asyncio.create_task(message_worker())

    try:
        async for message in websocket:
            await MESSAGE_QUEUE.put(message)

    except websockets.ConnectionClosed:
        print("【🛑】NapCat 連線中斷")

async def message_worker():
    while True:
        message = await MESSAGE_QUEUE.get()

        await message_handler(message)

        MESSAGE_QUEUE.task_done()

async def message_handler(message):
    message = json.loads(message)

    print("【💬】訊息傳入")
    print(json.dumps(
        message,
        ensure_ascii=False,
        indent=4
    ))

    match message["post_type"]:
        case "message_sent":
            has_at = False

            sender_message = {
                "id": message["sender"]["user_id"],
                "name": message["sender"]["nickname"],
                "role": "user",
                "message": []
            }

            match message["message_type"]:
                case "group":
                    if message["group_id"] != 689904439:
                        return

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
                        return

                    sender_message["message"] = "".join(sender_message["message"])

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
                                        "text": "正在思考中......"
                                    }
                                }
                            ]
                        }
                    )

                    short_memory_messages = qq_db.get_short_memory(
                        target_id=message["target_id"],
                        limit=8,
                    )
                    print(f"【🧠】載入最近 {len(short_memory_messages)} 則 QQ 對話記憶")
                    ai_result = await ask_ollama_with_audio_decision(
                        sender_message=sender_message,
                        short_memory_messages=short_memory_messages,
                        long_memory=[]
                    )
                    ai_response = ai_result["reply_text"]
                    audio_path = None
                    skip_audio_generation = False
                    skip_audio_reason = ""
                    voice_mode = ai_result.get("voice_mode", "speech")
                    song_query = ai_result.get("song_query", "")
                    song_min_duration = ai_result.get("song_min_duration_seconds", 0)
                    user_text = sender_message["message"]
                    selected_song = _selected_song_from_recent_catalog(
                        user_text,
                        short_memory_messages,
                        sender_message["id"],
                    )
                    previous_song = _last_played_song_from_memory(
                        short_memory_messages,
                        sender_message["id"],
                    )
                    previous_playback_offer = _previous_assistant_offered_playback(
                        short_memory_messages,
                        sender_message["id"],
                    )
                    if previous_song and _is_song_duration_followup(user_text):
                        voice_mode = "sing"
                        song_query = previous_song
                        song_min_duration = max(
                            song_min_duration,
                            _requested_duration_seconds(user_text),
                        )
                        print(
                            f"【🎵】收到延長上一首歌的要求：{previous_song}；"
                            f"至少 {song_min_duration} 秒"
                        )
                    elif selected_song:
                        voice_mode = "sing"
                        song_query = selected_song
                        song_min_duration = max(
                            song_min_duration,
                            _requested_duration_seconds(user_text),
                        )
                        print(f"【🎵】收到先前曲目清單的選擇：{selected_song}")
                    elif _is_song_catalog_question(user_text):
                        skip_audio_generation = True
                        skip_audio_reason = "曲目清單只傳文字"
                        voice_mode = "speech"
                        song_query = ""
                        song_min_duration = 0
                        titles = get_available_song_titles()
                        if titles:
                            choices = random.SystemRandom().sample(titles, len(titles))
                            ai_response = (
                                "目前收錄的歌曲有：\n"
                                + "\n".join(
                                    f"{index}. {title}"
                                    for index, title in enumerate(choices, start=1)
                                )
                            )
                        else:
                            ai_response = "目前沒有收錄歌曲。"
                    elif previous_playback_offer and _is_short_confirmation(user_text):
                        titles = get_available_song_titles()
                        if titles:
                            voice_mode = "sing"
                            song_query = random.SystemRandom().choice(titles)
                            print(f"【🎵】收到上一輪播放確認，隨機選曲：{song_query}")
                        else:
                            skip_audio_generation = True
                            skip_audio_reason = "曲庫沒有可播放的歌譜"
                            ai_response = "目前沒有收錄歌曲。"
                    elif _is_explicit_singing_request(user_text):
                        titles = get_available_song_titles()
                        if titles:
                            selected_title = _requested_song_title(user_text, titles)
                            voice_mode = "sing"
                            if selected_title:
                                song_query = selected_title
                            elif not song_query or any(word in user_text for word in (
                                "隨機", "随机", "一首", "一首歌"
                            )):
                                song_query = random.SystemRandom().choice(titles)
                            song_min_duration = max(
                                song_min_duration,
                                _requested_duration_seconds(user_text),
                            )
                            print(f"【🎵】偵測到明確演唱要求，選曲：{song_query}")
                        else:
                            skip_audio_generation = True
                            skip_audio_reason = "曲庫沒有可播放的歌譜"
                            ai_response = "目前沒有收錄歌曲。"
                    elif _is_song_capability_question(user_text) and voice_mode == "sing":
                        voice_mode = "speech"
                        song_query = ""
                        song_min_duration = 0
                        ai_result["voice_text"] = (
                            ai_result["voice_text"] or "ききたいきょくがあったら、おしえてね"
                        )
                    print(
                        f"【🧭】Ollama 語音模式：{voice_mode}；"
                        f"歌曲：{song_query or '未指定'}；"
                        f"演唱秒數下限：{song_min_duration or '未指定'}；"
                        f"語音稿：{ai_result['voice_text'] or '（無）'}；"
                        f"情緒：{ai_result['emotion']}；說話方式：{ai_result['delivery']}；"
                        f"句尾語調：{ai_result['intonation']}；強調詞：{ai_result.get('emphasis_text') or '（無）'}"
                    )

                    if skip_audio_generation:
                        print(f"【📚】{skip_audio_reason}，不生成語音。")
                    elif is_audio_enabled():
                        try:
                            if voice_mode == "sing":
                                audio_path, song_title = await generate_openutau_song_excerpt(
                                    song_query,
                                    minimum_duration_seconds=song_min_duration,
                                )
                                ai_response = f"正在播放：{song_title}"
                            else:
                                audio_path = await generate_openutau_audio(
                                    ai_result["voice_text"],
                                    prosody=ai_result
                                )
                        except Exception as e:
                            print(f"【⚠】OpenUtau 語音生成失敗，仍會送出文字：{e}")
                    else:
                        print("【🔇】OpenUtau 語音功能已關閉，這次只送出文字。")

                    def send_group_message(segments, label):
                        try:
                            response = requests.post(
                                url="http://127.0.0.1:3001/send_group_msg",
                                headers={"Authorization": os.getenv("QQ_BOT_TOKEN")},
                                json={"group_id": message["group_id"], "message": segments},
                                timeout=30
                            )
                            try:
                                result = response.json()
                            except ValueError:
                                result = {}
                            success = (
                                response.ok
                                and result.get("status") == "ok"
                                and result.get("retcode") == 0
                            )
                            if success:
                                print(f"【✅】已將{label}傳送至 QQ 群 {message['group_id']}：{result}")
                            else:
                                print(
                                    f"【❌】NapCat 傳送{label}失敗（群 {message['group_id']}）："
                                    f"HTTP {response.status_code}，{result or response.text}"
                                )
                            return success
                        except requests.RequestException as exc:
                            print(f"【❌】呼叫 NapCat 傳送{label}時發生錯誤：{exc}")
                            return False

                    text_segments = [
                        {"type": "reply", "data": {"id": str(message["message_id"])}},
                        {"type": "text", "data": {"text": ai_response}}
                    ]

                    if audio_path:
                        print(f"【📤】先單獨傳送語音至 QQ 群 {message['group_id']}：{audio_path}")
                        audio_sent = send_group_message(
                            [{"type": "record", "data": {"file": audio_path.resolve().as_uri()}}],
                            "語音"
                        )
                        if not audio_sent:
                            print("【↩】語音傳送失敗，仍會接著送出文字回覆。")
                    send_group_message(text_segments, "文字回覆")

                    qq_db.insert_short_memory(
                        ai_provider="ollama",
                        target_id=message["target_id"],
                        user_id=sender_message["id"],
                        user_name=sender_message["name"],
                        user_role=sender_message["role"],
                        user_message_type="",
                        user_message=sender_message["message"]
                    )

                    qq_db.insert_short_memory(
                        ai_provider="ollama",
                        target_id=message["target_id"],
                        user_id=-1,
                        user_name="足立レイ",
                        user_role="assistant",
                        user_message_type="",
                        user_message=ai_response
                    )

                    return

                case "private":
                    print("【❗】私人回覆尚未實作")
                    return

                case _:
                    print(f"【❗】未被登記的 message_type：{message['message_type']}")
                    return

        case _:
            print(f"【❗】未被登記的 post_type：{message['post_type']}")
            return

async def main():
    qq_db.init_short_memory()
    print("【🧹】已清空 QQ 短期記憶 JSON")
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
