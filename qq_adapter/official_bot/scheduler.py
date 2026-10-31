"""Proactive group messages driven by settings/scheduled_messages.json.

Jobs repeat daily unless they set `date`, which makes them one-off.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .sdk import (
    MEDIA_TYPE_IMAGE,
    MediaInfo,
    MediaUploader,
    MessageToCreate,
    QQApiClient,
    QQMessageType,
)
from .settings import SCHEDULED_MESSAGES_PATH


def load_scheduled_messages() -> list[dict]:
    """Return valid jobs as {"time", "date", "group_openid", "text", "image"}.

    `image` is optional and relative to the settings folder; `date`
    (YYYY-MM-DD) is optional and sends the job once on that day only.
    """
    if not SCHEDULED_MESSAGES_PATH.exists():
        return []
    # A broken schedule file must only disable scheduling, never stop the bot.
    try:
        entries = json.loads(SCHEDULED_MESSAGES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"【❌】無法讀取排程設定 {SCHEDULED_MESSAGES_PATH.name}，本次不啟用排程：{exc}")
        return []
    if not isinstance(entries, list):
        print(f"【❌】排程設定 {SCHEDULED_MESSAGES_PATH.name} 最外層必須是陣列，本次不啟用排程。")
        return []
    jobs = []
    for entry in entries:
        if not isinstance(entry, dict):
            print(f"【⚠】排程訊息設定格式錯誤，已略過：{entry!r}（每筆必須是物件）")
            continue
        try:
            image = str(entry.get("image") or "").strip()
            image_path = SCHEDULED_MESSAGES_PATH.parent / image if image else None
            if image_path and not image_path.is_file():
                raise ValueError(f"找不到圖片 {image_path}")
            once = str(entry.get("date") or "").strip()
            jobs.append({
                "time": time.fromisoformat(str(entry["time"])),
                "date": date.fromisoformat(once) if once else None,
                "group_openid": str(entry["group_openid"]).strip(),
                "text": str(entry.get("text") or "").strip(),
                "image": image_path,
            })
        except KeyError as exc:
            print(f"【⚠】排程訊息設定缺少欄位 {exc}，已略過：{entry!r}")
        except (TypeError, ValueError) as exc:
            print(f"【⚠】排程訊息設定格式錯誤，已略過：{entry!r}（{exc}）")
    return [job for job in jobs if job["group_openid"] and (job["text"] or job["image"])]


def _next_run(job: dict, now: datetime) -> datetime | None:
    """Next send time, or None for a one-off job whose moment has passed."""
    job_time: time = job["time"]
    if job["date"] is not None:
        run_at = datetime.combine(job["date"], job_time.replace(second=0, microsecond=0))
        return run_at if run_at > now else None
    run_at = now.replace(
        hour=job_time.hour, minute=job_time.minute, second=0, microsecond=0
    )
    return run_at if run_at > now else run_at + timedelta(days=1)


async def _send_job(api: QQApiClient, uploader: MediaUploader, job: dict) -> None:
    group_openid = job["group_openid"]
    image_path: Path | None = job["image"]
    if image_path is None:
        await api.send_text("group", group_openid, job["text"], markdown=False)
        return
    # Text and image go out as one rich-media message (QQ shows the text below
    # the image) so each job uses a single proactive-message quota slot.
    file_info = await uploader.upload(
        chat_type="group",
        chat_id=group_openid,
        source=str(image_path),
        file_type=MEDIA_TYPE_IMAGE,
    )
    await api.post_group_message(group_openid, MessageToCreate(
        content=job["text"],
        msg_type=QQMessageType.RICH_MEDIA,
        msg_seq=api.next_msg_seq(),
        media=MediaInfo(file_info=file_info),
    ))


async def run_scheduled_messages(
    api: QQApiClient,
    uploader: MediaUploader,
    jobs: list[dict],
) -> None:
    """Send each job to its group at its local time, daily or on its date."""
    if not jobs:
        return
    for job in jobs:
        attachment = f"（附圖 {job['image'].name}）" if job["image"] else ""
        when = f"{job['date']:%Y-%m-%d} " if job["date"] else "每日 "
        expired = "（時間已過，不會發送）" if _next_run(job, datetime.now()) is None else ""
        print(
            f"【⏰】已排程{when}{job['time']:%H:%M} 主動發送至群 "
            f"{job['group_openid'][:8]}…：{job['text']}{attachment}{expired}"
        )
    while True:
        now = datetime.now()
        next_runs = [(_next_run(job, now), job) for job in jobs]
        next_runs = [(run, job) for run, job in next_runs if run is not None]
        if not next_runs:
            return
        run_at = min(run for run, _ in next_runs)
        await asyncio.sleep((run_at - now).total_seconds())
        for run, job in next_runs:
            if run != run_at:
                continue
            try:
                await _send_job(api, uploader, job)
                print(f"【⏰】已主動發送排程訊息至群 {job['group_openid'][:8]}…")
            except Exception as exc:
                print(f"【❌】排程訊息主動發送失敗：{exc}")
        # Step past the minute just handled so it is not sent twice.
        await asyncio.sleep(1)
