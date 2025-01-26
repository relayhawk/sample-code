import json
from asyncio import Event
from typing import Optional
from fastapi import WebSocket
from websockets.client import WebSocketClientProtocol
from twilio_openai.protocols.websocket_adapter import WebsocketAdapterProtocol, WebSocketBridge, WebSocketBridgeNames
from abc import abstractmethod
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
from twilio_openai.utils.logger import logger


class WebsocketAdapterBase(WebsocketAdapterProtocol):
    """Base implementation for bidirectional WebSocket communication."""

    def __init__(self,
                 source_ws: WebSocket,
                 target_ws: WebSocketClientProtocol,
                 stream_id: str = None):
        self.source_ws = source_ws
        self.target_ws = target_ws
        self.stream_id = stream_id
        self.shutdown_event = Event()
        self.logger = logger

    def get_websockets(self) -> WebSocketBridge:
        return {
            WebSocketBridgeNames.SOURCE: self.source_ws,
            WebSocketBridgeNames.TARGET: self.target_ws
        }

    async def close(self):
        """Gracefully close the target WebSocket connection."""
        try:
            if self.target_ws and not self.target_ws.closed:
                await self.target_ws.close(1000, "Source disconnected")
                self.logger.info("Target WebSocket closed")
        except Exception as e:
            self.logger.debug(f"Error closing target WebSocket: {e}")

    async def receive_stream(self):
        try:
            while True:
                try:
                    message = await self.source_ws.receive_text()
                    processed_message = await self.process_incoming(message)
                    if processed_message:
                        if isinstance(processed_message, dict):
                            processed_message = json.dumps(processed_message)
                        await self.target_ws.send(processed_message)
                except WebSocketDisconnect:
                    self.logger.info("Source WebSocket disconnected normally")
                    # Close target gracefully if it's still open
                    try:
                        await self.target_ws.close(1000, "Source disconnected")
                    except Exception as e:
                        self.logger.debug(f"Error closing target: {e}")
                    break

        except Exception as e:
            self.logger.error(f"Error in receive_stream: {e}", exc_info=True)
            try:
                await self.close()
            except Exception as close_error:
                self.logger.debug(f"Error during cleanup: {close_error}")
            raise

    async def send_stream(self):
        try:
            while True:
                try:
                    message = await self.target_ws.recv()
                    processed_message = await self.process_outgoing(message)
                    if processed_message:
                        await self.source_ws.send_text(processed_message)
                except ConnectionClosedOK:
                    self.logger.info("Target WebSocket closed normally")
                    break
                except ConnectionClosedError as e:
                    self.logger.error(f"Target WebSocket closed with error: {e}")
                    break

        except Exception as e:
            self.logger.error(f"Error in send_stream: {e}")
        finally:
            # Only close target WebSocket in cleanup
            await self.close()

    @abstractmethod
    async def process_incoming(self, message: str) -> Optional[str | dict]:
        """Process incoming messages from source to target."""
        pass

    @abstractmethod
    async def process_outgoing(self, message: str) -> Optional[str]:
        """Process outgoing messages from target to source."""
        pass

    @abstractmethod
    async def should_terminate(self) -> bool:
        """Check if the stream should be terminated."""
        pass
