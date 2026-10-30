"""Song-request intent detection shared by the QQ adapters."""
from __future__ import annotations

import math
import re


def format_song_catalog(titles: list[str]) -> str:
    """Build the numbered song list that song selection follow-ups parse."""
    if not titles:
        return "目前没有收录歌曲。"
    return "目前收录的歌曲有：\n" + "\n".join(
        f"{index}. {title}" for index, title in enumerate(titles, start=1)
    )


def is_song_catalog_question(text: str) -> bool:
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


def is_song_capability_question(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text).casefold()
    asks_capability = re.search(r"(?:會不會|会不会|會|会|能不能|能|可以).{0,8}唱.{0,20}(?:嗎|吗|呢|\?|？)", normalized)
    asks_to_hear = any(phrase in normalized for phrase in (
        "唱給我聽", "唱给我听", "唱一段", "唱一下", "現在唱", "现在唱", "唱出來", "唱出来"
    ))
    return bool(asks_capability) and not asks_to_hear


def is_explicit_singing_request(text: str) -> bool:
    if is_song_catalog_question(text) or is_song_capability_question(text):
        return False
    normalized = re.sub(r"\s+", "", text).casefold()
    cues = (
        "唱給我聽", "唱给我听", "唱一首", "唱首歌", "唱一段", "唱一下",
        "隨機唱", "随机唱", "播放一首", "隨機播放", "随机播放",
        "開始唱", "开始唱", "開始播放", "开始播放", "播一首",
    )
    return any(cue in normalized for cue in cues)


def requested_song_title(text: str, titles: list[str]) -> str | None:
    normalized_text = re.sub(r"[\W_]+", "", text.casefold(), flags=re.UNICODE)
    matches = [
        title for title in titles
        if re.sub(r"[\W_]+", "", title.casefold(), flags=re.UNICODE) in normalized_text
    ]
    return max(matches, key=len) if matches else None


def is_short_confirmation(text: str) -> bool:
    normalized = re.sub(r"[\s!！。,.，?？]+", "", text).casefold()
    return normalized in {"請", "好", "好啊", "可以", "嗯", "來吧", "来吧", "開始", "开始"}


def previous_assistant_offered_playback(recent_messages: list, user_id: int) -> bool:
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


def selected_song_from_recent_catalog(user_text: str, recent_messages: list, user_id: int) -> str | None:
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


def last_played_song_from_memory(recent_messages: list, user_id: int) -> str | None:
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


def is_song_duration_followup(text: str) -> bool:
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


def requested_duration_seconds(text: str) -> int:
    match = re.search(r"(\d+(?:\.\d+)?)(?:秒鐘|秒)", text)
    return max(0, math.ceil(float(match.group(1)))) if match else 0
