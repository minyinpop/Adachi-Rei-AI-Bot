"""Runtime configuration for the official QQ bot."""
from __future__ import annotations

import json
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_PATH = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_PATH / ".env")

APP_ID = os.getenv("QQ_OWARI_BOT_ID", "").strip()
APP_SECRET = os.getenv("QQ_OWARI_BOT_SECRET", "").strip()
OLLAMA_CONFIG_PATH = ROOT_PATH / "ai_adapter" / "ollama_adapter" / "ollama_configs.json"
SCHEDULED_MESSAGES_PATH = ROOT_PATH / "qq_adapter" / "settings" / "scheduled_messages.json"
MODEL_SWITCH_COMMANDS = {
    "/model_set_27b3": "gemma3:27b",
    "/model_set_26b4": "gemma4:26b",
}
ALLOWED_GROUP_OPENIDS = {
    group_id.strip()
    for group_id in os.getenv("QQ_OWARI_ALLOWED_GROUP_OPENIDS", "").split(",")
    if group_id.strip()
}


def set_ollama_response_model(model_name: str) -> str:
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


def get_ollama_response_model() -> str:
    config = json.loads(OLLAMA_CONFIG_PATH.read_text(encoding="utf-8"))
    return str(config.get("response_model", "")).strip()
