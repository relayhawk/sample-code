import base64
import json
from typing import Any, Dict, Optional
from fastapi import WebSocket
from websockets.client import WebSocketClientProtocol
from twilio_openai.adapters.adapter_base import WebsocketAdapterBase
from twilio_openai.utils.logger import logger


class TwilioOpenAIAdapter(WebsocketAdapterBase):
    """Handles translation of messages between Twilio and OpenAI WebSocket protocols.

    Implements the MessageHandler protocol to:
    - Convert Twilio media packets to OpenAI audio format
    - Convert OpenAI responses to Twilio media format
    - Handle Twilio-specific events (start/stop/media)
    - Handle OpenAI tool calls
    - Manage stream state and packet counting
    """

    def __init__(self, source_ws: WebSocket, target_ws: WebSocketClientProtocol,
                 stream_sid: str, openai_service):
        super().__init__(
            source_ws=source_ws,
            target_ws=target_ws,
            stream_id=stream_sid
        )
        self.stream_sid = stream_sid
        self.openai_service = openai_service
        self.media_packet_count = 0
        self.response_packet_count = 0
        self.logger = logger
        self.should_terminate_flag = False

    async def process_incoming(self, message: str) -> Optional[str | dict]:
        """Process incoming Twilio messages for OpenAI."""
        try:
            data = json.loads(message)
            self.logger.debug(f"TwilioMessageHandler processing incoming message type: {data.get('event')}")

            if data['event'] == 'media':
                self.media_packet_count += 1
                try:
                    audio_payload = data['media']['payload']
                    decoded_audio = base64.b64decode(audio_payload)
                    if len(decoded_audio) == 0:
                        self.logger.warning("Empty audio payload received")
                        return None

                    self.logger.debug(f"TwilioMessageHandler processing media packet {self.media_packet_count} - Size: {len(decoded_audio)} bytes")
                    return {
                        "type": "input_audio_buffer.append",
                        "audio": audio_payload
                    }

                except KeyError as e:
                    self.logger.error(f"Missing expected field in media message: {e}")
                    self.logger.debug(f"Full message structure: {data}")
                    return None
                except Exception as e:
                    self.logger.error(f"Error processing media packet: {e}")
                    return None

            elif data['event'] == 'stop':
                self.logger.info(f"Received stop event after {self.media_packet_count} packets")
                # Instead of raising an exception, set a flag and return None
                self.should_terminate_flag = True
                return None

            self.logger.debug(f"Message type {data.get('event')} was filtered")
            return None
        except Exception as e:
            self.logger.error(f"Error in process_incoming: {e}", exc_info=True)
            return None

    async def _handle_tool_call(self, item: dict) -> None:
        """Handle a tool call from OpenAI and send back the response."""
        tool_name = item.get('name')
        arguments = json.loads(item.get('arguments', '{}'))
        call_id = item.get('call_id')

        result = await self.openai_service.handle_tool_call(tool_name, arguments)
        self.logger.info(f"Tool call result: {result}")

        # First send the tool response
        tool_response = {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result)
            }
        }

        await self.target_ws.send(json.dumps(tool_response))
        self.logger.info("Tool response sent to OpenAI")

        continue_message = {
            "event_id": "continue_001",
            "type": "response.create",
            "response": {
                "modalities": ["text", "audio"],
                "instructions": "Please respond to the user based on the tool call result.",
            }
        }

        await self.target_ws.send(json.dumps(continue_message))
        self.logger.info("Continuation message sent to OpenAI")

    async def process_outgoing(self, message: str) -> Optional[str]:
        """Process outgoing OpenAI messages for Twilio."""
        try:
            response = json.loads(message)
            self.logger.info(f"Processing outgoing message type: {response.get('type')}")
            self.logger.debug(f"Full message content: {message[:200]}...")

            if response['type'] == 'response.done':
                output_items = response.get('response', {}).get('output', [])
                for item in output_items:
                    if item.get('type') == 'function_call':
                        self.logger.info("matched function_call")
                        await self._handle_tool_call(item)
                        return None

            elif response['type'] == 'error':
                self.logger.error(f"OpenAI error: {response.get('error', {}).get('message', 'Unknown error')}")
                # TODO: determine if we should terminate the connection or not
                self.should_terminate_flag = True
                raise Exception(f"OpenAI error: {response.get('error', {}).get('message', 'Unknown error')}")

            elif response['type'] == 'conversation.item.created':
                self.logger.info(f"OpenAI acknowledged tool response: {response.get('item', {}).get('type')}")

            elif response['type'] == 'response.audio.delta' and response.get('delta'):
                self.logger.info("Received audio response from OpenAI")
                self.response_packet_count += 1
                try:
                    audio_payload = base64.b64encode(
                        base64.b64decode(response['delta'])
                    ).decode('utf-8')

                    self.logger.debug(f"Processing response packet {self.response_packet_count} - Size: {len(audio_payload)} bytes")
                    response_message = {
                        "event": "media",
                        "streamSid": self.stream_sid,
                        "media": {
                            "payload": audio_payload
                        }
                    }
                    self.logger.debug(f"Sending response to Twilio: {str(response_message)[:200]}...")
                    return json.dumps(response_message)

                except Exception as e:
                    self.logger.error(f"Error processing audio response: {e}", exc_info=True)
                    return None
            else:
                self.logger.debug("Message processed, continuing to next message")
                return None

        except Exception as e:
            self.logger.error(f"Error in process_outgoing: {e}", exc_info=True)
            return None

    async def on_connect(self) -> None:
        """Handle initial connection setup."""
        self.media_packet_count = 0
        self.response_packet_count = 0
        self.should_terminate_flag = False

    async def on_disconnect(self) -> None:
        """Handle cleanup when connection ends."""
        self.logger.info(f"Processed {self.media_packet_count} media packets and {self.response_packet_count} response packets")

    async def should_terminate(self) -> bool:
        """Check if the stream should be terminated."""
        return self.should_terminate_flag

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the message handling."""
        return {
            'media_packets': self.media_packet_count,
            'response_packets': self.response_packet_count,
            'stream_sid': self.stream_sid
        }
