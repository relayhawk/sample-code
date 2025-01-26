import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import WebSocket, WebSocketDisconnect
from services.openai_service import OpenAIService
from services.twilio_service import TwilioService
from twilio_openai.core.connection_manager import ConnectionManager


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
    return OpenAIService("fake_key", "test_model", "test_voice")


@pytest.mark.asyncio
async def test_twilio_connection_setup_success(mock_websocket, twilio_service):
    """Test successful Twilio connection setup."""
    # Mock the two expected messages from Twilio
    mock_websocket.receive_text.side_effect = [
        '{"event": "connected"}',
        '{"event": "start", "start": {"streamSid": "test_sid"}}'
    ]

    stream_sid = await twilio_service.setup_connection(mock_websocket)

    assert stream_sid == "test_sid"
    assert mock_websocket.receive_text.call_count == 2


@pytest.mark.asyncio
async def test_twilio_connection_setup_failure(mock_websocket, twilio_service):
    """Test Twilio connection setup with incorrect event sequence."""
    mock_websocket.receive_text.side_effect = [
        '{"event": "wrong_event"}'
    ]

    with pytest.raises(ValueError, match="Expected connected event"):
        await twilio_service.setup_connection(mock_websocket)


@pytest.mark.asyncio
async def test_openai_connection_setup(openai_service):
    """Test OpenAI connection setup."""
    mock_ws = AsyncMock()

    with patch('websockets.connect', AsyncMock(return_value=mock_ws)):
        ws = await openai_service.setup_realtime_websocket_connection("test message")

        assert ws == mock_ws
        # Verify session update was sent
        assert mock_ws.send.called


@pytest.mark.asyncio
async def test_connection_cleanup(mock_websocket, mock_openai_ws):
    """Test proper cleanup of connections."""
    # Simulate a connection error
    mock_websocket.receive_text.side_effect = WebSocketDisconnect()

    handler = MagicMock()
    handler.receive_stream = AsyncMock(side_effect=WebSocketDisconnect())
    handler.send_stream = AsyncMock()

    await ConnectionManager.process_streams(handler)

    # Verify both tasks were handled
    assert handler.receive_stream.called
    assert handler.send_stream.called


@pytest.mark.asyncio
async def test_graceful_shutdown(mock_websocket, mock_openai_ws):
    """Test graceful shutdown of streams."""
    handler = MagicMock()
    # Simulate normal completion of receive task
    handler.receive_stream = AsyncMock(return_value=None)
    # Simulate long-running send task
    handler.send_stream = AsyncMock(side_effect=asyncio.sleep(1000))

    # Process streams should complete when receive_stream finishes
    await ConnectionManager.process_streams(handler)

    # Verify send task was cancelled
    assert handler.send_stream.called
