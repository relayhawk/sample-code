import websockets
import json
from typing import Dict, Any, List
from twilio_openai.protocols.tool import ToolProtocol
from twilio_openai.utils.logger import logger


class OpenAIService:
    def __init__(self, api_key: str, voice: str = "alloy", tools: List[ToolProtocol] = []):
        self.api_key = api_key
        self.voice = voice
        self.logger = logger
        self.tools: Dict[str, ToolProtocol] = {}
        self.websocket = None
        if tools:
            self._register_tools_to_dict(tools)

    def _register_tools_to_dict(self, tools: List[ToolProtocol]) -> None:
        """Convert list of tools to dictionary with tool names as keys."""
        for tool in tools:
            tool_name = tool.get_tool_definition()["name"]
            self.tools[tool_name] = tool
            self.logger.info(f"Registered tool: {tool_name}")

    async def send_initial_greeting(self, websocket, greeting: str | None = None, system_message: str | None = None) -> None:
        """Send an initial greeting message to start the conversation."""
        self.logger.info("Sending initial greeting message")
        if not greeting:
            # We need to add the system prompt to the greeting because the response is not guaranteed to reference it
            greeting = "Greet the caller with the greeting based on the following system prompt.\n\n" + system_message
        try:
            greeting_message = {
                "event_id": "greeting_001",
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"],
                    "instructions": greeting,
                }
            }

            await websocket.send(json.dumps(greeting_message))
            self.logger.debug("Sent initial greeting message")

        except Exception as e:
            self.logger.error(f"Error sending initial greeting: {e}")
            raise

    async def setup_realtime_websocket_connection(self, model: str, system_message: str) -> websockets.WebSocketClientProtocol:
        """Set up WebSocket connection to OpenAI."""
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "OpenAI-Beta": "realtime=v1"
            }

            websocket = await websockets.connect(
                'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
                additional_headers=headers
            )

            # Configure initial session
            await self.send_session_update(websocket, system_message)

            # Send initial greeting
            await self.send_initial_greeting(websocket, system_message)

            self.logger.info("OpenAI WebSocket connection established")
            return websocket

        except Exception as e:
            self.logger.error(f"Error setting up OpenAI connection: {e}")
            raise

    async def send_session_update(self, ws, system_message: str):
        session_update = {
            "type": "session.update",
            "session": {
                "turn_detection": {"type": "server_vad"},
                "input_audio_format": "g711_ulaw",
                "output_audio_format": "g711_ulaw",
                "voice": self.voice,
                "instructions": system_message,
                "modalities": ["text", "audio"],
                "temperature": 0.8,
                "tools": [
                    tool.get_tool_definition()
                    for tool in self.tools.values()
                ]
            }
        }
        await ws.send(json.dumps(session_update))

    async def update_session_tools(self) -> None:
        """Update the OpenAI session with all registered tools."""
        try:
            tool_definitions = [
                tool.get_tool_definition()
                for tool in self.tools.values()
            ]

            update_message = {
                "type": "session.update",
                "session": {
                    "tools": tool_definitions,
                    "tool_choice": "auto",
                }
            }

            if self.websocket and self.websocket.open:
                await self.websocket.send_json(update_message)
                self.logger.debug(f"Updated session with {len(tool_definitions)} tools")  # NOQA
            else:
                self.logger.warning("Cannot update tools: WebSocket not connected")  # NOQA

        except Exception as e:
            self.logger.error(f"Error updating session tools: {str(e)}", exc_info=True)  # NOQA
            raise

    async def handle_tool_call(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle a tool call from OpenAI.

        Args:
            tool_name: Name of the tool to call
            params: Parameters for the tool

        Returns:
            Tool response

        Raises:
            ValueError: If tool_name is not registered
        """
        if tool_name not in self.tools:
            self.logger.error(f"Tool not found: {tool_name}")
            raise ValueError(f"Unknown tool: {tool_name}")

        tool = self.tools[tool_name]
        try:
            self.logger.debug(
                f"Calling tool {tool_name} with params: {params}")
            response = await tool.handle(params)
            self.logger.debug(f"Tool {tool_name} response: {response}")
            return response
        except Exception as e:
            self.logger.error(f"Error executing tool {tool_name}: {str(e)}", exc_info=True)  # NOQA#
            raise

    async def process_tool_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Process a tool-related message from OpenAI.

        Args:
            message: The message from OpenAI containing tool call

        Returns:
            Tool response formatted for OpenAI
        """
        try:
            tool_call = message.get("tool_call", {})
            tool_name = tool_call.get("name")
            tool_params = tool_call.get("parameters", {})

            response = await self.handle_tool_call(tool_name, tool_params)

            return {
                "type": "tool_output",
                "tool_call_id": tool_call.get("id"),
                "output": response
            }

        except Exception as e:
            self.logger.error(f"Error processing tool message: {str(e)}", exc_info=True)  # NOQA
            return {
                "type": "error",
                "error": {
                    "message": str(e)
                }
            }
