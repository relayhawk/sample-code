import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from vcon_app.decorators.webhook_auth import validate_ui_key, validate_webhook
from vcon_app.services.vcon_store import VconStore
from vcon_app.utils.logger import logger, request_id_context

load_dotenv()

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
if not WEBHOOK_SECRET:
    raise ValueError("WEBHOOK_SECRET environment variable is required")

UI_API_KEY = os.getenv("UI_API_KEY") or secrets.token_urlsafe(24)

CAPTURE_DIR = Path("/app/captured")
CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

logger.info("=" * 60)
logger.info("Free World Dialup vCons server starting")
logger.info(f"UI API key: {UI_API_KEY}")
logger.info("=" * 60)

app = FastAPI(title="Free World Dialup vCons")
store = VconStore()

_webhook_dep = validate_webhook(WEBHOOK_SECRET)
_ui_key_dep = validate_ui_key(UI_API_KEY)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/webhook")
async def receive_webhook(request: Request, _=Depends(_webhook_dep)):
    req_id = str(uuid.uuid4())[:8]
    request_id_context.set(req_id)

    raw_body = await request.body()
    headers = dict(request.headers)

    # Capture every inbound request to disk for inspection
    capture = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "headers": headers,
        "query_params": dict(request.query_params),
        "body_raw": raw_body.decode("utf-8", errors="replace"),
    }
    try:
        capture["body_parsed"] = json.loads(raw_body)
    except Exception:
        capture["body_parsed"] = None

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    capture_path = CAPTURE_DIR / f"{ts}_{req_id}.json"
    capture_path.write_text(json.dumps(capture, indent=2))

    logger.info(f"Webhook received — saved to {capture_path.name}")

    payload = capture["body_parsed"] if capture["body_parsed"] is not None else capture["body_raw"]
    entry = store.add(headers=headers, payload=payload)

    logger.info(f"Stored vCon id={entry['id']}")
    return JSONResponse({"status": "ok", "id": entry["id"]}, status_code=200)


@app.get("/api/vcons")
async def list_vcons(_=Depends(_ui_key_dep)):
    return store.list()


@app.get("/api/vcons/{vcon_id}")
async def get_vcon(vcon_id: str, _=Depends(_ui_key_dep)):
    entry = store.get(vcon_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Not found")
    return entry
