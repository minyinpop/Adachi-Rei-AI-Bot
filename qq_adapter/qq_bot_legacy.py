import data.qq_repository as qq_db
import websockets
import requests
import asyncio
import json
import os
import random
import re

from ai_adapter.ollama_adapter.ollama_bot import ask_ollama_with_audio_decision
from openutau_adapter.openutau_bot import (
    generate_openutau_audio,
    generate_openutau_song_excerpt,
    get_available_song_titles,
    is_audio_enabled,
)
from dotenv import load_dotenv
from qq_adapter.song_intent import (
    is_song_catalog_question,
    is_song_capability_question,
    is_explicit_singing_request,
    requested_song_title,
    is_short_confirmation,
    previous_assistant_offered_playback,
    selected_song_from_recent_catalog,
    last_played_song_from_memory,
    is_song_duration_followup,
    requested_duration_seconds,
)

load_dotenv()

MESSAGE_QUEUE = asyncio.Queue()


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
                    selected_song = selected_song_from_recent_catalog(
                        user_text,
                        short_memory_messages,
                        sender_message["id"],
                    )
                    previous_song = last_played_song_from_memory(
                        short_memory_messages,
                        sender_message["id"],
                    )
                    previous_playback_offer = previous_assistant_offered_playback(
                        short_memory_messages,
                        sender_message["id"],
                    )
                    if previous_song and is_song_duration_followup(user_text):
                        voice_mode = "sing"
                        song_query = previous_song
                        song_min_duration = max(
                            song_min_duration,
                            requested_duration_seconds(user_text),
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
                            requested_duration_seconds(user_text),
                        )
                        print(f"【🎵】收到先前曲目清單的選擇：{selected_song}")
                    elif is_song_catalog_question(user_text):
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
                    elif previous_playback_offer and is_short_confirmation(user_text):
                        titles = get_available_song_titles()
                        if titles:
                            voice_mode = "sing"
                            song_query = random.SystemRandom().choice(titles)
                            print(f"【🎵】收到上一輪播放確認，隨機選曲：{song_query}")
                        else:
                            skip_audio_generation = True
                            skip_audio_reason = "曲庫沒有可播放的歌譜"
                            ai_response = "目前沒有收錄歌曲。"
                    elif is_explicit_singing_request(user_text):
                        titles = get_available_song_titles()
                        if titles:
                            selected_title = requested_song_title(user_text, titles)
                            voice_mode = "sing"
                            if selected_title:
                                song_query = selected_title
                            elif not song_query or any(word in user_text for word in (
                                "隨機", "随机", "一首", "一首歌"
                            )):
                                song_query = random.SystemRandom().choice(titles)
                            song_min_duration = max(
                                song_min_duration,
                                requested_duration_seconds(user_text),
                            )
                            print(f"【🎵】偵測到明確演唱要求，選曲：{song_query}")
                        else:
                            skip_audio_generation = True
                            skip_audio_reason = "曲庫沒有可播放的歌譜"
                            ai_response = "目前沒有收錄歌曲。"
                    elif is_song_capability_question(user_text) and voice_mode == "sing":
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
