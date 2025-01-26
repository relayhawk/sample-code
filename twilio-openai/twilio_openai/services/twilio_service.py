from twilio.twiml.voice_response import VoiceResponse, Connect
import json
from fastapi import WebSocket

from twilio_openai.utils.logger import logger


class TwilioService:
    def get_twiml_connect_mediastream(self, stream_url: str) -> str:
        """Generate TwiML response for incoming Twilio calls.

        Args:
            stream_url (str): The WebSocket URL for the media stream connection

        Returns:
            str: TwiML response as a string
        """
        # Create a new TwiML response
        response = VoiceResponse()

        # Set up media stream connection
        connect = Connect()
        connect.stream(url=stream_url)
        # Add the connection to the response
        response.append(connect)

        logger.debug(f"Generated TwiML response: {response}")
        return str(response)

    async def setup_websocket_connection(self, websocket: WebSocket) -> str:
        """Handle Twilio's two-step WebSocket handshake."""
        logger.debug("Starting Twilio connection setup...")

        # Step 1: Wait for connected event
        data = await self._receive_event(websocket, 'connected')
        logger.debug(f"Connected event received: {data}")

        # Step 2: Wait for start event
        data = await self._receive_event(websocket, 'start')
        logger.debug(f"Start event received: {data}")

        return data['start']['streamSid']

    async def _receive_event(self, websocket: WebSocket, expected_event: str) -> dict:
        """Wait for and validate a specific Twilio event."""
        message = await websocket.receive_text()
        data = json.loads(message)

        if data['event'] != expected_event:
            raise ValueError(f"Expected {expected_event} event, got: {data['event']}")

        return data
