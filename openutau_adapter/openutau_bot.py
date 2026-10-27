import asyncio
import json
import math
import random
import re
import subprocess
import time
import unicodedata
from collections import Counter

from datetime import datetime
from pathlib import Path


ROOT_PATH = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).parent/"openutau_configs.json"
SMALL_KANA = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")
IGNORED = set("、。，．！？!?,.・\n\r\t ")
PAUSE_SECONDS = {
    "、": 0.12,
    ",": 0.12,
    "，": 0.12,
    "。": 0.24,
    ".": 0.24,
    "！": 0.28,
    "!": 0.28,
    "？": 0.24,
    "?": 0.24
}
NUMBER_PATTERN = re.compile(r"\d+(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
NUMBER_DIGIT_READINGS = ("ぜろ", "いち", "に", "さん", "よん", "ご", "ろく", "なな", "はち", "きゅう")
KANJI_DIGITS = ("", "一", "二", "三", "四", "五", "六", "七", "八", "九")
KANJI_GROUP_UNITS = ("", "万", "億", "兆", "京")
KNOWN_ENGLISH_TO_KATAKANA = {"missile": "ミサイル"}
LATIN_LETTER_TO_KATAKANA = {
    "a": "エー", "b": "ビー", "c": "シー", "d": "ディー", "e": "イー",
    "f": "エフ", "g": "ジー", "h": "エイチ", "i": "アイ", "j": "ジェイ",
    "k": "ケー", "l": "エル", "m": "エム", "n": "エヌ", "o": "オー",
    "p": "ピー", "q": "キュー", "r": "アール", "s": "エス", "t": "ティー",
    "u": "ユー", "v": "ブイ", "w": "ダブリュー", "x": "エックス",
    "y": "ワイ", "z": "ゼット",
}


def _four_digit_number_to_kanji(value: int) -> str:
    result = []
    for unit, label in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        digit = value // unit if unit > 1 else value
        digit %= 10
        if not digit:
            continue
        result.append(("" if digit == 1 and unit > 1 else KANJI_DIGITS[digit]) + label)
    return "".join(result)


def _integer_to_japanese_kanji(value: int) -> str:
    if value == 0:
        return "ゼロ"

    original_value = value
    groups = []
    while value:
        groups.append(value % 10_000)
        value //= 10_000
    if len(groups) > len(KANJI_GROUP_UNITS):
        return "".join(NUMBER_DIGIT_READINGS[int(digit)] for digit in str(original_value))

    result = []
    for index in range(len(groups) - 1, -1, -1):
        group = groups[index]
        if not group:
            continue
        prefix = "" if group == 1 and index else _four_digit_number_to_kanji(group)
        result.append(prefix + KANJI_GROUP_UNITS[index])
    return "".join(result)


def _number_to_japanese_reading(match: re.Match[str], converter) -> str:
    number = match.group(0).replace(",", "")
    integer, decimal_point, fraction = number.partition(".")
    leading_zeroes = len(integer) - len(integer.lstrip("0"))
    significant_digits = integer[leading_zeroes:] or "0"

    if leading_zeroes and len(integer) > 1:
        reading = "".join(NUMBER_DIGIT_READINGS[int(digit)] for digit in integer)
    else:
        integer_kanji = _integer_to_japanese_kanji(int(significant_digits))
        if decimal_point:
            reading = "".join(part["hira"] for part in converter.convert(integer_kanji))
        else:
            reading = integer_kanji

    if decimal_point:
        reading += "てん" + "".join(NUMBER_DIGIT_READINGS[int(digit)] for digit in fraction)
    return reading


def load_configs() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def is_audio_enabled() -> bool:
    return bool(load_configs().get("enabled", False))


def get_available_song_titles() -> list[str]:
    songs_dir = Path(__file__).resolve().parent / "songs"
    return sorted(path.stem for path in songs_dir.glob("*.ustx") if path.is_file())


def _resolve_path(path: str) -> Path:
    result = Path(path).expanduser()
    if not result.is_absolute():
        result = ROOT_PATH/result
    return result.resolve()


def _quote_yaml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _normalize_hiragana(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = unicodedata.normalize("NFKC", text)
    for english, katakana in KNOWN_ENGLISH_TO_KATAKANA.items():
        text = re.sub(
            rf"(?i)(?<![A-Za-z]){re.escape(english)}s?(?=\d|[^\w]|$)",
            katakana,
            text,
        )
    # Stray Latin text (such as the reported "p") has no Japanese phoneme
    # mapping. Spell any leftover token using Japanese letter names so one
    # accidental English character cannot abort the entire audio reply.
    text = re.sub(
        r"(?<![A-Za-z])[A-Za-z]+(?![A-Za-z])",
        lambda match: "、".join(
            LATIN_LETTER_TO_KATAKANA[letter] for letter in match.group(0).lower()
        ),
        text,
    )
    try:
        from pykakasi import kakasi
    except ImportError as exc:
        raise RuntimeError(
            "日文語音稿自動轉平假名需要 pykakasi。請執行："
            "python -m pip install -r openutau_adapter/requirements.txt"
        ) from exc

    converter = kakasi()
    text = NUMBER_PATTERN.sub(lambda match: _number_to_japanese_reading(match, converter), text)
    text = "".join(part["hira"] for part in converter.convert(text))
    result = []

    for char in text:
        category = unicodedata.category(char)
        # LLM replies may include parentheses, quotation marks, emoji, or
        # other decorative punctuation. They have no phoneme in the Japanese
        # voicebank, so skip Unicode punctuation/symbol/separator characters
        # instead of failing the entire audio reply.
        if char in IGNORED or char == "ー" or "ぁ" <= char <= "ゖ":
            result.append(char)
            continue
        if category[0] in {"P", "S", "Z", "C"}:
            continue
        raise ValueError(f"語音稿含有日文假名以外的字元：{char}")

    normalized = "".join(result).strip()
    if not normalized or not any("ぁ" <= char <= "ゖ" for char in normalized):
        raise ValueError("語音稿沒有可合成的日文假名。")
    return normalized


def _split_lyrics(text: str) -> list[str]:
    lyrics = []
    for char in text.strip():
        if char in IGNORED:
            continue
        if char in SMALL_KANA and lyrics:
            lyrics[-1] += char
        else:
            lyrics.append(char)
    if not lyrics:
        raise ValueError("語音稿沒有可合成的日文歌詞。")
    return lyrics


def _pauses_after_lyrics(text: str, ticks_per_second: float) -> dict[int, int]:
    pauses = {}
    morae = 0
    for char in text.strip():
        if char in PAUSE_SECONDS:
            pause_ticks = round(PAUSE_SECONDS[char] * ticks_per_second)
            pauses[morae] = max(pauses.get(morae, 0), pause_ticks)
        elif char in IGNORED:
            continue
        elif char in SMALL_KANA and morae:
            continue
        else:
            morae += 1
    return pauses


def _katakana_to_hiragana(text: str) -> str:
    return "".join(
        chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char
        for char in text
    )


def _japanese_pitch_accent_levels(text: str, lyrics: list[str]) -> list[int] | None:
    """Map OpenJTalk accent-phrase patterns onto the generated mora notes.

    Returns -1/1 pitch levels (low/high), or None when the analyzer's mora
    alignment does not match the lyric notes and the caller should use its
    safe fallback contour.
    """
    try:
        import pyopenjtalk

        features = pyopenjtalk.run_frontend(text)
    except Exception as exc:
        print(f"【i】日文音調分析不可用，改用內建簡易 Pitch 曲線：{exc}")
        return None

    accent_phrases = []
    current_phrase = []

    def finish_phrase() -> None:
        nonlocal current_phrase
        if current_phrase:
            accent_phrases.append(current_phrase)
            current_phrase = []

    for feature in features:
        if feature.get("pos") == "記号":
            finish_phrase()
            continue

        reading = _katakana_to_hiragana(feature.get("read", ""))
        morae = _split_lyrics(reading) if reading else []
        if not morae:
            continue

        if current_phrase and feature.get("chain_flag") != 1:
            finish_phrase()
        current_phrase.append((morae, int(feature.get("acc", 0) or 0)))

    finish_phrase()

    levels = []
    for phrase in accent_phrases:
        phrase_mora_count = sum(len(morae) for morae, _ in phrase)
        if not phrase_mora_count:
            continue
        accent = max(0, min(phrase_mora_count, phrase[0][1]))
        if accent == 0:
            # Heiban: low first mora, then high.
            phrase_levels = [-1] + [1] * (phrase_mora_count - 1)
        elif accent == 1:
            # Atamadaka: high first mora, then a drop.
            phrase_levels = [1] + [-1] * (phrase_mora_count - 1)
        else:
            # Nakadaka/odaka: rise after the first mora, drop after nucleus.
            phrase_levels = [-1]
            phrase_levels.extend([1] * (accent - 1))
            phrase_levels.extend([-1] * (phrase_mora_count - accent))

        for morae, _ in phrase:
            levels.extend(phrase_levels[:len(morae)])
            phrase_levels = phrase_levels[len(morae):]

    if len(levels) != len(lyrics):
        print(
            "【i】OpenJTalk 音調分析與歌詞拍數未對齊，"
            f"改用內建簡易 Pitch 曲線（分析 {len(levels)} 拍／歌詞 {len(lyrics)} 拍）。"
        )
        return None
    return levels


def _make_project(template_text: str,
                  singer: str,
                  phonemizer: str,
                  text: str,
                  speech_rate: float,
                  prosody: dict | None = None) -> str:
    start = template_text.find("tracks:\n")
    end = template_text.find("wave_parts:", start)
    if start < 0 or end < 0:
        raise ValueError("OpenUtau 範本格式不正確，找不到 tracks 或 wave_parts。")
    if not 3.0 <= speech_rate <= 10.0:
        raise ValueError("speech_rate 必須介於 3 到 10 morae/秒。")

    tempo_match = re.search(r"(?ms)^tempos:\s*\n.*?^\s+bpm:\s*([0-9]+(?:\.[0-9]+)?)", template_text)
    bpm = float(tempo_match.group(1)) if tempo_match else 120.0
    prosody = prosody or {}
    emotion = prosody.get("emotion", "neutral")
    delivery = prosody.get("delivery", "normal")
    intonation = prosody.get("intonation", "falling")
    expression_gain = 2.5
    # Owari is a calm rescue robot with deliberately restrained affect.
    # Ollama's emotion tags color the delivery slightly instead of turning
    # every reply into strongly human-like acting.
    emotion_pitch = {
        "neutral": 0, "happy": 0, "curious": 1, "comforting": 0,
        "sad": -1, "serious": 0, "playful": 1
    }.get(emotion, 0) * expression_gain
    emotion_pitch = round(emotion_pitch)
    emotion_dynamic = {
        "neutral": 0, "happy": 1, "curious": 0, "comforting": 0,
        "sad": -1, "serious": 0, "playful": 1
    }.get(emotion, 0) * expression_gain
    emotion_dynamic = round(emotion_dynamic)
    emotion_velocity = {
        "neutral": 0, "happy": 1, "curious": 0, "comforting": -1,
        "sad": -2, "serious": 0, "playful": 1
    }.get(emotion, 0) * expression_gain
    emotion_velocity = round(emotion_velocity)
    emotion_breath = {
        "neutral": 0, "happy": 0, "curious": 1, "comforting": 3,
        "sad": 4, "serious": 0, "playful": 0
    }.get(emotion, 0) * expression_gain
    emotion_breath = round(emotion_breath)
    emotion_tension = {
        "neutral": 0, "happy": 1, "curious": 1, "comforting": -1,
        "sad": -2, "serious": 1, "playful": 1
    }.get(emotion, 0) * expression_gain
    emotion_tension = round(emotion_tension)
    delivery_rate = {
        "normal": 1.0, "calm": 0.98, "gentle": 0.98,
        "quick": 1.04, "emphatic": 1.02
    }.get(delivery, 1.0)
    delivery_velocity = {
        "normal": 0, "calm": -1, "gentle": -1, "quick": 1, "emphatic": 2
    }.get(delivery, 0) * expression_gain
    delivery_velocity = round(delivery_velocity)
    delivery_breath = {
        "normal": 0, "calm": 1, "gentle": 1, "quick": 0, "emphatic": 0
    }.get(delivery, 0) * expression_gain
    delivery_breath = round(delivery_breath)
    delivery_tension = {
        "normal": 0, "calm": -1, "gentle": -1, "quick": 1, "emphatic": 2
    }.get(delivery, 0) * expression_gain
    delivery_tension = round(delivery_tension)
    delivery_attack = {
        "normal": 0, "calm": -2, "gentle": -3, "quick": 2, "emphatic": 3
    }.get(delivery, 0) * expression_gain
    delivery_attack = round(delivery_attack)
    delivery_decay = {
        "normal": 0, "calm": 1, "gentle": 2, "quick": 0, "emphatic": 0
    }.get(delivery, 0) * expression_gain
    delivery_decay = round(delivery_decay)
    effective_speech_rate = speech_rate * delivery_rate
    ticks_per_second = bpm * 480 / 60
    mora_ticks = max(80, round(ticks_per_second / effective_speech_rate))
    lyrics = _split_lyrics(text)
    pauses = _pauses_after_lyrics(text, ticks_per_second)
    pause_scale = {"normal": 1.0, "calm": 1.05, "gentle": 1.05, "quick": 0.95, "emphatic": 1.0}.get(delivery, 1.0)
    pauses = {index: round(duration * pause_scale) for index, duration in pauses.items()}

    phrase_starts = [0]
    mora_count = 0
    for char in text.strip():
        if char in PAUSE_SECONDS:
            if mora_count and phrase_starts[-1] != mora_count:
                phrase_starts.append(mora_count)
        elif char in IGNORED:
            continue
        elif char in SMALL_KANA and mora_count:
            continue
        else:
            mora_count += 1
    phrase_starts = sorted(set(phrase_starts))
    pitch_accent_levels = _japanese_pitch_accent_levels(text, lyrics)
    if pitch_accent_levels is None:
        # Safe fallback: create a distinct low/high contour per phrase even
        # if the linguistic analyzer is unavailable or cannot align the text.
        pitch_accent_levels = [0] * len(lyrics)
        for phrase_index, phrase_start in enumerate(phrase_starts):
            phrase_end = phrase_starts[phrase_index + 1] if phrase_index + 1 < len(phrase_starts) else len(lyrics)
            phrase_length = phrase_end - phrase_start
            if phrase_length == 1:
                pitch_accent_levels[phrase_start] = 1
            else:
                for offset in range(phrase_length):
                    pitch_accent_levels[phrase_start + offset] = (
                        -1 if offset == 0 or offset == phrase_length - 1 else 1
                    )

    emphasis_indices = set()
    emphasis_text = prosody.get("emphasis_text", "")
    if emphasis_text:
        try:
            emphasis = "".join(
                char for char in _normalize_hiragana(emphasis_text)
                if char not in IGNORED
            )
        except (TypeError, ValueError):
            emphasis = ""
        lyric_text = "".join(lyrics)
        emphasis_start = lyric_text.find(emphasis) if emphasis else -1
        if emphasis_start >= 0:
            emphasis_end = emphasis_start + len(emphasis)
            char_position = 0
            for lyric_index, lyric in enumerate(lyrics):
                next_char_position = char_position + len(lyric)
                if char_position < emphasis_end and next_char_position > emphasis_start:
                    emphasis_indices.add(lyric_index)
                char_position = next_char_position

    note_blocks = []
    dynamics_xs = []
    dynamics_ys = []
    pitch_deviation_ys = []
    breath_ys = []
    tension_ys = []
    position = 0
    particles = {"は", "が", "を", "に", "へ", "と", "も", "で", "や", "の"}

    # Let emotion and delivery change the timing contrast inside each phrase,
    # while phrase normalization keeps the overall speaking rate stable.
    emotion_timing_gain = {
        "neutral": 1.0, "happy": 1.12, "curious": 1.10,
        "comforting": 1.30, "sad": 1.20, "serious": 0.78, "playful": 1.20
    }.get(emotion, 1.0)
    delivery_timing_gain = {
        "normal": 1.0, "calm": 1.12, "gentle": 1.18,
        "quick": 0.72, "emphatic": 1.25
    }.get(delivery, 1.0)
    timing_gain = emotion_timing_gain * delivery_timing_gain
    duration_weights = [1.0] * len(lyrics)
    for phrase_index, phrase_start in enumerate(phrase_starts):
        phrase_end = phrase_starts[phrase_index + 1] if phrase_index + 1 < len(phrase_starts) else len(lyrics)
        phrase_length = max(phrase_end - phrase_start, 1)
        for index in range(phrase_start, phrase_end):
            lyric = lyrics[index]
            fraction = (index - phrase_start) / phrase_length
            weight = 1.0 + 0.08 * timing_gain * math.sin(math.pi * fraction)
            if lyric == "ー":
                weight *= 1.38
            elif lyric in particles:
                weight *= {"normal": 0.82, "calm": 0.88, "gentle": 0.90,
                           "quick": 0.72, "emphatic": 0.80}.get(delivery, 0.82)
            elif lyric == "っ":
                weight *= 0.68
            elif lyric == "ん":
                weight *= 0.86
            if pitch_accent_levels[index] > 0:
                weight *= 1.0 + 0.045 * timing_gain
            if index in emphasis_indices:
                weight *= 1.0 + 0.12 * timing_gain
            if index == phrase_end - 1:
                weight *= {
                    "neutral": 1.08, "happy": 1.06, "curious": 1.10,
                    "comforting": 1.15, "sad": 1.13, "serious": 1.04, "playful": 1.07
                }.get(emotion, 1.08)
                weight *= {
                    "normal": 1.0, "calm": 1.03, "gentle": 1.07,
                    "quick": 0.96, "emphatic": 1.06
                }.get(delivery, 1.0)
            duration_weights[index] = max(0.55, min(1.75, weight))

        phrase_mean = sum(duration_weights[phrase_start:phrase_end]) / phrase_length
        for index in range(phrase_start, phrase_end):
            duration_weights[index] = max(
                0.55, min(1.75, duration_weights[index] / phrase_mean)
            )

    for index, lyric in enumerate(lyrics):
        phrase_start = max((start for start in phrase_starts if start <= index), default=0)
        next_phrase_start = next((start for start in phrase_starts if start > index), len(lyrics))
        phrase_length = max(next_phrase_start - phrase_start, 1)
        fraction = (index - phrase_start) / phrase_length
        duration_weight = duration_weights[index]
        duration = max(50, round(mora_ticks * duration_weight))
        # Use Japanese accent-phrase H/L levels so lexical words do not all
        # sit on the same note. Keep the built-in robot profile controlled.
        accent_level = pitch_accent_levels[index]
        tone = (
            64 + emotion_pitch
            + accent_level * round(expression_gain / 2)
            + (round(expression_gain / 2) if index in emphasis_indices else 0)
        )
        if index == len(lyrics) - 1:
            end_offset = round({"falling": -1, "rising": 1, "flat": 0}.get(intonation, -1) * expression_gain)
            if text.rstrip().endswith(("?", "？")):
                end_offset = max(end_offset, round(expression_gain))
            tone += end_offset

        pitch_start = round(-1 * expression_gain)
        pitch_peak = round(1 * expression_gain) + (round(1 * expression_gain) if index in emphasis_indices else 0)
        vibrato_length = 0
        dynamic = emotion_dynamic + round(3 * expression_gain * math.sin(fraction * math.pi))
        if index in emphasis_indices:
            dynamic += round(3 * expression_gain)
        if index == len(lyrics) - 1:
            dynamic -= round(2 * expression_gain)
        dynamics_xs.append(position + duration // 2)
        dynamics_ys.append(dynamic)
        pitch_deviation = accent_level * round(50 * expression_gain)
        if index in emphasis_indices:
            pitch_deviation += round(35 * expression_gain)
        pitch_deviation_ys.append(max(-1200, min(1200, pitch_deviation)))
        breath_base = max(0, min(100, emotion_breath + delivery_breath))
        breath_ys.append(max(0, min(100, breath_base + round(2 * expression_gain * math.sin(fraction * math.pi)))))
        tension_base = max(-100, min(100, emotion_tension + delivery_tension))
        tension_ys.append(max(-100, min(100, tension_base + round(2 * expression_gain * math.sin(fraction * math.pi)))))
        emphasis_velocity = round(5 * expression_gain) if index in emphasis_indices else 0
        velocity = max(0, min(200, 100 + emotion_velocity + delivery_velocity + emphasis_velocity))
        breath = max(0, min(100, emotion_breath + delivery_breath))
        attack = max(0, min(200, 100 + delivery_attack + round((5 if emotion in ("happy", "playful") else 0) * expression_gain)))
        decay = max(0, min(100, delivery_decay + round((6 if emotion in ("comforting", "sad") else 0) * expression_gain)))
        note_blocks.append(f'''  - position: {position}
    duration: {duration}
    tone: {tone}
    lyric: {_quote_yaml(lyric)}
    pitch:
      data:
      - {{x: -40, y: {pitch_start}, shape: io}}
      - {{x: -5, y: {pitch_peak}, shape: io}}
      - {{x: 40, y: 0, shape: io}}
      snap_first: true
    vibrato: {{length: {vibrato_length}, period: 175, depth: 0, in: 20, out: 30, shift: 0, drift: 0, vol_link: 0}}
    phoneme_expressions:
    - {{index: 0, abbr: atk, value: {attack}}}
    - {{index: 0, abbr: dec, value: {decay}}}
    - {{index: 0, abbr: vel, value: {velocity}}}
    - {{index: 0, abbr: bre, value: {breath}}}
    phoneme_overrides: []''')
        position += duration
        position += pauses.get(index + 1, 0)

    curve_blocks = []
    for abbr, values in (
        ("dyn", dynamics_ys),
        ("pitd", pitch_deviation_ys),
        ("brec", breath_ys),
        ("tenc", tension_ys),
    ):
        curve_blocks.append(
            f'''  - abbr: {abbr}
    xs: [{", ".join(map(str, dynamics_xs))}]
    ys: [{", ".join(map(str, values))}]'''
        )
    curves = "\n".join(curve_blocks)
    block = f'''tracks:
- singer: {_quote_yaml(singer)}
  phonemizer: {_quote_yaml(phonemizer)}
  renderer_settings:
    renderer: WORLDLINE-R
  track_name: QQ voice reply
  track_color: Blue
  mute: false
  solo: false
  volume: 0
  pan: 0
  track_expressions: []
  voice_color_names:
  - ""
voice_parts:
- duration: {position}
  name: {_quote_yaml(text.strip())}
  comment: Generated by Adachi Rei AI Bot
  track_no: 0
  position: 0
  notes:
{chr(10).join(note_blocks)}
  curves:
{curves}
'''
    return template_text[:start] + block + template_text[end:]


def _visible_windows():
    from pywinauto import Desktop, findwindows

    desktop = Desktop(backend="win32")
    for handle in findwindows.find_windows():
        try:
            window = desktop.window(handle=handle).wrapper_object()
            if window.is_visible():
                yield window
        except Exception:
            continue


def _wait_window(predicate, timeout: float, description: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for window in _visible_windows():
            try:
                if predicate(window):
                    return window
            except Exception:
                continue
        time.sleep(0.5)
    raise RuntimeError(f"等待 OpenUtau 視窗逾時：{description}")


def _choose_ustx(songs_dir: Path, song_query: str = "") -> Path:
    songs = sorted(path for path in songs_dir.glob("*.ustx") if path.is_file())
    if not songs:
        raise FileNotFoundError(f"找不到可用的 USTX 歌譜：{songs_dir}")
    query = re.sub(r"[\W_]+", "", song_query.casefold(), flags=re.UNICODE)
    if query:
        matches = [
            path for path in songs
            if query in re.sub(r"[\W_]+", "", path.stem.casefold(), flags=re.UNICODE)
            or re.sub(r"[\W_]+", "", path.stem.casefold(), flags=re.UNICODE) in query
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise FileNotFoundError(f"歌譜資料夾中沒有符合「{song_query}」的 USTX。")
        raise ValueError(f"找到多份符合「{song_query}」的歌譜，無法唯一選定。")
    if len(songs) == 1:
        return songs[0]
    raise ValueError("有多份 USTX 歌譜；請明確指定歌曲名稱。")


def _load_ustx_text(text: str, source: str = "USTX") -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "讀取 USTX 歌譜需要 PyYAML。請執行："
            "python -m pip install -r openutau_adapter/requirements.txt"
        ) from exc
    class UstxSafeLoader(yaml.SafeLoader):
        pass

    # PyYAML's YAML 1.1 resolver treats words such as "no" and "on" as
    # booleans; OpenUtau lyrics and names must remain strings.
    UstxSafeLoader.yaml_implicit_resolvers = {
        key: [entry for entry in entries if entry[0] != "tag:yaml.org,2002:bool"]
        for key, entries in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    UstxSafeLoader.add_implicit_resolver(
        "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$", re.IGNORECASE), list("tTfF")
    )
    project = yaml.load(text, Loader=UstxSafeLoader)
    if not isinstance(project, dict) or not isinstance(project.get("voice_parts"), list):
        raise ValueError(f"無法解析 USTX 專案或找不到 voice_parts：{source}")
    return project


def _load_ustx(path: Path) -> dict:
    return _load_ustx_text(path.read_text(encoding="utf-8-sig"), str(path))


def _tempo_map(project: dict) -> list[tuple[int, float]]:
    tempos = project.get("tempos") or []
    result = sorted(
        (int(item.get("position", 0)), float(item.get("bpm", project.get("bpm", 120))))
        for item in tempos
    )
    if not result or result[0][0] > 0:
        result.insert(0, (0, float(project.get("bpm", 120))))
    return result


def _tick_to_seconds(tick: int, tempos: list[tuple[int, float]], resolution: int) -> float:
    seconds = 0.0
    for index, (start_tick, bpm) in enumerate(tempos):
        if tick <= start_tick:
            break
        end_tick = tempos[index + 1][0] if index + 1 < len(tempos) else tick
        segment_end = min(tick, end_tick)
        if segment_end > start_tick:
            seconds += (segment_end - start_tick) * 60 / (resolution * bpm)
        if segment_end >= tick:
            break
    return seconds


def _ustx_phrase_candidates(project: dict) -> tuple[list[dict], list[dict], int, list[tuple[int, float]]]:
    resolution = int(project.get("resolution", 480))
    if resolution <= 0:
        raise ValueError("USTX resolution 必須大於 0。")
    tempos = _tempo_map(project)
    events = []
    for part_index, part in enumerate(project.get("voice_parts") or []):
        part_position = int(part.get("position", 0))
        for note in part.get("notes") or []:
            duration = int(note.get("duration", 0))
            if duration <= 0:
                continue
            start = part_position + int(note.get("position", 0))
            end = start + duration
            events.append({
                "part_index": part_index,
                "part_position": part_position,
                "note": note,
                "start_tick": start,
                "end_tick": end,
                "pitch": int(note.get("tone", 60)),
                "velocity": _ustx_note_velocity(note),
            })
    if not events:
        raise ValueError("USTX 歌譜沒有可演唱的音符。")
    events.sort(key=lambda item: (item["start_tick"], item["end_tick"], item["part_index"]))

    # USTX encodes rests as gaps between notes. Use the busiest voice part to
    # find phrase boundaries so sustained notes in another track do not join
    # the whole song into one oversized phrase.
    gap_limit = resolution // 2
    primary_part = max(
        range(len(project.get("voice_parts") or [])),
        key=lambda index: sum(event["part_index"] == index for event in events),
    )
    primary_events = [event for event in events if event["part_index"] == primary_part]
    phrase_groups = []
    group = []
    group_end = None
    for event in primary_events:
        if group and event["start_tick"] - group_end > gap_limit:
            phrase_groups.append(group)
            group = []
        group.append(event)
        group_end = max(group_end or event["end_tick"], event["end_tick"])
    if group:
        phrase_groups.append(group)

    phrases = []
    for primary_group in phrase_groups:
        start_tick = min(item["start_tick"] for item in primary_group)
        end_tick = max(item["end_tick"] for item in primary_group)
        group = [event for event in events
                 if event["start_tick"] < end_tick and event["end_tick"] > start_tick]
        start_seconds = _tick_to_seconds(start_tick, tempos, resolution)
        end_seconds = _tick_to_seconds(end_tick, tempos, resolution)
        duration_seconds = end_seconds - start_seconds
        if duration_seconds <= 0:
            continue
        pitches = [item["pitch"] for item in group]
        by_part = {}
        for item in group:
            by_part.setdefault(item["part_index"], []).append(item)
        intervals = []
        for part_events in by_part.values():
            part_events.sort(key=lambda item: item["start_tick"])
            intervals.extend(
                abs(right["pitch"] - left["pitch"])
                for left, right in zip(part_events, part_events[1:])
            )
        phrases.append({
            "events": group,
            "start_tick": start_tick,
            "end_tick": end_tick,
            "start": start_seconds,
            "end": end_seconds,
            "duration": duration_seconds,
            "density": len(group) / duration_seconds,
            "pitch_range": max(pitches) - min(pitches),
            "pitch_center": sum(pitches) / len(pitches),
            "movement": sum(intervals) / len(intervals) if intervals else 0,
            "velocity": sum(item["velocity"] for item in group) / len(group),
            "motif": tuple(intervals),
        })
    if not phrases:
        raise ValueError("找不到可供演唱的完整樂句。")
    return events, phrases, resolution, tempos


def _ustx_note_velocity(note: dict) -> float:
    expressions = note.get("phoneme_expressions") or []
    for expression in expressions:
        if str(expression.get("abbr", "")).casefold() == "vel":
            try:
                return float(expression.get("value", 100))
            except (TypeError, ValueError):
                break
    return 100.0


def _load_song_excerpt_history(output_dir: Path, song_name: str) -> list[dict]:
    history_path = output_dir / "song_excerpt_history.json"
    if not history_path.is_file():
        return []
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = data.get(song_name, []) if isinstance(data, dict) else []
    return [entry for entry in entries if isinstance(entry, dict)][-5:]


def _record_song_excerpt_history(
    output_dir: Path, song_name: str, start_seconds: float, end_seconds: float
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    history_path = output_dir / "song_excerpt_history.json"
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    entries = data.get(song_name, [])
    if not isinstance(entries, list):
        entries = []
    entries.append({"start": round(start_seconds, 3), "end": round(end_seconds, 3)})
    data[song_name] = entries[-5:]
    temporary_path = history_path.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(history_path)


def _adapt_ustx_for_renderer(project: dict) -> None:
    """Down-convert newer USTX metadata for the bundled OpenUtau 0.1.565 core."""
    version_text = str(project.get("ustx_version", "0.7"))
    version = tuple(int(part) for part in re.findall(r"\d+", version_text))
    if version <= (0, 7):
        return

    expressions = project.get("expressions") or {}
    unsupported = {
        key for key, expression in expressions.items()
        if str(expression.get("type", "")) == "MaskedCurve"
    }
    if not unsupported:
        project["ustx_version"] = "0.7"
        print(f"【🔄】USTX {version_text} 專案標記已轉為渲染器相容的 0.7。")
        return

    for part in project.get("voice_parts") or []:
        if part.get("masked_curves"):
            raise RuntimeError(
                f"USTX {version_text} 含有渲染器不支援的遮罩曲線資料：{part.get('name', '')}。"
            )
        referenced = {
            str(curve.get("abbr", ""))
            for curve in part.get("curves") or []
        } & unsupported
        if referenced:
            raise RuntimeError(
                f"USTX {version_text} 的樂句使用了渲染器不支援的曲線："
                f"{', '.join(sorted(referenced))}。"
            )

    for key in unsupported:
        expressions.pop(key, None)
    project["ustx_version"] = "0.7"
    print(
        f"【🔄】USTX {version_text} 專案轉為渲染器相容格式；"
        f"移除未使用的遮罩曲線定義：{', '.join(sorted(unsupported))}。"
    )


def _adapt_ustx_text_for_renderer(text: str, source: str) -> str:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "相容處理 USTX 需要 PyYAML。請執行："
            "python -m pip install -r openutau_adapter/requirements.txt"
        ) from exc
    project = _load_ustx_text(text, source)
    _adapt_ustx_for_renderer(project)
    return yaml.safe_dump(project, allow_unicode=True, sort_keys=False, width=120)


def _crop_highlight_ustx(
    source: Path,
    output_dir: Path,
    requested_duration_seconds: int = 0,
    default_duration_seconds: int = 10,
) -> tuple[Path, float, float]:
    import copy

    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "切片 USTX 歌譜需要 PyYAML。請執行："
            "python -m pip install -r openutau_adapter/requirements.txt"
        ) from exc

    project = _load_ustx(source)
    _, phrases, resolution, tempos = _ustx_phrase_candidates(project)
    feature_names = ("density", "pitch_range", "pitch_center", "movement", "velocity")
    normalized = {}
    for name in feature_names:
        values = [phrase[name] for phrase in phrases]
        low, high = min(values), max(values)
        normalized[name] = [
            0.5 if high == low else (value - low) / (high - low)
            for value in values
        ]
    motif_counts = Counter(phrase["motif"] for phrase in phrases if phrase["motif"])
    scores = []
    for index, phrase in enumerate(phrases):
        repeated = motif_counts[phrase["motif"]] > 1 if phrase["motif"] else False
        phrase["score"] = (
            normalized["density"][index] * 0.28
            + normalized["pitch_range"][index] * 0.22
            + normalized["movement"][index] * 0.18
            + normalized["pitch_center"][index] * 0.12
            + normalized["velocity"][index] * 0.10
            + (0.10 if repeated else 0.0)
        )
        scores.append(phrase["score"])
    smoothed = [
        sum(scores[max(0, i - 2):min(len(scores), i + 3)])
        / len(scores[max(0, i - 2):min(len(scores), i + 3)])
        for i in range(len(scores))
    ]
    def joined_duration(start: int, end: int) -> float:
        duration = sum(phrases[i]["duration"] for i in range(start, end + 1))
        for i in range(start, end):
            gap = phrases[i + 1]["start"] - phrases[i]["end"]
            if gap <= 1.5:
                duration += max(0.0, gap)
        return duration

    requested_target = requested_duration_seconds or default_duration_seconds
    # Build candidate excerpts across the whole song. The previous algorithm
    # expanded only from the single highest-scoring phrase, so it repeated the
    # same chorus on every request even when other good phrases existed.
    components = []
    component_start = 0
    for index in range(1, len(phrases)):
        if phrases[index]["start"] - phrases[index - 1]["end"] > 1.5:
            components.append((component_start, index - 1))
            component_start = index
    components.append((component_start, len(phrases) - 1))

    candidates = []
    for component_first, component_last in components:
        component_duration = joined_duration(component_first, component_last)
        target_duration = min(float(requested_target), component_duration)
        for window_start in range(component_first, component_last + 1):
            for window_end in range(window_start, component_last + 1):
                duration = joined_duration(window_start, window_end)
                if duration >= target_duration:
                    phrase_window = phrases[window_start:window_end + 1]
                    weighted_score = sum(
                        phrase["score"] * phrase["duration"] for phrase in phrase_window
                    ) / max(sum(phrase["duration"] for phrase in phrase_window), 0.1)
                    peak_score = max(smoothed[window_start:window_end + 1])
                    candidates.append({
                        "first": window_start,
                        "last": window_end,
                        "duration": duration,
                        "score": weighted_score * 0.75 + peak_score * 0.25,
                        "start": phrase_window[0]["start"],
                        "end": phrase_window[-1]["end"],
                    })
                    break
    if not candidates:
        raise ValueError("找不到符合預設時長的可用歌曲片段。")

    # Remove near-duplicate windows before random selection. This keeps
    # variation musically plausible while allowing strong areas throughout the
    # song, instead of forcing every request to the global maximum.
    candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    diverse_candidates = []
    for candidate in candidates:
        overlaps_existing = False
        for selected_candidate in diverse_candidates:
            overlap = max(
                0.0,
                min(candidate["end"], selected_candidate["end"])
                - max(candidate["start"], selected_candidate["start"]),
            )
            shorter = min(
                candidate["end"] - candidate["start"],
                selected_candidate["end"] - selected_candidate["start"],
            )
            if shorter > 0 and overlap / shorter >= 0.35:
                overlaps_existing = True
                break
        if not overlaps_existing:
            diverse_candidates.append(candidate)

    history = _load_song_excerpt_history(output_dir, source.stem)
    recent_ranges = [
        (float(entry.get("start", 0)), float(entry.get("end", 0)))
        for entry in history
    ]

    def repeat_overlap(candidate: dict) -> float:
        candidate_duration = max(candidate["end"] - candidate["start"], 0.1)
        return max((
            max(0.0, min(candidate["end"], old_end) - max(candidate["start"], old_start))
            / min(candidate_duration, max(old_end - old_start, 0.1))
            for old_start, old_end in recent_ranges
        ), default=0.0)

    fresh_candidates = [candidate for candidate in diverse_candidates
                        if repeat_overlap(candidate) < 0.35]
    if fresh_candidates:
        diverse_candidates = fresh_candidates
    else:
        # If the repertoire has too few distinct regions, relax repeat
        # avoidance gradually instead of failing to generate audio.
        diverse_candidates.sort(
            key=lambda candidate: (repeat_overlap(candidate), -candidate["score"])
        )

    best_score = diverse_candidates[0]["score"]
    shortlist = [candidate for candidate in diverse_candidates
                 if candidate["score"] >= best_score - max(0.10, best_score * 0.18)][:6]
    chosen = random.SystemRandom().choice(shortlist)
    chosen_first, chosen_last = chosen["first"], chosen["last"]
    selected_phrases = phrases[chosen_first:chosen_last + 1]
    start_tick = selected_phrases[0]["start_tick"]
    end_tick = selected_phrases[-1]["end_tick"]
    start_seconds = _tick_to_seconds(start_tick, tempos, resolution)
    end_seconds = _tick_to_seconds(end_tick, tempos, resolution)

    cropped = copy.deepcopy(project)
    _adapt_ustx_for_renderer(cropped)
    cropped_voice_parts = []
    # Match selected notes by their source track and absolute tick range; YAML
    # reloads notes as dictionaries, so do not depend on object identity here.
    selected_ranges = {}
    for phrase in selected_phrases:
        for event in phrase["events"]:
            selected_ranges.setdefault(event["part_index"], set()).add(
                (event["start_tick"], event["end_tick"])
            )
    for part_index, part in enumerate(cropped.get("voice_parts") or []):
        original_position = int(part.get("position", 0))
        kept_notes = []
        for note in part.get("notes") or []:
            absolute_start = original_position + int(note.get("position", 0))
            absolute_end = absolute_start + int(note.get("duration", 0))
            if (absolute_start, absolute_end) not in selected_ranges.get(part_index, set()):
                continue
            note["position"] = absolute_start - start_tick
            kept_notes.append(note)
        if not kept_notes:
            continue
        part["position"] = 0
        part["notes"] = kept_notes
        part["duration"] = max(
            int(note["position"]) + int(note["duration"]) for note in kept_notes
        )
        for curve in part.get("curves") or []:
            data = curve.get("data") or []
            curve["data"] = [
                {**point, "x": int(point["x"]) + original_position - start_tick}
                for point in data
                if start_tick <= int(point.get("x", 0)) + original_position <= end_tick
            ]
        cropped_voice_parts.append(part)
    if not cropped_voice_parts:
        raise ValueError("依照選段範圍沒有找到可輸出的音符。")
    cropped["voice_parts"] = cropped_voice_parts

    # Shift the tempo map so the selected excerpt starts at project tick zero.
    tempo_before_start = [item for item in (project.get("tempos") or [])
                          if int(item.get("position", 0)) <= start_tick]
    baseline_tempo = max(tempo_before_start,
                         key=lambda item: int(item.get("position", 0))) if tempo_before_start else {
                             "position": 0, "bpm": tempos[0][1]
                         }
    cropped["tempos"] = [{"position": 0, "bpm": float(baseline_tempo["bpm"])}]
    for item in project.get("tempos") or []:
        position = int(item.get("position", 0))
        if start_tick < position < end_tick:
            cropped["tempos"].append({**item, "position": position - start_tick})
    cropped["bpm"] = float(baseline_tempo["bpm"])

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_path = output_dir / f"song_excerpt_{timestamp}.ustx"
    output_path.write_text(
        yaml.safe_dump(cropped, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    print(
        f"【🎼】從 {len(diverse_candidates)} 個分散的高分候選區中選段，"
        f"片段 {start_seconds:.2f}–{end_seconds:.2f} 秒（共 {end_seconds - start_seconds:.2f} 秒）；"
        f"目標 {requested_target} 秒，保留完整樂句並略過長空白。"
    )
    return output_path, start_seconds, end_seconds


def _crop_highlight_ust(
    source: Path,
    output_dir: Path,
    minimum_duration_seconds: int = 0,
) -> tuple[Path, float, float]:
    raw = source.read_bytes()
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "shift_jis"
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        encoding = "gbk"
        text = raw.decode(encoding)

    sections = []
    current_header = None
    current_lines = []
    for line in text.splitlines(keepends=True):
        match = re.match(r"^\[#(\d{4}|SETTING|TRACKEND)\]\s*$", line.rstrip("\r\n"))
        if match:
            if current_header is not None:
                sections.append((current_header, "".join(current_lines)))
            current_header = match.group(1)
            current_lines = [line]
        elif current_header is not None:
            current_lines.append(line)
    if current_header is not None:
        sections.append((current_header, "".join(current_lines)))

    setting = next((block for key, block in sections if key == "SETTING"), None)
    if setting is None:
        raise ValueError(f"UST 缺少設定區：{source}")
    settings = dict(re.findall(r"(?m)^([^=\r\n]+)=(.*)$", setting))
    base_tempo = float(settings.get("Tempo", "120"))
    notes = []
    for key, block in sections:
        if not key.isdigit():
            continue
        fields = dict(re.findall(r"(?m)^([^=\r\n]+)=(.*)$", block))
        if "Length" in fields and "NoteNum" in fields:
            notes.append({
                "block": block,
                "fields": fields,
                "length": int(fields["Length"]),
                "pitch": int(fields["NoteNum"]),
                "velocity": float(fields.get("Velocity") or 100),
            })
    if not notes:
        raise ValueError(f"UST 中沒有音符：{source}")

    seconds = 0.0
    current_tempo = base_tempo
    for note in notes:
        if note["fields"].get("Tempo"):
            current_tempo = float(note["fields"]["Tempo"])
        note["start_seconds"] = seconds
        note["tempo_at_start"] = current_tempo
        seconds += note["length"] / (480 * current_tempo / 60)

    # Treat rests of at least half a beat as phrase boundaries.
    phrase_break_ticks = 240
    minimum_phrase_seconds = 0.35
    phrases = []
    phrase_start = None
    for index, note in enumerate(notes):
        is_rest = note["fields"].get("Lyric", "").strip().casefold() == "r"
        if not is_rest and phrase_start is None:
            phrase_start = index
        elif is_rest and phrase_start is not None and note["length"] >= phrase_break_ticks:
            phrase_end = index
            if phrase_end > phrase_start:
                phrases.append((phrase_start, phrase_end))
            phrase_start = None
    if phrase_start is not None:
        phrase_end = len(notes)
        while phrase_end > phrase_start and notes[phrase_end - 1]["fields"].get("Lyric", "").strip().casefold() == "r":
            phrase_end -= 1
        if phrase_end > phrase_start:
            phrases.append((phrase_start, phrase_end))

    if not phrases:
        raise ValueError(f"找不到由休止符分隔的可演唱樂句：{source.name}")

    phrase_times = []
    for start_index, end_index in phrases:
        start_time = notes[start_index]["start_seconds"]
        last_note = notes[end_index - 1]
        end_time = last_note["start_seconds"] + last_note["length"] / (
            480 * last_note["tempo_at_start"] / 60
        )
        phrase_notes = [note for note in notes[start_index:end_index]
                        if note["fields"].get("Lyric", "").strip().casefold() != "r"]
        if not phrase_notes or end_time - start_time < minimum_phrase_seconds:
            continue
        pitches = [note["pitch"] for note in phrase_notes]
        intervals = [abs(right - left) for left, right in zip(pitches, pitches[1:])]
        phrase_times.append({
            "start_index": start_index,
            "end_index": end_index,
            "start": start_time,
            "end": end_time,
            "duration": end_time - start_time,
            "density": len(phrase_notes) / max(end_time - start_time, 0.1),
            "pitch_range": max(pitches) - min(pitches),
            "pitch_center": sum(pitches) / len(pitches),
            "movement": sum(intervals) / len(intervals) if intervals else 0,
            "velocity": sum(note["velocity"] for note in phrase_notes) / len(phrase_notes),
            "motif": tuple(intervals),
        })

    if not phrase_times:
        raise ValueError(f"找不到可供演唱的完整樂句：{source.name}")

    # UST has no chorus labels, so approximate the highlight from musical
    # activity: note density, pitch range/height, melodic movement, dynamics,
    # and recurring interval patterns. Smooth neighboring phrases to avoid
    # treating one isolated high note as the entire highlight.
    feature_names = ("density", "pitch_range", "pitch_center", "movement", "velocity")
    normalized = {name: [] for name in feature_names}
    for name in feature_names:
        values = [phrase[name] for phrase in phrase_times]
        low, high = min(values), max(values)
        normalized[name] = [
            0.5 if high == low else (value - low) / (high - low)
            for value in values
        ]
    motif_counts = Counter(phrase["motif"] for phrase in phrase_times if phrase["motif"])
    scores = []
    for index, phrase in enumerate(phrase_times):
        repeated = motif_counts[phrase["motif"]] > 1 if phrase["motif"] else False
        phrase["score"] = (
            normalized["density"][index] * 0.28
            + normalized["pitch_range"][index] * 0.22
            + normalized["movement"][index] * 0.18
            + normalized["pitch_center"][index] * 0.12
            + normalized["velocity"][index] * 0.10
            + (0.10 if repeated else 0.0)
        )
        scores.append(phrase["score"])
    smoothed_scores = [
        sum(scores[max(0, index - 2):min(len(scores), index + 3)])
        / len(scores[max(0, index - 2):min(len(scores), index + 3)])
        for index in range(len(scores))
    ]
    peak_index = max(range(len(smoothed_scores)), key=smoothed_scores.__getitem__)
    highlight_threshold = max(0.40, smoothed_scores[peak_index] * 0.72)
    highlight_start = highlight_end = peak_index
    while highlight_start > 0:
        gap = phrase_times[highlight_start]["start"] - phrase_times[highlight_start - 1]["end"]
        if gap > 2.0 or smoothed_scores[highlight_start - 1] < highlight_threshold:
            break
        highlight_start -= 1
    while highlight_end + 1 < len(phrase_times):
        gap = phrase_times[highlight_end + 1]["start"] - phrase_times[highlight_end]["end"]
        if gap > 2.0 or smoothed_scores[highlight_end + 1] < highlight_threshold:
            break
        highlight_end += 1
    def joined_duration(first: int, last: int) -> float:
        duration = sum(phrase_times[index]["duration"] for index in range(first, last + 1))
        for index in range(first, last):
            gap = phrase_times[index + 1]["start"] - phrase_times[index]["end"]
            if gap <= 1.5:
                duration += max(0.0, gap)
        return duration

    highlight_duration = joined_duration(highlight_start, highlight_end)
    requested_target = minimum_duration_seconds + 0.5 if minimum_duration_seconds else highlight_duration
    target_duration = min(requested_target, highlight_duration)
    windows = []
    for first in range(highlight_start, highlight_end + 1):
        for last in range(first, highlight_end + 1):
            duration = joined_duration(first, last)
            if duration >= target_duration:
                weighted_score = sum(
                    phrase_times[index]["score"] * phrase_times[index]["duration"]
                    for index in range(first, last + 1)
                ) / max(sum(phrase_times[index]["duration"] for index in range(first, last + 1)), 0.1)
                center_distance = abs((first + last) / 2 - peak_index)
                windows.append((weighted_score, -abs(duration - target_duration), -center_distance, first, last))
                break
    if windows:
        _, _, _, chosen_first, chosen_last = max(windows)
    else:
        chosen_first, chosen_last = highlight_start, highlight_end

    chosen_phrases = phrase_times[chosen_first:chosen_last + 1]
    start_time = chosen_phrases[0]["start"]
    end_time = chosen_phrases[-1]["end"]
    selected_with_phrase_starts = []
    for offset, phrase in enumerate(chosen_phrases):
        if offset:
            previous = chosen_phrases[offset - 1]
            gap = phrase["start"] - previous["end"]
            if gap <= 1.5:
                selected_with_phrase_starts.extend(
                    (note, False)
                    for note in notes[previous["end_index"]:phrase["start_index"]]
                )
        selected_with_phrase_starts.extend(
            (note, index == phrase["start_index"])
            for index, note in enumerate(notes[phrase["start_index"]:phrase["end_index"]],
                                          start=phrase["start_index"])
        )
    selected = [note for note, _ in selected_with_phrase_starts]
    setting = re.sub(
        r"(?m)^Tempo=.*$",
        f"Tempo={selected[0]['tempo_at_start']:.6f}",
        setting,
        count=1
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir/f"song_excerpt_{datetime.now():%Y%m%d_%H%M%S}.ust"
    note_blocks = []
    for index, (note, is_phrase_start) in enumerate(selected_with_phrase_starts):
        block = note["block"]
        if is_phrase_start:
            tempo_line = f"Tempo={note['tempo_at_start']:.6f}"
            if re.search(r"(?m)^Tempo=.*$", block):
                block = re.sub(r"(?m)^Tempo=.*$", tempo_line, block, count=1)
            else:
                block = re.sub(r"^(\[#\d{4}\]\r?\n)", rf"\1{tempo_line}\r\n", block, count=1)
        note_blocks.append(
            re.sub(r"(?m)^\[#\d{4}\]", f"[#{index:04d}]", block, count=1).rstrip("\r\n") + "\r\n"
        )
    excerpt = setting.rstrip("\r\n") + "\r\n" + "".join(note_blocks) + "[#TRACKEND]\r\n"
    output_path.write_bytes(excerpt.encode(encoding))
    print(
        f"【🎼】精華候選約 {highlight_duration:.2f} 秒，"
        f"選段約 {joined_duration(chosen_first, chosen_last):.2f} 秒；"
        f"略過超過 1.5 秒的段落空白，需求下限 {minimum_duration_seconds} 秒。"
    )
    return output_path, start_time, end_time


def _save_unsaved_project(main, output_dir: Path) -> None:
    from pywinauto import Desktop

    if "*" not in main.window_text():
        return

    print("【💾】偵測到 OpenUtau 專案尚未儲存，正在先儲存……")
    main.set_focus()
    main.type_keys("^s")

    deadline = time.monotonic() + 20
    save_dialog = None
    while time.monotonic() < deadline:
        if "*" not in main.window_text():
            print("【✅】OpenUtau 專案已儲存。")
            return
        for window in _visible_windows():
            try:
                title = window.window_text().casefold()
                if (
                    window.is_visible()
                    and window.process_id() == main.process_id()
                    and window.class_name() == "#32770"
                    and ("save" in title or "儲存" in title)
                ):
                    save_dialog = window
                    break
            except Exception:
                continue
        if save_dialog:
            break
        time.sleep(0.3)

    if save_dialog:
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sequence = 1
        while True:
            recovery_path = output_dir/f"unsaved_project_recovery_{timestamp}_{sequence:03}.ustx"
            if not recovery_path.exists():
                break
            sequence += 1

        dialog = Desktop(backend="win32").window(handle=save_dialog.handle)
        try:
            dialog.child_window(class_name="Edit", found_index=0).set_edit_text(str(recovery_path))
            save_button = next(
                (name for name in ("&Save", "儲存", "Save")
                 if dialog.child_window(title=name, class_name="Button").exists()),
                None
            )
            if not save_button:
                raise RuntimeError("找不到另存新檔對話框的儲存按鈕。")
            dialog.child_window(title=save_button, class_name="Button").click_input()
        except Exception as exc:
            raise RuntimeError(f"無法安全地另存未命名專案：{exc}") from exc

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if "*" not in main.window_text():
            print("【✅】OpenUtau 專案已儲存。")
            return
        time.sleep(0.3)

    raise RuntimeError("OpenUtau 專案仍顯示未儲存；為避免遺失修改，已取消切換與語音匯出。")


def _render_wav(project_path: Path, renderer_exe: Path) -> Path:
    if not renderer_exe.is_file():
        raise FileNotFoundError(
            f"找不到 OpenUtau C# 渲染器：{renderer_exe}。請先執行："
            "dotnet build openutau_adapter/renderer_host/OpenUtauRenderer.csproj"
        )
    project_path = project_path.resolve()
    wav_path = project_path.with_suffix(".wav")
    if not project_path.is_file():
        raise FileNotFoundError(f"找不到待渲染的 USTX 專案：{project_path}")

    try:
        result = subprocess.run(
            [str(renderer_exe), str(project_path), str(wav_path)],
            cwd=str(renderer_exe.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"OpenUtau C# 渲染逾時：{project_path}") from exc
    except OSError as exc:
        raise RuntimeError(f"無法啟動 OpenUtau C# 渲染器：{exc}") from exc

    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        if wav_path.is_file() and wav_path.stat().st_size <= 44:
            wav_path.unlink()
        raise RuntimeError(
            f"OpenUtau C# 渲染失敗（結束碼 {result.returncode}）：{details or '沒有錯誤詳情'}"
        )
    if not wav_path.is_file() or wav_path.stat().st_size <= 44:
        raise RuntimeError(f"渲染器沒有產生有效的 WAV：{wav_path}")

    print(f"【✅】C# 渲染完成：{wav_path}（{wav_path.stat().st_size:,} bytes）")
    return wav_path


def _import_ust_and_save_ustx(ust_path: Path, ustx_path: Path, openutau_exe: Path, singer: str) -> Path:
    """Import a legacy UST, choose the configured singer, and save it as USTX."""
    from pywinauto import Desktop

    find_main = lambda window: "OpenUtau" in window.window_text() and window.class_name() != "#32770"
    main_windows = [window for window in _visible_windows() if find_main(window)]
    main = main_windows[0] if main_windows else None
    if main is None:
        subprocess.Popen([str(openutau_exe)], cwd=str(openutau_exe.parent))
        main = _wait_window(find_main, 90, "OpenUtau 主視窗")
    for window in main_windows:
        _save_unsaved_project(window, ustx_path.parent)

    main.set_focus()
    main.type_keys("^o")
    dialog_window = _wait_window(
        lambda window: window.class_name() == "#32770" and window.process_id() == main.process_id(),
        15, "開啟 UST 對話框"
    )
    dialog = Desktop(backend="win32").window(handle=dialog_window.handle)
    dialog.child_window(class_name="Edit", found_index=0).set_edit_text(str(ust_path.resolve()))
    open_button = next((name for name in ("&Open", "開啟", "Open")
                        if dialog.child_window(title=name, class_name="Button").exists()), None)
    if not open_button:
        raise RuntimeError("找不到開啟 UST 對話框的 Open 按鈕。")
    dialog.child_window(title=open_button, class_name="Button").click_input()
    time.sleep(3)

    ui = Desktop(backend="uia").window(handle=main.handle)
    singer_buttons = [b for b in ui.descendants(control_type="Button")
                      if any(mark in b.window_text().casefold() for mark in ("missing", "singer", "歌手"))]
    if not singer_buttons:
        raise RuntimeError("找不到 UST 軌道的歌手選擇按鈕。")
    singer_buttons[0].click_input()
    time.sleep(0.5)
    singer_item = next((item for item in ui.descendants(control_type="MenuItem")
                        if item.window_text().strip() == singer), None)
    if singer_item is None:
        raise RuntimeError(f"OpenUtau 歌手清單中找不到設定的音源：{singer}")
    singer_item.click_input()
    time.sleep(1)

    file_menu = next((name for name in ("檔案", "File")
                      if ui.child_window(title=name, control_type="MenuItem").exists()), None)
    if not file_menu:
        raise RuntimeError("找不到 OpenUtau 的 File/檔案選單。")
    ui.child_window(title=file_menu, control_type="MenuItem").click_input()
    time.sleep(0.3)
    save_as = next((name for name in ("另存新檔", "Save As")
                    if ui.child_window(title=name, control_type="MenuItem").exists()), None)
    if not save_as:
        raise RuntimeError("找不到 OpenUtau 的另存新檔選單項目。")
    ui.child_window(title=save_as, control_type="MenuItem").click_input()
    save_window = _wait_window(
        lambda window: window.class_name() == "#32770" and window.process_id() == main.process_id(),
        15, "另存 USTX 對話框"
    )
    save_dialog = Desktop(backend="win32").window(handle=save_window.handle)
    save_dialog.child_window(class_name="Edit", found_index=0).set_edit_text(str(ustx_path.resolve()))
    save_button = next((name for name in ("&Save", "儲存", "Save")
                        if save_dialog.child_window(title=name, class_name="Button").exists()), None)
    if not save_button:
        raise RuntimeError("找不到另存 USTX 對話框的 Save 按鈕。")
    save_dialog.child_window(title=save_button, class_name="Button").click_input()
    _wait_window(lambda window: find_main(window) and ustx_path.stem.casefold() in window.window_text().casefold(),
                 30, "已儲存的 USTX 專案")
    return ustx_path


def _generate_song_excerpt(
    song_query: str = "",
    minimum_duration_seconds: int = 0,
) -> tuple[Path, str]:
    configs = load_configs()
    if not configs.get("enabled", False):
        raise RuntimeError("OpenUtau 語音功能目前已在 openutau_configs.json 關閉。")
    output_dir = _resolve_path(configs["output_dir"])
    songs_dir = Path(__file__).resolve().parent / "songs"
    source = _choose_ustx(songs_dir, song_query)
    excerpt_path, start_time, end_time = _crop_highlight_ustx(
        source,
        output_dir,
        requested_duration_seconds=minimum_duration_seconds,
        default_duration_seconds=max(1, int(configs.get("song_default_duration_seconds", 10))),
    )
    print(f"【🎵】選用歌譜：{source.name}；原曲位置：{start_time:.2f}–{end_time:.2f} 秒")
    renderer_exe = _resolve_path(configs["renderer_exe"])
    audio_path = _render_wav(excerpt_path, renderer_exe)
    _record_song_excerpt_history(output_dir, source.stem, start_time, end_time)
    return audio_path, source.stem


def _generate_audio(text: str, prosody: dict | None = None) -> Path:
    configs = load_configs()
    if not configs.get("enabled", False):
        raise RuntimeError("OpenUtau 語音功能目前已在 openutau_configs.json 關閉。")

    text = _normalize_hiragana(text)
    template_path = _resolve_path(configs["template_path"])
    renderer_exe = _resolve_path(configs["renderer_exe"])
    output_dir = _resolve_path(configs["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    if not template_path.is_file():
        raise FileNotFoundError(f"找不到 OpenUtau 範本：{template_path}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sequence = 1
    while True:
        project_path = output_dir/f"qq_voice_{timestamp}_{sequence:03}.ustx"
        if not project_path.exists() and not project_path.with_suffix(".wav").exists():
            break
        sequence += 1

    template_text = _adapt_ustx_text_for_renderer(
        template_path.read_text(encoding="utf-8-sig"), str(template_path)
    )
    project_text = _make_project(
        template_text=template_text,
        singer=configs["singer"],
        phonemizer=configs["phonemizer"],
        text=text,
        speech_rate=float(configs.get("speech_rate", 6.5)),
        prosody=prosody
    )
    print("【🎚】說話 Pitch 使用 OpenJTalk 日文音調分析與內建曲線；未套用 AI Auto Pitch。")
    project_path.write_text(project_text, encoding="utf-8")
    print(f"【🎙】OpenUtau 專案已建立：{project_path}")

    return _render_wav(project_path, renderer_exe)


async def generate_openutau_audio(text: str, prosody: dict | None = None) -> Path:
    return await asyncio.to_thread(_generate_audio, text, prosody)


async def generate_openutau_song_excerpt(
    song_query: str = "",
    minimum_duration_seconds: int = 0,
) -> tuple[Path, str]:
    return await asyncio.to_thread(
        _generate_song_excerpt, song_query, minimum_duration_seconds
    )
