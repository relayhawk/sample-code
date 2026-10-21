"""
FastAPI app: Twilio webhooks, the ConversationRelay WebSocket, and the dashboard.

Twilio-facing
    POST /twiml                  call starts -> TwiML that connects ConversationRelay
    WS   /ws                     ConversationRelay session (setup / prompt / interrupt / ...)
    POST /twiml/connect-action   ConversationRelay session ended -> hang up
    POST /twilio/status          call status callbacks (ringing, completed, ...)
    POST /twilio/recording       recording finished

Dashboard-facing (optionally behind DASHBOARD_PASSWORD)
    GET  /                       live + replay UI
    WS   /ui/ws                  live event stream for every call
    GET  /api/calls              recent calls
    GET  /api/calls/{sid}        one call and its full event timeline
    GET  /api/calls/{sid}/recording   the call audio
    POST /api/calls/outbound     have the agent call a number
"""

import asyncio
import base64
import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.agent import CallSession
from src.config import Settings, load_settings
from src.hub import LiveHub, Recorder
from src.llm import LLM, OpenAILLM
from src.store import CallStore, now_iso
from src.twilio_api import TwilioAPI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, llm: LLM | None = None, twilio: TwilioAPI | None = None) -> FastAPI:
    settings = settings or load_settings()
    store = CallStore(settings.database_path)
    hub = LiveHub()
    recorder = Recorder(store, hub)
    twilio = twilio or TwilioAPI(settings)
    llm = llm or OpenAILLM(settings.openai_api_key, settings.openai_model)
    background: set[asyncio.Task[Any]] = set()

    def spawn(coro: Any) -> None:
        task = asyncio.create_task(coro)
        background.add(task)
        task.add_done_callback(background.discard)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logger.info("Voice webhook: %s/twiml", settings.public_base_url)
        logger.info("Dashboard:     %s/", settings.public_base_url)
        yield
        store.close()

    app = FastAPI(title="Twilio ConversationRelay — Conversation State", lifespan=lifespan)
    app.state.store = store
    app.state.hub = hub
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # -- auth helpers ----------------------------------------------------------

    basic = HTTPBasic(auto_error=False)

    def password_ok(password: str | None) -> bool:
        if not settings.dashboard_password:
            return True
        return secrets.compare_digest((password or "").encode(), settings.dashboard_password.encode())

    def require_dashboard(credentials: HTTPBasicCredentials | None = Depends(basic)) -> None:
        if not password_ok(credentials.password if credentials else None):
            raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": 'Basic realm="dashboard"'})

    # Browsers don't reliably send Basic credentials on a WebSocket upgrade, so the
    # page sets a cookie derived from the password that /ui/ws checks instead.
    dashboard_cookie = twilio.session_token("dashboard:" + settings.dashboard_password)

    def dashboard_page() -> FileResponse:
        response = FileResponse(STATIC_DIR / "index.html")
        response.set_cookie("dashboard", dashboard_cookie, httponly=True, samesite="strict")
        return response

    async def twilio_form(request: Request) -> dict[str, str]:
        """Parse a Twilio webhook and reject it if the signature does not match."""
        form = {key: str(value) for key, value in (await request.form()).items()}
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        if not twilio.signature_ok(path, form, request.headers.get("X-Twilio-Signature", "")):
            logger.warning("Rejected webhook with a bad Twilio signature: %s", path)
            raise HTTPException(403, "Invalid Twilio signature")
        return form

    # -- Twilio webhooks ---------------------------------------------------------

    @app.post("/twiml")
    async def twiml(request: Request) -> Response:
        form = await twilio_form(request)
        call_sid = form.get("CallSid", "")
        direction = "inbound" if form.get("Direction", "inbound") == "inbound" else "outbound"
        store.start_call(call_sid, direction=direction, from_number=form.get("From", ""), to_number=form.get("To", ""))
        recorder.event(call_sid, "call.started", {
            "from": form.get("From"), "to": form.get("To"), "direction": form.get("Direction"),
            "caller_city": form.get("FromCity"), "caller_state": form.get("FromState"),
        })
        recorder.call_changed(call_sid)

        base = settings.public_base_url
        ws_url = f"wss://{settings.public_host}/ws"
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect action={quoteattr(base + "/twiml/connect-action")}>
        <ConversationRelay
            url={quoteattr(ws_url)}
            welcomeGreeting={quoteattr(settings.welcome_greeting)}
            ttsProvider={quoteattr(settings.tts_provider)}
            voice={quoteattr(settings.tts_voice)}
            elevenlabsTextNormalization="on"
            interruptible="any"
            dtmfDetection="true">
            <Parameter name="token" value={quoteattr(twilio.session_token(call_sid))} />
        </ConversationRelay>
    </Connect>
</Response>"""
        return Response(content=body, media_type="application/xml")

    @app.post("/twiml/connect-action")
    async def connect_action(request: Request) -> Response:
        form = await twilio_form(request)
        call_sid = form.get("CallSid", "")
        recorder.event(call_sid, "session.ended", {
            "status": form.get("SessionStatus"),
            "duration_sec": form.get("SessionDuration"),
            "handoff_data": _json_or_text(form.get("HandoffData")),
            "error": form.get("ErrorMessage"),
        })
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
            media_type="application/xml",
        )

    @app.post("/twilio/status")
    async def status_callback(request: Request) -> dict[str, str]:
        form = await twilio_form(request)
        call_sid = form.get("CallSid", "")
        status = form.get("CallStatus", "")
        if store.get_call(call_sid) is None:
            store.start_call(call_sid, direction=form.get("Direction", ""), from_number=form.get("From", ""),
                             to_number=form.get("To", ""))
        recorder.event(call_sid, "twilio.status", {"status": status, "duration_sec": form.get("CallDuration")})
        if status in ("completed", "busy", "failed", "no-answer", "canceled"):
            updates: dict[str, Any] = {"status": status}
            if form.get("CallDuration"):
                updates["duration_sec"] = int(form["CallDuration"])
            call = store.get_call(call_sid)
            if call and not call["ended_at"]:
                updates["ended_at"] = now_iso()
            store.update_call(call_sid, **updates)
        recorder.call_changed(call_sid)
        return {"status": "received"}

    @app.post("/twilio/recording")
    async def recording_callback(request: Request) -> dict[str, str]:
        form = await twilio_form(request)
        call_sid = form.get("CallSid", "")
        recording_sid = form.get("RecordingSid", "")
        recorder.event(call_sid, "recording", {
            "status": form.get("RecordingStatus"),
            "recording_sid": recording_sid,
            "duration_sec": form.get("RecordingDuration"),
            "channels": form.get("RecordingChannels"),
        })
        if form.get("RecordingStatus") == "completed" and recording_sid:
            store.update_call(call_sid, recording_sid=recording_sid)
            recorder.call_changed(call_sid)
            # Keep a local copy so replays still work if the Twilio recording is deleted.
            spawn(_cache_recording(recording_sid))
        return {"status": "received"}

    async def _cache_recording(recording_sid: str) -> Path | None:
        dest = Path(settings.recordings_dir) / f"{recording_sid}.mp3"
        if dest.exists():
            return dest
        try:
            return await twilio.download_recording(recording_sid, dest)
        except Exception:
            logger.exception("Could not download recording %s", recording_sid)
            return None

    async def _start_recording(call_sid: str) -> None:
        try:
            recording_sid = await twilio.start_recording(call_sid)
        except Exception as exc:
            logger.exception("[%s] Could not start recording", call_sid)
            recorder.event(call_sid, "error", {"where": "start_recording", "message": str(exc)})
            return
        event = recorder.event(call_sid, "recording.started", {"recording_sid": recording_sid})
        store.update_call(call_sid, recording_sid=recording_sid, recording_offset_ms=event["t_ms"])
        recorder.call_changed(call_sid)

    # -- ConversationRelay WebSocket ----------------------------------------------

    @app.websocket("/ws")
    async def conversation_relay(websocket: WebSocket) -> None:
        await websocket.accept()
        session: CallSession | None = None
        call_sid = ""

        async def send(message: dict[str, Any]) -> None:
            await websocket.send_text(json.dumps(message))

        try:
            while True:
                message = json.loads(await websocket.receive_text())
                kind = message.get("type")

                if kind == "setup":
                    call_sid = message.get("callSid", "")
                    token = (message.get("customParameters") or {}).get("token", "")
                    if not twilio.session_token_ok(call_sid, token):
                        logger.warning("Rejected ConversationRelay session with a bad token for %s", call_sid)
                        await websocket.close(code=1008)
                        return
                    store.start_call(call_sid, direction=message.get("direction", ""),
                                     from_number=message.get("from", ""), to_number=message.get("to", ""))
                    recorder.event(call_sid, "session.setup", {
                        k: message.get(k) for k in ("sessionId", "from", "to", "direction", "callStatus")
                    })
                    # The person on the line: the caller on inbound calls, the callee on outbound ones.
                    outbound = message.get("direction", "").startswith("outbound")
                    caller_number = message.get("to") if outbound else message.get("from")
                    session = CallSession(call_sid, send, recorder, llm, settings.company_name, caller_number)
                    session.start(settings.welcome_greeting)
                    recorder.call_changed(call_sid)
                    if settings.enable_recording and settings.twilio_configured:
                        spawn(_start_recording(call_sid))

                elif session is None:
                    logger.warning("Ignoring %s before setup", kind)

                elif kind == "prompt":
                    text = message.get("voicePrompt", "").strip()
                    if text:
                        session.on_prompt(text)

                elif kind == "interrupt":
                    await session.on_interrupt(message.get("utteranceUntilInterrupt", ""),
                                               message.get("durationUntilInterruptMs"))

                elif kind == "dtmf":
                    recorder.event(call_sid, "dtmf", {"digit": message.get("digit")})

                elif kind == "error":
                    recorder.event(call_sid, "error", {"where": "conversation_relay",
                                                       "message": message.get("description")})
                else:
                    recorder.event(call_sid, "relay.unknown", {"message": message})

        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("[%s] ConversationRelay socket error", call_sid)
        finally:
            if session:
                await session.close()
            if call_sid:
                recorder.event(call_sid, "socket.closed", {})
                final = session.flow.snapshot() if session else None
                call = store.get_call(call_sid)
                updates: dict[str, Any] = {"final_state": final}
                if call and call["status"] == "live":
                    updates["status"] = "completed"
                if call and not call["ended_at"]:
                    updates["ended_at"] = now_iso()
                store.update_call(call_sid, **updates)
                recorder.call_changed(call_sid)

    # -- dashboard --------------------------------------------------------------

    @app.get("/", dependencies=[Depends(require_dashboard)])
    async def index() -> FileResponse:
        return dashboard_page()

    @app.get("/calls/{call_sid}", dependencies=[Depends(require_dashboard)])
    async def call_page(call_sid: str) -> FileResponse:
        return dashboard_page()

    @app.get("/api/config", dependencies=[Depends(require_dashboard)])
    async def config() -> dict[str, Any]:
        return {
            "company": settings.company_name,
            "phone_number": settings.twilio_phone_number,
            "voice_url": f"{settings.public_base_url}/twiml",
            "model": llm.model,
            "outbound_enabled": bool(settings.twilio_configured and settings.twilio_phone_number
                                     and settings.dashboard_password),
        }

    @app.get("/api/calls", dependencies=[Depends(require_dashboard)])
    async def list_calls() -> list[dict[str, Any]]:
        return store.list_calls()

    @app.get("/api/calls/{call_sid}", dependencies=[Depends(require_dashboard)])
    async def get_call(call_sid: str) -> dict[str, Any]:
        call = store.get_call(call_sid)
        if call is None:
            raise HTTPException(404, "Call not found")
        return {"call": call, "events": store.list_events(call_sid)}

    @app.get("/api/calls/{call_sid}/recording", dependencies=[Depends(require_dashboard)])
    async def get_recording(call_sid: str) -> FileResponse:
        call = store.get_call(call_sid)
        if not call or not call["recording_sid"]:
            raise HTTPException(404, "No recording for this call")
        path = await _cache_recording(call["recording_sid"])
        if path is None:
            raise HTTPException(404, "Recording is not available yet")
        return FileResponse(path, media_type="audio/mpeg")

    class OutboundRequest(BaseModel):
        to: str

    @app.post("/api/calls/outbound", dependencies=[Depends(require_dashboard)])
    async def outbound(body: OutboundRequest) -> JSONResponse:
        # Placing calls costs money, so it is only allowed when the dashboard is password protected.
        if not settings.dashboard_password:
            raise HTTPException(403, "Set DASHBOARD_PASSWORD to enable outbound calls")
        if not (settings.twilio_configured and settings.twilio_phone_number):
            raise HTTPException(400, "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER")
        try:
            call_sid = await twilio.place_call(body.to.strip())
        except Exception as exc:
            raise HTTPException(502, f"Twilio rejected the call: {exc}") from exc
        store.start_call(call_sid, direction="outbound", from_number=settings.twilio_phone_number, to_number=body.to)
        recorder.call_changed(call_sid)
        return JSONResponse({"call_sid": call_sid})

    @app.websocket("/ui/ws")
    async def dashboard_socket(websocket: WebSocket) -> None:
        cookie_ok = secrets.compare_digest(websocket.cookies.get("dashboard", ""), dashboard_cookie)
        if not cookie_ok and not password_ok(_basic_password(websocket.headers.get("authorization", ""))):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue = hub.subscribe()
        receiver = asyncio.create_task(_drain(websocket))
        try:
            while not receiver.done():
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    await websocket.send_text(json.dumps(getter.result(), default=str))
                else:
                    getter.cancel()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.unsubscribe(queue)
            receiver.cancel()

    return app


async def _drain(websocket: WebSocket) -> None:
    """Read (and ignore) client messages so we notice when the browser goes away."""
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return


def _basic_password(header: str) -> str | None:
    if not header.lower().startswith("basic "):
        return None
    try:
        return base64.b64decode(header[6:]).decode().partition(":")[2]
    except ValueError:
        return None


def _json_or_text(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value

