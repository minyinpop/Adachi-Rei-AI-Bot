import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SHORT_MEMORY_PATH = Path(__file__).parent / "qq_short_memory.json"
LONG_MEMORY_DB_PATH = Path(__file__).parent / "qq_long_memory.db"
MAX_SHORT_MEMORY_MESSAGES_PER_TARGET = 100
MAX_USER_IDENTITY_LENGTH = 300


def init_long_memory() -> None:
    """Create persistent QQ long-memory storage without deleting existing data."""
    LONG_MEMORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(LONG_MEMORY_DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_identities (
                group_openid TEXT NOT NULL,
                user_id TEXT NOT NULL,
                identity TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_openid, user_id)
            )
            """
        )


def set_user_identity(
    group_openid: str,
    user_id: str,
    identity: str,
) -> None:
    identity = identity.strip()
    if not identity:
        raise ValueError("身份內容不可為空。")
    if len(identity) > MAX_USER_IDENTITY_LENGTH:
        raise ValueError(
            f"身份內容不可超過 {MAX_USER_IDENTITY_LENGTH} 個字元。"
        )
    with sqlite3.connect(LONG_MEMORY_DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO user_identities (group_openid, user_id, identity, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(group_openid, user_id) DO UPDATE SET
                identity = excluded.identity,
                updated_at = excluded.updated_at
            """,
            (
                str(group_openid),
                str(user_id),
                identity,
                datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            ),
        )


def get_user_identity(group_openid: str, user_id: str) -> str:
    with sqlite3.connect(LONG_MEMORY_DB_PATH) as connection:
        row = connection.execute(
            """
            SELECT identity FROM user_identities
            WHERE group_openid = ? AND user_id = ?
            """,
            (str(group_openid), str(user_id)),
        ).fetchone()
    return str(row[0]) if row else ""


def clear_user_identity(group_openid: str, user_id: str) -> bool:
    """Remove one user's identity for one QQ group; preserve other groups."""
    with sqlite3.connect(LONG_MEMORY_DB_PATH) as connection:
        cursor = connection.execute(
            "DELETE FROM user_identities WHERE group_openid = ? AND user_id = ?",
            (str(group_openid), str(user_id)),
        )
    return cursor.rowcount > 0


def _read_short_memory() -> list[dict]:
    if not SHORT_MEMORY_PATH.exists():
        return []

    with open(SHORT_MEMORY_PATH, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"QQ 短期記憶 JSON 格式錯誤：{SHORT_MEMORY_PATH}")
    return data


def _write_short_memory(messages: list[dict]) -> None:
    temporary_path = SHORT_MEMORY_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(messages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(SHORT_MEMORY_PATH)


def init_short_memory() -> None:
    SHORT_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_short_memory([])


def insert_short_memory(
    ai_provider: str,
    target_id: int | str,
    user_id: int | str,
    user_name: str,
    user_role: str,
    user_message_type: str,
    user_message: str,
) -> None:
    messages = _read_short_memory()
    messages.append({
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "ai_provider": ai_provider,
        "target_id": target_id,
        "user_id": user_id,
        "user_name": user_name,
        "user_role": user_role,
        "user_message_type": user_message_type,
        "user_message": user_message,
    })
    # Keep each chat's own rolling window, so activity in a busy group cannot
    # evict another group's short-term context.
    target_indices = [
        index
        for index, message in enumerate(messages)
        if str(message.get("target_id")) == str(target_id)
    ]
    indices_to_remove = set(
        target_indices[:-MAX_SHORT_MEMORY_MESSAGES_PER_TARGET]
    )
    if indices_to_remove:
        messages = [
            message
            for index, message in enumerate(messages)
            if index not in indices_to_remove
        ]
    _write_short_memory(messages)


def get_short_memory(target_id: int | str, limit: int) -> list[tuple]:
    """Return JSON-backed memory in the legacy tuple shape used by Ollama prompts."""
    if limit <= 0:
        return []
    messages = _read_short_memory()
    matching = [
        message for message in messages
        if str(message.get("target_id")) == str(target_id)
    ][-limit:]
    return [
        (
            index,
            message.get("created_at"),
            message.get("ai_provider"),
            message.get("target_id"),
            message.get("user_id"),
            message.get("user_name"),
            message.get("user_role"),
            message.get("user_message_type"),
            message.get("user_message", ""),
        )
        for index, message in enumerate(matching)
    ]


def clear_short_memory(target_id: int | str) -> int:
    """Delete short-term chat memory for one target and return its row count."""
    messages = _read_short_memory()
    remaining = [
        message for message in messages
        if str(message.get("target_id")) != str(target_id)
    ]
    removed_count = len(messages) - len(remaining)
    if removed_count:
        _write_short_memory(remaining)
    return removed_count
