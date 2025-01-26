import os
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv
from twilio_openai.adapters.TwilioOpenAIAdapter import TwilioOpenAIAdapter
from twilio_openai.utils.utils import load_system_message
from twilio_openai.services.openai_service import OpenAIService
from twilio_openai.services.twilio_service import TwilioService
from twilio_openai.core.connection_manager import ConnectionManager
import websockets
from twilio_openai.decorators.twilio_auth import validate_twilio_request, validate_twilio_websocket
from twilio_openai.tools.availability import AvailabilityTool
from twilio_openai.utils.logger import logger, stream_sid_context

load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
SYSTEM_MESSAGE = load_system_message()
VOICE = os.getenv('OPENAI_VOICE', 'alloy')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini-realtime-preview-2024-12-17')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

if not TWILIO_AUTH_TOKEN:
    raise ValueError('Missing the Twilio Auth Token. Please set it in the .env file.')

app = FastAPI()
twilio_service = TwilioService()


@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}


@app.api_route("/incoming-call", methods=["GET", "POST"])
@validate_twilio_request
async def handle_incoming_call(request: Request):
    logger.debug("Incoming call received")
    # Construct WebSocket URL using FastAPI's URL path based on the route's function name
    ws_path = app.url_path_for('handle_media_stream')
    media_stream_url = f'wss://{request.url.hostname}{ws_path}'
    logger.debug(f"Media stream URL: {media_stream_url}")

    twiml_response = twilio_service.get_twiml_connect_mediastream(media_stream_url)
    logger.debug(f"TwiML Response: {twiml_response}")

    return HTMLResponse(
        content=twiml_response,
        media_type="application/xml"
    )


@app.websocket("/media-stream")
@validate_twilio_websocket
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    try:
        await websocket.accept()
        logger.info("WebSocket connection established")

        # Handle Twilio's connection setup
        stream_sid = await twilio_service.setup_websocket_connection(websocket)
        # Set the stream_sid in context
        stream_sid_context.set(stream_sid)
        logger.info("Twilio connection established")

        # Set up OpenAI connection
        tools = [AvailabilityTool()]
        openai_service = OpenAIService(OPENAI_API_KEY, VOICE, tools)
        openai_ws = await openai_service.setup_realtime_websocket_connection(OPENAI_MODEL, SYSTEM_MESSAGE)
        logger.info("OpenAI connection established")

        # Tools are now automatically registered and updated in OpenAIService initialization
        await openai_service.update_session_tools()
        logger.info("OpenAI session updated with tools")

        # Set up message handler with the stream_sid
        handler = TwilioOpenAIAdapter(
            source_ws=websocket,
            target_ws=openai_ws,
            stream_sid=stream_sid,
            openai_service=openai_service
        )

        # Process the bidirectional streams using our ConnectionManager
        await ConnectionManager.process_streams(handler)

    except websockets.exceptions.ConnectionClosedOK:
        logger.info("WebSocket connection closed normally")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        raise
    finally:
        logger.info("Closing WebSocket connection")
