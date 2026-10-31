"""Slash-command handlers for the official QQ bot."""
from __future__ import annotations

from datetime import datetime

import data.qq_repository as qq_db
from openutau_adapter.openutau_bot import get_available_song_titles
from qq_adapter.song_intent import format_song_catalog
from .base import OwariOfficialBotBase
from .message_utils import _get_message_value, _get_sender_name
from .scheduler import next_scheduled_job
from .settings import (
    MODEL_SWITCH_COMMANDS,
    get_ollama_response_model as _get_ollama_response_model,
    set_ollama_response_model as _set_ollama_response_model,
)

# The first prefix is current; the rest are backward-compatible aliases.
IDENTITY_SET_PREFIXES = ("/identity_set", "/set_identity")
IDENTITY_CLEAN_COMMANDS = {"/identity_clean", "/forget_identity"}
MODEL_SHOW_COMMANDS = {"/model_show", "/model"}
CHAT_CLEAN_COMMANDS = {
    "/forget", "forget", "/clean_message", "clean_message",
    "/clean_memory", "clean_memory", "/chat_clean", "chat_clean",
}
SONG_SHOW_COMMANDS = {"/songs", "songs", "/song_show", "song_show"}
STATUS_COMMANDS = {"/status", "/bot_status"}


def _format_uptime(seconds: float) -> str:
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    return f"{minutes} 分钟"


def _match_command_prefix(command: str, prefixes: tuple[str, ...]) -> str | None:
    """Return the prefix when the command is exactly it or it plus whitespace."""
    for prefix in prefixes:
        if command == prefix or (
            command.startswith(prefix) and command[len(prefix):len(prefix) + 1].isspace()
        ):
            return prefix
    return None


class OwariOfficialBotCommands(OwariOfficialBotBase):
    async def handle_command(
        self,
        event,
        group_openid: str,
        message_id: str,
        user_text: str,
        command: str,
    ) -> bool:
        user_id = str(_get_message_value(event, "user_id", "unknown"))

        if command in MODEL_SHOW_COMMANDS:
            try:
                current_model = _get_ollama_response_model()
                response = (
                    f"当前使用的 Ollama 模型：{current_model}。"
                    if current_model
                    else "当前尚未设置 Ollama 对话模型。"
                )
                print(f"【🧠】{command}：{current_model or '未設定'}")
            except Exception as exc:
                print(f"【❌】讀取目前 Ollama 模型失敗：{exc}")
                response = "暂时无法读取当前模型，请稍后再试。"
            await self._reply(group_openid, message_id, response, "目前模型回覆")
            return True

        identity_command_prefix = _match_command_prefix(command, IDENTITY_SET_PREFIXES)
        if identity_command_prefix:
            identity = user_text.strip()[len(identity_command_prefix):].strip()
            if not identity:
                response = (
                    f"请在 {identity_command_prefix} 后输入你的身份介绍，"
                    f"最多 {qq_db.MAX_USER_IDENTITY_LENGTH} 个字。"
                )
            elif len(identity) > qq_db.MAX_USER_IDENTITY_LENGTH:
                response = (
                    f"身份介绍有 {len(identity)} 个字，超过 "
                    f"{qq_db.MAX_USER_IDENTITY_LENGTH} 字上限，请缩短后重试。"
                )
            else:
                try:
                    qq_db.set_user_identity(group_openid, user_id, identity)
                    response = "已记下你的身份设定。"
                except Exception as exc:
                    print(f"【❌】儲存 QQ 使用者身份失敗：{exc}")
                    response = "身份设定暂时保存失败，请稍后再试。"
            if await self._reply(group_openid, message_id, response, "身份設定結果"):
                print(
                    f"【🪪】{identity_command_prefix}：群 {group_openid}、使用者 {user_id}，"
                    f"輸入 {len(identity)} 字"
                )
            return True

        if command in IDENTITY_CLEAN_COMMANDS:
            try:
                removed = qq_db.clear_user_identity(group_openid, user_id)
                response = (
                    "已清除你在本群设置的身份。"
                    if removed
                    else "本群还没有保存你的身份设定。"
                )
                print(
                    f"【🪪】/identity_clean：群 {group_openid}、使用者 {user_id}，"
                    f"已清除 {removed}"
                )
            except Exception as exc:
                print(f"【❌】清除 QQ 使用者身份失敗：{exc}")
                response = "身份设定暂时无法清除，请稍后再试。"
            await self._reply(group_openid, message_id, response, "身份清除結果")
            return True

        if command == "/identity_show":
            try:
                identity = qq_db.get_user_identity(group_openid, user_id)
                response = (
                    f"热音终记得你在本群设置的身份：\n{identity}"
                    if identity
                    else "热音终还没有记录你在本群设置的身份。"
                )
                print(
                    f"【🪪】/identity_show：群 {group_openid}、使用者 {user_id}，"
                    f"命中 {bool(identity)}"
                )
            except Exception as exc:
                print(f"【❌】讀取 QQ 使用者身份失敗：{exc}")
                response = "暂时无法读取你的身份设定，请稍后再试。"
            await self._reply(group_openid, message_id, response, "身份查詢結果")
            return True

        if command in MODEL_SWITCH_COMMANDS:
            selected_model = MODEL_SWITCH_COMMANDS[command]
            try:
                previous_model = _set_ollama_response_model(selected_model)
                response = (
                    f"已将全局 Ollama 模型切换为 {selected_model}。"
                    if previous_model != selected_model
                    else f"当前全局 Ollama 模型已经是 {selected_model}。"
                )
                print(
                    f"【🧠】收到模型切換指令，已切換全局 Ollama 模型："
                    f"{previous_model or '未設定'} -> {selected_model}"
                )
            except Exception as exc:
                print(f"【❌】切換 Ollama 模型失敗：{exc}")
                response = "模型切换失败，请稍后再试。"
            await self._reply(group_openid, message_id, response, "模型切換結果")
            return True

        if command in CHAT_CLEAN_COMMANDS:
            removed_count = qq_db.clear_short_memory(target_id=group_openid)
            print(f"【🧹】已清除此群短期記憶 {removed_count} 則。")
            await self._reply(group_openid, message_id, "已清除本群的聊天记忆。", "記憶清除完成訊息")
            return True

        if command in SONG_SHOW_COMMANDS:
            song_titles = get_available_song_titles()
            response = format_song_catalog(song_titles)
            if await self._reply(group_openid, message_id, response, "/songs 曲目清單"):
                # Stored so a follow-up number or title can select from the list.
                qq_db.insert_short_memory(
                    ai_provider="ollama",
                    target_id=group_openid,
                    user_id=user_id,
                    user_name=_get_sender_name(event),
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
            return True

        if command in STATUS_COMMANDS:
            now = datetime.now()
            try:
                model = _get_ollama_response_model() or "未设置"
            except Exception as exc:
                print(f"【⚠】/status 讀取模型失敗：{exc}")
                model = "读取失败"
            upcoming = next_scheduled_job(now)
            next_job = (
                f"{upcoming[0]:%m-%d %H:%M}「{upcoming[1]['text'] or '图片'}」"
                if upcoming else "无"
            )
            response = "\n".join([
                "热音终运行状态：",
                f"- 已运行：{_format_uptime((now - self.started_at).total_seconds())}",
                f"- 当前模型：{model}",
                f"- 本次启动后已回复：{self.replies_sent} 则",
                f"- 本群短期记忆：{len(qq_db.get_short_memory(group_openid, 1000))} 则",
                f"- 处理状态：{'处理中' if self._active_group_openid else '空闲'}",
                f"- 下一则排程：{next_job}",
            ])
            await self._reply(group_openid, message_id, response, "/status 狀態")
            print("【📊】/status 已回報運行狀態。")
            return True

        return False
