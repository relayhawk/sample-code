import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call
from fastapi import WebSocket, WebSocketDisconnect
from twilio_openai.services.openai_service import OpenAIService
from twilio_openai.services.twilio_service import TwilioService
from twilio_openai.core.connection_manager import ConnectionManager
import json


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket with async methods."""
    ws = AsyncMock(spec=WebSocket)
    ws.receive_text = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def mock_openai_ws():
    """Create a mock OpenAI WebSocket."""
    ws = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def twilio_service():
    return TwilioService()


@pytest.fixture
def openai_service():
    return OpenAIService(api_key="fake_key", voice="test_voice")


@pytest.mark.asyncio
async def test_twilio_connection_setup_success(mock_websocket, twilio_service):
    """Test successful Twilio connection setup."""
    # Mock the two expected messages from Twilio
    mock_websocket.receive_text.side_effect = [
        '{"event": "connected"}',
        '{"event": "start", "start": {"streamSid": "test_sid"}}'
    ]

    stream_sid = await twilio_service.setup_websocket_connection(mock_websocket)

    assert stream_sid == "test_sid"
    assert mock_websocket.receive_text.call_count == 2


@pytest.mark.asyncio
async def test_twilio_connection_setup_failure(mock_websocket, twilio_service):
    """Test Twilio connection setup with incorrect event sequence."""
    mock_websocket.receive_text.side_effect = [
        '{"event": "wrong_event"}'
    ]

    with pytest.raises(ValueError, match="Expected connected event"):
        await twilio_service.setup_websocket_connection(mock_websocket)


@pytest.mark.asyncio
async def test_openai_connection_setup(openai_service):
    """Test OpenAI connection setup."""
    mock_ws = AsyncMock()
    # Configure the mock to return a JSON string
    mock_ws.recv.return_value = json.dumps({
        "type": "session.update.response",
        "status": "success"
    })

    with patch('websockets.connect', AsyncMock(return_value=mock_ws)):
        ws = await openai_service.setup_realtime_websocket_connection("gpt-4o-realtime-preview-2024-10-01", "test system message")

        # Verify both messages were sent in the correct order
        assert mock_ws.send.call_count == 2

        # First call should be session update
        first_call = mock_ws.send.call_args_list[0]
        assert first_call == call(json.dumps({
            "type": "session.update",
            "session": {
                "turn_detection": {"type": "server_vad"},
                "input_audio_format": "g711_ulaw",
                "output_audio_format": "g711_ulaw",
                "voice": "test_voice",
                "instructions": "test system message",
                "modalities": ["text", "audio"],
                "temperature": 0.8
            }
        }))

        # Second call should be initial greeting
        second_call = mock_ws.send.call_args_list[1]
        assert second_call == call(json.dumps({
            "event_id": "greeting_001",
            "type": "response.create",
            "response": {
                "modalities": ["text", "audio"],
                "instructions": "Greet the caller based on information in the system prompt."
            }
        }))

        assert ws == mock_ws


@pytest.mark.asyncio
async def test_connection_cleanup(mock_websocket, mock_openai_ws):
    """Test proper cleanup of connections."""
    handler = MagicMock()

    # Create an async function that raises WebSocketDisconnect
    async def receive_with_disconnect():
        raise WebSocketDisconnect()

    # Create an async function that runs indefinitely
    async def send_forever():
        while True:
            await asyncio.sleep(0.1)

    handler.receive_stream = receive_with_disconnect
    handler.send_stream = send_forever

    # Process streams should handle the disconnect gracefully
    await ConnectionManager.process_streams(handler)


@pytest.mark.asyncio
async def test_graceful_shutdown(mock_websocket, mock_openai_ws):
    """Test graceful shutdown of streams."""
    handler = MagicMock()

    # Create an async function that completes normally
    async def receive_and_complete():
        return None

    # Create an async function that runs indefinitely
    async def send_forever():
        while True:
            await asyncio.sleep(0.1)

    handler.receive_stream = receive_and_complete
    handler.send_stream = send_forever

    # Process streams should complete when receive_stream finishes
    await ConnectionManager.process_streams(handler)


@pytest.mark.asyncio
async def test_websocket_cleanup_on_success():
    """Test that WebSockets are properly closed on successful completion."""
    # Mock WebSockets
    source_ws = AsyncMock()
    target_ws = AsyncMock()

    # Create handler that completes normally
    handler = MagicMock()

    async def complete_normally():
        return None
    handler.receive_stream = complete_normally
    handler.send_stream = complete_normally

    # Mock the get_websockets method
    handler.get_websockets.return_value = {
        'source': source_ws,
        'target': target_ws
    }

    await ConnectionManager.process_streams(handler)

    # Verify both WebSockets were closed
    source_ws.close.assert_called_once()
    target_ws.close.assert_called_once()


@pytest.mark.asyncio
async def test_websocket_cleanup_on_error():
    """Test that WebSockets are properly closed even when an error occurs."""
    # Mock WebSockets
    source_ws = AsyncMock()
    target_ws = AsyncMock()

    # Create handler that raises an exception
    handler = MagicMock()

    async def raise_error():
        raise RuntimeError("Simulated error")
    handler.receive_stream = raise_error
    handler.send_stream = AsyncMock()

    # Mock the get_websockets method
    handler.get_websockets.return_value = {
        'source': source_ws,
        'target': target_ws
    }

    # Process should raise the error but still clean up
    with pytest.raises(RuntimeError):
        await ConnectionManager.process_streams(handler)

    # Verify both WebSockets were closed despite the error
    source_ws.close.assert_called_once()
    target_ws.close.assert_called_once()


@pytest.mark.asyncio
async def test_websocket_cleanup_on_disconnect():
    """Test that WebSockets are properly closed on WebSocket disconnect."""
    # Mock WebSockets
    source_ws = AsyncMock()
    target_ws = AsyncMock()

    # Create handler that simulates a disconnect
    handler = MagicMock()

    async def simulate_disconnect():
        raise WebSocketDisconnect()
    handler.receive_stream = simulate_disconnect
    handler.send_stream = AsyncMock()

    # Mock the get_websockets method
    handler.get_websockets.return_value = {
        'source': source_ws,
        'target': target_ws
    }

    await ConnectionManager.process_streams(handler)

    # Verify both WebSockets were closed
    source_ws.close.assert_called_once()
    target_ws.close.assert_called_once()
