from typing import Any, Dict, Protocol


class WebsocketBridgeProtocol(Protocol):
    """Protocol defining how two different applications communicate via WebSocket streams.

    This protocol serves as a translator between two different WebSocket applications
    (e.g., Twilio and OpenAI). Each application may use different message formats,
    protocols, or data types. The MessageHandler implementation defines how to:

    1. Convert messages from Application A's format to Application B's format
    2. Convert messages from Application B's format to Application A's format
    3. Handle connection lifecycle events for both applications
    4. Manage stream state and termination conditions

    Example:
        TwilioOpenAIHandler would implement this protocol to:
        - Convert Twilio's audio format to OpenAI's expected format
        - Convert OpenAI's responses to Twilio's message format
        - Handle Twilio's media streaming protocol
        - Manage OpenAI's session state
    """

    async def process_incoming(self, message: str) -> str | None:
        """Process message from source, return message for target or None to skip"""
        ...

    async def process_outgoing(self, message: str) -> str | None:
        """Process message from target, return message for source or None to skip"""
        ...

    async def on_connect(self) -> None:
        """Handle connection to the message handler"""
        ...

    async def on_disconnect(self) -> None:
        """Handle disconnection from the message handler"""
        ...

    async def should_terminate(self) -> bool:
        """Check if the stream should terminate"""
        ...

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the message handler"""
        ...
