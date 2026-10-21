import dataclasses
from typing import Any, AsyncIterator

import pytest

from src.config import load_settings
from src.llm import StreamItem, TextDelta, ToolCall, ToolCalls


class ScriptedLLM:
    """
    Plays back canned model responses in order. Each script entry is either
    a string (spoken text, streamed word by word) or a list of (tool, args)
    tuples. Every request's messages are kept for assertions.
    """

    model = "scripted"

    def __init__(self, script: list[str | list[tuple[str, dict[str, Any]]]]):
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: str
    ) -> AsyncIterator[StreamItem]:
        self.requests.append({"messages": messages, "tool_choice": tool_choice})
        if not self.script:
            raise AssertionError("ScriptedLLM ran out of responses")
        step = self.script.pop(0)
        if isinstance(step, str):
            for i, word in enumerate(step.split(" ")):
                yield TextDelta(word if i == 0 else " " + word)
        else:
            yield ToolCalls([ToolCall(id=f"call_{len(self.requests)}_{i}", name=name, arguments=args)
                             for i, (name, args) in enumerate(step)])


class FakeTwilio:
    """TwilioAPI stand-in: no network, signatures always valid."""

    def __init__(self, real):
        self._real = real
        self.recordings_started: list[str] = []

    def signature_ok(self, *_: Any) -> bool:
        return True

    def session_token(self, call_sid: str) -> str:
        return self._real.session_token(call_sid)

    def session_token_ok(self, call_sid: str, token: str) -> bool:
        return self._real.session_token_ok(call_sid, token)

    async def start_recording(self, call_sid: str) -> str:
        self.recordings_started.append(call_sid)
        return "RE" + "0" * 32


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "example.ngrok.app")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC" + "0" * 32)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "")
    base = load_settings()
    return dataclasses.replace(base, database_path=str(tmp_path / "calls.db"),
                               recordings_dir=str(tmp_path / "recordings"))
