"""
Talk to the agent by typing, no phone required.

This script pretends to be Twilio: it requests TwiML, opens the ConversationRelay
WebSocket, and sends your lines as `prompt` messages. The agent, state engine,
storage and dashboard all run exactly as they do on a real call, so it's a quick
way to try prompt changes or to demo the dashboard.

    python -m scripts.simulate_call                       # interactive
    python -m scripts.simulate_call --say "I'm Ada" --say "Lovelace" --say yes ...

Run it with the same .env as the server so it can sign the webhook request.
"""

import argparse
import asyncio
import json
import os
import re
import secrets

import httpx
import websockets
from dotenv import load_dotenv
from twilio.request_validator import RequestValidator

load_dotenv()

FROM_NUMBER = "+15555550100"


def public_url() -> str:
    host = (os.getenv("PUBLIC_URL") or os.getenv("NGROK_URL") or "localhost:8000")
    return "https://" + host.replace("https://", "").replace("http://", "").strip("/")


async def receive_reply(ws) -> dict | None:
    """Print agent tokens until the end of the utterance. Returns an `end` message if one arrives."""
    parts: list[str] = []
    while True:
        message = json.loads(await ws.recv())
        if message["type"] == "end":
            return message
        if message["type"] == "text":
            parts.append(message["token"])
            if message["last"]:
                print(f"  agent > {''.join(parts)}")
                return None


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server", default="http://localhost:8000", help="Where the FastAPI app is running")
    parser.add_argument("--say", action="append", help="A caller line (repeat for each turn). Omit for interactive.")
    args = parser.parse_args()

    call_sid = "CA" + secrets.token_hex(16)
    params = {"CallSid": call_sid, "From": FROM_NUMBER, "To": os.getenv("TWILIO_PHONE_NUMBER", "+15555550199"),
              "Direction": "inbound", "AccountSid": os.getenv("TWILIO_ACCOUNT_SID", "")}
    headers = {}
    if token := os.getenv("TWILIO_AUTH_TOKEN"):
        headers["X-Twilio-Signature"] = RequestValidator(token).compute_signature(f"{public_url()}/twiml", params)

    async with httpx.AsyncClient(base_url=args.server) as client:
        twiml = await client.post("/twiml", data=params, headers=headers)
        twiml.raise_for_status()
    session_token = re.search(r'name="token" value="([^"]+)"', twiml.text).group(1)
    greeting = re.search(r'welcomeGreeting="([^"]*)"', twiml.text).group(1)

    ws_url = args.server.replace("http", "ws", 1) + "/ws"
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({
            "type": "setup", "callSid": call_sid, "sessionId": "VX" + secrets.token_hex(16),
            "from": FROM_NUMBER, "to": params["To"], "direction": "inbound",
            "customParameters": {"token": session_token},
        }))
        print(f"Call {call_sid} (watch it at {args.server}/calls/{call_sid})\n")
        print(f"  agent > {greeting}")

        lines = iter(args.say) if args.say else None
        while True:
            if lines is not None:
                line = next(lines, None)
                if line is None:
                    break
                print(f" caller > {line}")
            else:
                try:
                    line = await asyncio.to_thread(input, " caller > ")
                except EOFError:
                    break
            if not line.strip():
                continue
            await ws.send(json.dumps({"type": "prompt", "voicePrompt": line, "lang": "en-US", "last": True}))
            end = await receive_reply(ws)
            if end is None:
                continue
            print(f"\nAgent ended the call. Handoff data: {end.get('handoffData')}")
            return

        # Ran out of lines: give a pending goodbye a moment to arrive, then hang up.
        try:
            while (end := await asyncio.wait_for(receive_reply(ws), timeout=8)) is None:
                pass
            print(f"\nAgent ended the call. Handoff data: {end.get('handoffData')}")
        except asyncio.TimeoutError:
            print("\nHanging up.")


if __name__ == "__main__":
    asyncio.run(main())
