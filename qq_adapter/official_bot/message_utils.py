"""Helpers for parsing QQ events and message content."""
from __future__ import annotations

import base64
import binascii
import json
import re
from functools import lru_cache
from .settings import APP_ID


@lru_cache(maxsize=None)
def _opencc(config: str):
    try:
        from opencc import OpenCC
    except ImportError:
        print(
            "【⚠】缺少 opencc-python-reimplemented，繁簡比對停用。請執行："
            "python -m pip install -r qq_adapter/requirements_official.txt"
        )
        return None
    return OpenCC(config)


def _convert_chinese(text: str, config: str) -> str:
    converter = _opencc(config)
    return converter.convert(text) if converter else text


def to_simplified(text: str) -> str:
    """Normalize Traditional Chinese to Simplified so both match each other."""
    return _convert_chinese(text, "t2s")


def _name_variants(name: str) -> set[str]:
    return {name, to_simplified(name), _convert_chinese(name, "s2t")}

BOT_DISPLAY_NAME = "热音终"
_MENTION_ID_KEYS = ("member_openid", "user_openid", "openid", "user_id", "id", "bot_appid")
# A leading mention in either QQ markup form: <@!ID> or <qqbot-at-user id="ID" />.
_LEADING_MARKUP_RE = re.compile(
    r"""^\s*(?:<@!?([^>\s]+)>|<qqbot-at-user\b[^>]*\bid=["']([^"']+)["'][^>]*/?>)\s*""",
    re.IGNORECASE,
)


# QQ face markup, e.g. <faceType=1,faceId="38",ext="eyJ0ZXh0Ijoi5pWy5omTIn0=">,
# where ext is base64 JSON such as {"text": "敲打"}.
_FACE_MARKUP_RE = re.compile(r"<faceType=(\d+)[^>]*>", re.IGNORECASE)
_FACE_EXT_RE = re.compile(r"\bext=[\"']([^\"']*)[\"']", re.IGNORECASE)
# faceType 6 is a sticker; QQ sends its picture as an image attachment, so the
# markup itself carries nothing the model can use.
_STICKER_FACE_TYPES = {"6"}


def _face_name(markup: str) -> str:
    ext = _FACE_EXT_RE.search(markup)
    if not ext:
        return ""
    try:
        data = json.loads(base64.b64decode(ext.group(1)).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return ""
    return str(data.get("text") or "").strip() if isinstance(data, dict) else ""


def _replace_face_markup(content: str) -> str:
    """Turn QQ face markup into readable text such as [表情：敲打]."""
    def replace(match: re.Match) -> str:
        if match.group(1) in _STICKER_FACE_TYPES:
            return ""
        name = _face_name(match.group(0))
        return f"[表情：{name}]" if name else "[表情]"

    return _FACE_MARKUP_RE.sub(replace, content)


def _raw_mentions(raw_event: dict) -> list:
    mentions = raw_event.get("mentions", [])
    if isinstance(mentions, dict):
        mentions = list(mentions.values())
    return mentions if isinstance(mentions, list) else []


def _is_self_mention(mention) -> bool:
    # `bot: true` marks any bot in the group; only `is_you` means this bot.
    return isinstance(mention, dict) and bool(mention.get("is_you"))


def _resolve_bot_ids(raw_event: dict) -> set[str]:
    """Collect every ID that refers to this bot in the event's mentions."""
    bot_ids = {APP_ID} if APP_ID else set()
    for mention in _raw_mentions(raw_event):
        if not _is_self_mention(mention):
            continue
        for key in _MENTION_ID_KEYS:
            value = str(mention.get(key) or "").strip().lstrip("@!")
            if value:
                bot_ids.add(value)
    return bot_ids


def _is_bot_mentioned(raw_event: dict) -> bool:
    """Whether a group message @-mentions this bot."""
    if any(_is_self_mention(mention) for mention in _raw_mentions(raw_event)):
        return True
    content = str(raw_event.get("content") or "")
    return bool(APP_ID) and re.search(
        rf"<@!?{re.escape(APP_ID)}>|<qqbot-at-user\b[^>]*\bid=[\"']{re.escape(APP_ID)}[\"']",
        content,
        re.IGNORECASE,
    ) is not None


def _clean_message_content(
    content: str,
    mentioned_members: list[dict[str, str]] | None = None,
    bot_ids: set[str] | None = None,
) -> str:
    """Replace QQ mention markup with readable names instead of dropping it."""
    name_by_id = {
        member["user_id"]: member["name"]
        for member in (mentioned_members or [])
    }
    bot_ids = bot_ids or set()

    def replace_mention(match: re.Match) -> str:
        member_id = match.group(1).lstrip("@!")
        if member_id in bot_ids:
            return f"@{BOT_DISPLAY_NAME}"
        display_name = name_by_id.get(member_id, "被@的群成员")
        return f"@{display_name}"

    content = re.sub(r"<@!?([^>\s]+)>", replace_mention, content)
    content = re.sub(
        r"<qqbot-at-user\b[^>]*\bid=[\"']([^\"']+)[\"'][^>]*/?>",
        replace_mention,
        content,
        flags=re.IGNORECASE,
    )
    content = _replace_face_markup(content)
    return re.sub(r"[ \t]{2,}", " ", content).strip()

def _get_message_value(event, key: str, default=""):
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)

def _strip_leading_bot_mention(content: str, bot_ids: set[str]) -> str:
    """Remove the leading mention when it refers to this bot."""
    leading = _LEADING_MARKUP_RE.match(content)
    if leading and (leading.group(1) or leading.group(2)).lstrip("@!") in bot_ids:
        return content[leading.end():]
    leading = re.match(r"^\s*@!?([^\s]+)\s+", content)
    if leading and leading.group(1).lstrip("@!") in bot_ids:
        return content[leading.end():]
    return content

def _get_sender_name(event) -> str:
    name = str(_get_message_value(event, "user_name", "") or "").strip()
    if name and name.casefold() not in {"none", "群友"}:
        return name

    raw = _get_message_value(event, "raw", {}) or {}
    if isinstance(raw, dict):
        author = raw.get("author") or {}
        member = raw.get("member") or {}
        member_user = member.get("user") or {} if isinstance(member, dict) else {}
        for candidate in (
            author.get("username") if isinstance(author, dict) else "",
            member.get("nick") if isinstance(member, dict) else "",
            member_user.get("username") if isinstance(member_user, dict) else "",
        ):
            candidate = str(candidate or "").strip()
            if candidate and candidate.casefold() not in {"none", "群友"}:
                return candidate
    return "群友"

def _extract_mentioned_members(
    event,
    bot_ids: set[str],
) -> list[dict[str, str]]:
    """Resolve members other than this bot from mentions metadata and markup.

    The sender is kept when they @ themselves, so their identity is looked up
    like any other mentioned member.
    """
    raw = _get_message_value(event, "raw", {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    content = str(raw.get("content") or _get_message_value(event, "content", ""))

    extracted: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for mention in _raw_mentions(raw):
        if isinstance(mention, str):
            member_id = mention.strip()
            display_name = "被@的群成员"
        elif isinstance(mention, dict):
            nested_user = mention.get("user") or mention.get("author") or {}
            if not isinstance(nested_user, dict):
                nested_user = {}
            if _is_self_mention(mention):
                continue
            member_id = str(
                mention.get("member_openid")
                or mention.get("user_openid")
                or mention.get("openid")
                or mention.get("user_id")
                or mention.get("id")
                or nested_user.get("member_openid")
                or nested_user.get("user_openid")
                or nested_user.get("openid")
                or nested_user.get("id")
                or ""
            ).strip()
            display_name = str(
                mention.get("nick")
                or mention.get("nickname")
                or mention.get("username")
                or mention.get("name")
                or mention.get("display_name")
                or nested_user.get("nick")
                or nested_user.get("nickname")
                or nested_user.get("username")
                or nested_user.get("name")
                or "被@的群成员"
            ).strip()
        else:
            continue
        if (
            not member_id
            or member_id.lstrip("@!") in bot_ids
            or member_id in seen_ids
        ):
            continue
        seen_ids.add(member_id)
        extracted.append({"user_id": member_id, "name": display_name})

    # Some QQ event variants omit the structured `mentions` array while keeping
    # target OpenIDs embedded in content as <@...> or <qqbot-at-user ...>.
    markup_ids = re.findall(r"<@!?([^>\s]+)>", content)
    markup_ids.extend(re.findall(
        r"<qqbot-at-user\b[^>]*\bid=[\"']([^\"']+)[\"'][^>]*/?>",
        content,
        flags=re.IGNORECASE,
    ))
    for member_id in markup_ids:
        member_id = member_id.strip()
        if (
            not member_id
            or member_id.lstrip("@!") in bot_ids
            or member_id in seen_ids
        ):
            continue
        seen_ids.add(member_id)
        extracted.append({"user_id": member_id, "name": "被@的群成员"})
    return extracted

def _link_member_mentions(text: str, member_ids: dict[str, str]) -> tuple[str, bool]:
    """Turn "@name" for known members into QQ @ markup.

    QQ only renders the markup as a real @ inside a Markdown message, so the
    caller must send the result with markdown=True when this returns True.
    """
    linked = False
    # The model may write a name in either Traditional or Simplified Chinese.
    variant_ids = {
        variant: member_id
        for name, member_id in member_ids.items()
        for variant in _name_variants(name)
    }
    # Longest name first so "@Bob2" is not matched as "@Bob".
    for name in sorted(variant_ids, key=len, reverse=True):
        pattern = re.compile(rf"[@＠]{re.escape(name)}")
        if pattern.search(text):
            markup = f'<qqbot-at-user id="{variant_ids[name]}" />'
            text = pattern.sub(lambda _: markup, text)
            linked = True
    return text, linked

def _collect_image_attachments(event) -> list:
    """Return the event's image attachments, de-duplicated by URL."""
    inbound_attachments = list(_get_message_value(event, "attachments", []) or [])
    for element in (_get_message_value(event, "msg_elements", []) or []):
        inbound_attachments.extend(_get_message_value(element, "attachments", []) or [])
    unique_images = {}
    for attachment in inbound_attachments:
        if not _is_image_attachment(attachment):
            continue
        image_url = str(_get_message_value(attachment, "url", ""))
        if image_url:
            unique_images.setdefault(image_url, attachment)
    return list(unique_images.values())

def _is_image_attachment(attachment) -> bool:
    content_type = str(_get_message_value(attachment, "content_type", "")).lower()
    filename = str(_get_message_value(attachment, "filename", "")).lower()
    url = str(_get_message_value(attachment, "url", "")).lower().split("?", 1)[0]
    return content_type.startswith("image/") or any(
        filename.endswith(ext) or url.endswith(ext)
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
    )
