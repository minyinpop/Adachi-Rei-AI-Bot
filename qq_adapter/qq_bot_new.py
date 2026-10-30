"""QQ official bot executable entry point.

Run from the project root with:
    python -m qq_adapter.qq_bot_new
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT_PATH = Path(__file__).resolve().parents[1]
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))

from qq_adapter.official_bot.runtime import main


if __name__ == "__main__":
    try:
        import asyncio
        asyncio.run(main())
    except KeyboardInterrupt:
        print("【🛑】QQ_OWARI_BOT 已停止。")
