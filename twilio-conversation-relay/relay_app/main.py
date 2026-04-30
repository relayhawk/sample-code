import asyncio
import json
import os
from pathlib import Path
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv
from relay_app.services.twilio_service import TwilioService
from relay_app.services.openai_service import OpenAIService
from relay_app.decorators.twilio_auth import validate_twilio_request
from relay_app.utils.logger import logger, call_sid_context

load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-5-mini')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')

if not OPENAI_API_KEY:
    raise ValueError('Missing OPENAI_API_KEY. Please set it in the .env file.')

if not TWILIO_AUTH_TOKEN:
    raise ValueError('Missing TWILIO_AUTH_TOKEN. Please set it in the .env file.')

SYSTEM_PROMPT = (Path(__file__).parent / 'prompts' / 'system_prompt.md').read_text()

app = FastAPI()
twilio_service = TwilioService()
openai_service = OpenAIService(api_key=OPENAI_API_KEY, model=OPENAI_MODEL)


@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Conversation Relay server is running!"}


@app.api_route("/incoming-call", methods=["GET", "POST"])
@validate_twilio_request
async def handle_incoming_call(request: Request):
    logger.info("Incoming call received")
    ws_path = app.url_path_for('handle_relay_ws')
    ws_url = f"wss://{request.url.hostname}{ws_path}"
    logger.debug(f"WebSocket URL: {ws_url}")
    twiml = twilio_service.get_twiml_conversation_relay(ws_url)
    return HTMLResponse(content=twiml, media_type="application/xml")


@app.websocket("/relay-ws")
async def handle_relay_ws(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connection accepted")
    messages = []

    try:
        async for raw in websocket.iter_text():
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "setup":
                call_sid = data.get("callSid", "unknown")
                call_sid_context.set(call_sid)
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                logger.info(f"Setup received for call {call_sid}")

            elif msg_type == "prompt":
                user_text = data.get("voicePrompt", "")
                logger.info(f"User said: {user_text!r}")
                messages.append({"role": "user", "content": user_text})

                reply_text, should_end = await openai_service.get_reply(messages)
                logger.info(f"Bot reply: {reply_text!r}")

                await websocket.send_text(json.dumps({
                    "type": "text",
                    "token": reply_text,
                    "last": True,
                }))

                messages.append({"role": "assistant", "content": reply_text})

                if should_end:
                    # Wait for Twilio to finish speaking the confirmation before ending.
                    # Approximate TTS duration: ~3 words/sec + 1s buffer.
                    tts_seconds = max(4, len(reply_text.split()) // 3 + 1)
                    logger.info(f"Waiting {tts_seconds}s for TTS to finish before sending end")
                    await asyncio.sleep(tts_seconds)
                    logger.info("Sending end message to Conversation Relay")
                    await websocket.send_text(json.dumps({"type": "end"}))
                    break

            elif msg_type == "interrupt":
                logger.info("Caller interrupted — no in-flight streaming to cancel")

            elif msg_type == "error":
                logger.error(f"Error from Conversation Relay: {data}")
                break

            else:
                logger.debug(f"Unhandled message type: {msg_type}")

    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        logger.info("WebSocket connection closed")
