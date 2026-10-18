"""Dashboard WebSocket transport: connection hub + virtual IM channel."""

from octop.infra.gateway.ws.ws_channel import WS_CHANNEL_ID, WebSocketChannel
from octop.infra.gateway.ws.ws_hub import WebSocketHub

# Backward-compatible aliases (same channel id string).
DASHBOARD_CHANNEL_ID = WS_CHANNEL_ID

__all__ = [
    "DASHBOARD_CHANNEL_ID",
    "WS_CHANNEL_ID",
    "WebSocketChannel",
    "WebSocketHub",
]
