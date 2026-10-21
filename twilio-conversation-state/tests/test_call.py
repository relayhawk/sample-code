"""
End-to-end: drive /twiml and /ws exactly the way Twilio does, with a scripted LLM.
"""

import re

import pytest
from fastapi.testclient import TestClient

import src.agent as agent
from src.main import create_app
from src.twilio_api import TwilioAPI

from .conftest import FakeTwilio, ScriptedLLM

CALL_SID = "CA" + "1" * 32


@pytest.fixture(autouse=True)
def instant_hangup(monkeypatch):
    monkeypatch.setattr(agent, "SECONDS_PER_WORD", 0)
    monkeypatch.setattr(agent, "HANGUP_PADDING_SEC", 0)


def start_call(client: TestClient) -> str:
    response = client.post("/twiml", data={"CallSid": CALL_SID, "From": "+13125550148", "To": "+15559870000",
                                           "Direction": "inbound"})
    assert response.status_code == 200
    assert "<ConversationRelay" in response.text
    assert 'url="wss://example.ngrok.app/ws"' in response.text
    return re.search(r'name="token" value="([0-9a-f]+)"', response.text).group(1)


def setup_message(token: str) -> dict:
    return {"type": "setup", "callSid": CALL_SID, "sessionId": "VX1", "from": "+13125550148",
            "to": "+15559870000", "direction": "inbound", "customParameters": {"token": token}}


def say(ws, text: str) -> str:
    """Send a caller utterance and collect the agent's reply up to the last token."""
    ws.send_json({"type": "prompt", "voicePrompt": text, "lang": "en-US", "last": True})
    reply = ""
    while True:
        message = ws.receive_json()
        assert message["type"] == "text"
        reply += message["token"]
        if message["last"]:
            return reply


def test_full_call_collects_state_and_is_replayable(settings):
    llm = ScriptedLLM([
        [("record_answer", {"field": "first_name", "value": "grace"})],
        "Nice to meet you, Grace! And what's your last name?",
        [("record_answer", {"field": "last_name", "value": "Hopper"})],
        [("confirm_readback", {})],
        "Great. What's the best email to reach you?",
        [("record_answer", {"field": "email", "value": "grace@relayhawk.com"})],
        [("confirm_readback", {})],
        "And is the number you're calling from the best one to reach you?",
        [("record_answer", {"field": "phone", "value": "caller_id"})],
        [("confirm_readback", {})],
    ])
    twilio = FakeTwilio(TwilioAPI(settings))
    app = create_app(settings, llm=llm, twilio=twilio)

    with TestClient(app) as client:
        token = start_call(client)
        with client.websocket_connect("/ws") as ws:
            ws.send_json(setup_message(token))

            assert say(ws, "Hi, it's Grace") == "Nice to meet you, Grace! And what's your last name?"
            assert "H - O - P - P - E - R" in say(ws, "Hopper")  # deterministic readback, no LLM text
            assert say(ws, "Yep") == "Great. What's the best email to reach you?"
            assert "at - R - E - L - A - Y - H - A - W - K dot com" in say(ws, "grace at relayhawk dot com")
            assert "calling from" in say(ws, "That's right")
            summary = say(ws, "Yes, that number's fine")  # caller ID accepted, straight to the summary
            assert "grace at relayhawk dot com" in summary and "three one two. five five five" in summary
            assert "all set" in say(ws, "Yes")

            end = ws.receive_json()
            assert end["type"] == "end"
            assert '"email": "grace@relayhawk.com"' in end["handoffData"]
            assert '"phone": "+13125550148"' in end["handoffData"]

        # The LLM always sees the current directive as the last message.
        assert "CURRENT DIRECTIVE" in llm.requests[0]["messages"][-1]["content"]
        assert llm.requests[1]["tool_choice"] == "none"
        assert twilio.recordings_started == [CALL_SID]

        payload = client.get(f"/api/calls/{CALL_SID}").json()
        call, events = payload["call"], payload["events"]
        assert call["status"] == "completed"
        assert call["from_number"] == "+13125550148"
        assert call["final_state"]["completed"] is True
        assert call["recording_sid"].startswith("RE")

        kinds = [e["kind"] for e in events]
        assert kinds[0] == "call.started"
        assert {"session.setup", "transcript", "tool", "snapshot", "llm", "session.end"} <= set(kinds)
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

        steps = [e["data"]["step"] for e in events if e["kind"] == "snapshot"]
        assert steps[0] == "first_name" and steps[-1] == "complete"
        sources = [e["data"]["source"] for e in events if e["kind"] == "transcript" and e["data"]["role"] == "agent"]
        assert sources[0] == "greeting" and "engine" in sources and "llm" in sources

        assert client.get("/api/calls").json()[0]["call_sid"] == CALL_SID


def test_interrupt_trims_history_to_what_the_caller_heard(settings):
    llm = ScriptedLLM([
        "Sorry, could you tell me your first name one more time please?",
        [("record_answer", {"field": "first_name", "value": "Grace"})],
        "Thanks Grace. And your last name?",
    ])
    app = create_app(settings, llm=llm, twilio=FakeTwilio(TwilioAPI(settings)))
    with TestClient(app) as client:
        token = start_call(client)
        with client.websocket_connect("/ws") as ws:
            ws.send_json(setup_message(token))
            say(ws, "uh")
            ws.send_json({"type": "interrupt", "utteranceUntilInterrupt": "Sorry, could you",
                          "durationUntilInterruptMs": 900})
            say(ws, "Grace")

        history = llm.requests[1]["messages"]
        assistant_lines = [m["content"] for m in history if m["role"] == "assistant"]
        assert "Sorry, could you" in assistant_lines
        assert not any("one more time" in (line or "") for line in assistant_lines)

        events = client.get(f"/api/calls/{CALL_SID}").json()["events"]
        interrupt = next(e for e in events if e["kind"] == "interrupt")
        assert interrupt["data"]["heard"] == "Sorry, could you"


def test_session_with_a_bad_token_is_refused(settings):
    app = create_app(settings, llm=ScriptedLLM([]), twilio=FakeTwilio(TwilioAPI(settings)))
    with TestClient(app) as client:
        start_call(client)
        with client.websocket_connect("/ws") as ws:
            ws.send_json(setup_message("not-the-token"))
            with pytest.raises(Exception):
                ws.receive_json()
        assert client.get(f"/api/calls/{CALL_SID}").json()["call"]["final_state"] is None


def test_webhooks_reject_bad_signatures(settings):
    app = create_app(settings, llm=ScriptedLLM([]))  # real TwilioAPI, validation on
    with TestClient(app) as client:
        response = client.post("/twiml", data={"CallSid": CALL_SID}, headers={"X-Twilio-Signature": "bogus"})
        assert response.status_code == 403


def test_dashboard_password(settings):
    import dataclasses

    locked = dataclasses.replace(settings, dashboard_password="hunter2")
    app = create_app(locked, llm=ScriptedLLM([]), twilio=FakeTwilio(TwilioAPI(locked)))
    with TestClient(app) as client:
        assert client.get("/api/calls").status_code == 401
        assert client.get("/api/calls", auth=("demo", "hunter2")).status_code == 200
        page = client.get("/", auth=("demo", "hunter2"))
        assert page.status_code == 200 and "dashboard" in page.cookies
        with client.websocket_connect("/ui/ws") as ws:  # cookie from the page load
            ws.send_text("ping")
