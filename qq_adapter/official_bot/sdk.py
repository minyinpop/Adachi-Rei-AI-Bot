"""Optional QQ Bot SDK imports with an actionable setup error."""
try:
    import qqbot_agent_sdk.websocket as _sdk_websocket
    from qqbot_agent_sdk import (
        EventParser,
        MEDIA_TYPE_IMAGE,
        MEDIA_TYPE_VOICE,
        MediaInfo,
        MediaUploader,
        MessageToCreate,
        QQApiClient,
        QQMessageType,
        QQWebSocket,
        WSCallbacks,
    )
except ImportError as exc:
    raise SystemExit(
        "缺少 QQ Bot SDK。請在專案虛擬環境執行："
        "python -m pip install -r qq_adapter/requirements_official.txt"
    ) from exc

# Groups whose owner enables "receive all group messages" push every message
# as GROUP_MESSAGE_CREATE, which is the only variant that includes the
# `mentions` metadata. qqbot-agent-sdk 1.2.2 drops this event type, so
# register it for dispatch and parse it like a group @ message.
GROUP_MESSAGE_CREATE = "GROUP_MESSAGE_CREATE"
_sdk_websocket.MESSAGE_EVENT_TYPES = _sdk_websocket.MESSAGE_EVENT_TYPES | {GROUP_MESSAGE_CREATE}
EventParser._EVENT_HANDLERS[GROUP_MESSAGE_CREATE] = EventParser._EVENT_HANDLERS["GROUP_AT_MESSAGE_CREATE"]
