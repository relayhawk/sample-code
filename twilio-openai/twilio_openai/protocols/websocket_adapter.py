from typing import Dict, Protocol, Optional, Union
from enum import Enum
from fastapi import WebSocket
from websockets.client import WebSocketClientProtocol


class WebSocketBridgeNames(str, Enum):
    SOURCE = "source"
    TARGET = "target"


# We use a Union of WebSocket types because we need to support both FastAPI's WebSocket
# for the server-side connection (source) and websockets' WebSocketClientProtocol for
# the client-side connection (target). While not ideal, this reflects the reality of
# working with these two different WebSocket implementations.
WebSocketBridge = Dict[WebSocketBridgeNames, Union[WebSocket, WebSocketClientProtocol]]


class WebsocketAdapterProtocol(Protocol):
    """Protocol defining the interface for WebSocket adapters."""

    async def get_websockets(self) -> WebSocketBridge:
        """Return the bridged WebSocket connections."""
        ...

    async def close(self):
        """Gracefully close all connections."""
        ...

    async def receive_stream(self):
        """Handle messages flowing from source to target."""
        ...

    async def send_stream(self):
        """Handle messages flowing from target to source."""
        ...

    async def process_incoming(self, message: str) -> Optional[str | dict]:
        """Process incoming messages from source to target."""
        ...

    async def process_outgoing(self, message: str) -> Optional[str]:
        """Process outgoing messages from target to source."""
        ...

    async def should_terminate(self) -> bool:
        """Check if the stream should be terminated."""
        ...
