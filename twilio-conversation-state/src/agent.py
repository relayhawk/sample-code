"""
One CallSession per ConversationRelay WebSocket.

ConversationRelay does speech-to-text and text-to-speech; this class decides what
to say. Each caller turn runs like this:

1. The caller's words arrive as a `prompt` message.
2. The LLM sees the conversation plus the engine's CURRENT DIRECTIVE and either
   replies (streamed straight to Twilio) or calls a tool to record what it heard.
3. If it called tools, the engine updates the state and decides the next
   utterance: an exact `say` line (readbacks, summary, goodbye) or a second,
   tool-free LLM pass that follows the new directive.

Every step is recorded as an event so the dashboard can show it live and replay
it after the call.
"""

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

from src.flow import FIELDS, CallFlow, Directive
from src.hub import Recorder
from src.llm import LLM, TextDelta, ToolCall, ToolCalls

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a friendly phone agent for {company}. You are on a live phone call and everything you write is spoken aloud by text-to-speech.

Your only job is to collect the caller's first name, last name, email address, and phone number. A separate system tracks what has been collected and tells you what to do next in a CURRENT DIRECTIVE message. Follow it.

Rules:
- Whenever the caller gives a value, call record_answer for it, even if it was not the thing you asked for. If they give several at once ("I'm Grace Hopper"), call record_answer once per field. Never ask again for something you already have.
- When you call a tool, do not write any spoken text in the same response. The system decides what is said next.
- The transcription is noisy. Normalize spoken emails silently: "at" becomes @, "dot" becomes a period, "underscore" becomes _, "dash" becomes -. Join spelled letters ("s m i t h" is "smith"). Write spoken phone numbers as digits ("four oh four, five five five..." is 404555...).
- Never invent or guess a value. If you could not understand it, ask the caller to repeat it.
- Keep replies to one or two short sentences. No lists, markdown, emoji, or special characters.
- If the caller asks something unrelated, answer in one short sentence and then continue with the directive."""

FIELD_KEYS = [f.key for f in FIELDS]

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "record_answer",
            "description": "Record a value the caller just gave, or a correction to one. Call once per field.",
            "parameters": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": FIELD_KEYS},
                    "value": {"type": "string", "description": (
                        "The value, normalized: an email with @ and dots, a phone number as digits, "
                        "or 'caller_id' when they agree to use the number they're calling from."
                    )},
                },
                "required": ["field", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_readback",
            "description": "The caller confirmed the value or summary that was just read back to them.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject_readback",
            "description": (
                "The caller said the readback is wrong but did not give the corrected value. "
                "When rejecting the summary, pass the field they said is wrong."
            ),
            "parameters": {
                "type": "object",
                "properties": {"field": {"type": "string", "enum": FIELD_KEYS}},
            },
        },
    },
]

# Rough speaking rate used to wait for the goodbye to finish before hanging up.
SECONDS_PER_WORD = 0.4
HANGUP_PADDING_SEC = 1.5

Sender = Callable[[dict[str, Any]], Awaitable[None]]


class CallSession:
    def __init__(
        self, call_sid: str, send: Sender, recorder: Recorder, llm: LLM, company: str, caller_number: str | None = None
    ):
        self.call_sid = call_sid
        self.send = send
        self.recorder = recorder
        self.llm = llm
        self.flow = CallFlow.for_caller(caller_number)
        self.system_prompt = SYSTEM_PROMPT.format(company=company)
        self.history: list[dict[str, Any]] = []
        self._turn_task: asyncio.Task[None] | None = None
        self._hangup_task: asyncio.Task[None] | None = None
        self.ended = False

    # -- lifecycle -----------------------------------------------------------

    def start(self, welcome_greeting: str) -> None:
        """Called on `setup`. ConversationRelay speaks the welcome greeting itself."""
        self.history.append({"role": "assistant", "content": welcome_greeting})
        self.recorder.event(self.call_sid, "transcript", {"role": "agent", "text": welcome_greeting, "source": "greeting"})
        self._snapshot("setup")

    def on_prompt(self, text: str) -> None:
        """A new caller utterance. Supersedes any turn still being generated."""
        if self.ended:
            return
        self._cancel_turn()
        self._turn_task = asyncio.create_task(self._run_turn(text))

    async def on_interrupt(self, heard: str, duration_ms: int | None) -> None:
        """The caller talked over the agent. Keep only what they actually heard."""
        self._cancel_turn()
        for index in range(len(self.history) - 1, -1, -1):
            message = self.history[index]
            if message["role"] == "assistant" and message.get("content"):
                if heard or message.get("tool_calls"):
                    message["content"] = heard or None
                else:
                    del self.history[index]  # cut off before a single word played
                break
        self.recorder.event(self.call_sid, "interrupt", {"heard": heard, "duration_ms": duration_ms})

    async def close(self) -> None:
        self.ended = True
        self._cancel_turn()
        if self._hangup_task:
            self._hangup_task.cancel()
        if self._turn_task:
            await asyncio.gather(self._turn_task, return_exceptions=True)

    def _cancel_turn(self) -> None:
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()

    async def wait_idle(self) -> None:
        """Test helper: wait for the current turn to finish."""
        if self._turn_task:
            await asyncio.gather(self._turn_task, return_exceptions=True)

    # -- a single caller turn -------------------------------------------------

    async def _run_turn(self, text: str) -> None:
        try:
            self._drop_dangling_tool_calls()
            self.flow.begin_turn()
            self.history.append({"role": "user", "content": text})
            self.recorder.event(self.call_sid, "transcript", {"role": "caller", "text": text})

            tool_calls = await self._llm_pass(self.flow.directive(), tool_choice="auto", llm_pass=1)
            if not tool_calls:
                return

            for call in tool_calls:
                outcome = self._dispatch(call)
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(outcome),
                })
                self.recorder.event(self.call_sid, "tool", {"name": call.name, "arguments": call.arguments, **outcome})
            self._snapshot("tools")

            directive = self.flow.directive()
            if directive.say:
                await self._say(directive)
            else:
                await self._llm_pass(directive, tool_choice="none", llm_pass=2)
        except asyncio.CancelledError:
            self.recorder.event(self.call_sid, "turn.cancelled", {"turn": self.flow.turn})
            raise
        except Exception as exc:  # never let one bad turn kill the call
            logger.exception("[%s] turn failed", self.call_sid)
            self.recorder.event(self.call_sid, "error", {"where": "turn", "message": str(exc)})
            await self._send_text("Sorry, I missed that. Could you say it one more time?")

    def _dispatch(self, call: ToolCall) -> dict[str, Any]:
        args = call.arguments
        if call.name == "record_answer":
            outcome = self.flow.record_answer(str(args.get("field", "")), str(args.get("value", "")))
        elif call.name == "confirm_readback":
            outcome = self.flow.confirm_readback()
        elif call.name == "reject_readback":
            outcome = self.flow.reject_readback(args.get("field"))
        else:
            return {"ok": False, "message": f"Unknown tool {call.name}"}
        return outcome.to_dict()

    async def _llm_pass(self, directive: Directive, *, tool_choice: str, llm_pass: int) -> list[ToolCall]:
        messages = [
            {"role": "system", "content": self.system_prompt},
            *self.history,
            # The state block goes last and is not kept in history, so the model
            # always reads the *current* directive, never a stale one.
            {"role": "system", "content": self._state_block(directive)},
        ]
        started = time.perf_counter()
        first_token_ms: int | None = None
        spoken = ""
        tool_calls: list[ToolCall] = []
        try:
            async for item in self.llm.stream(messages, TOOLS, tool_choice):
                if isinstance(item, TextDelta) and item.text:
                    if first_token_ms is None:
                        first_token_ms = int((time.perf_counter() - started) * 1000)
                    spoken += item.text
                    await self.send({"type": "text", "token": item.text, "last": False})
                elif isinstance(item, ToolCalls):
                    tool_calls = item.calls
        finally:
            if spoken:
                await self.send({"type": "text", "token": "", "last": True})
            assistant: dict[str, Any] = {"role": "assistant", "content": spoken or None}
            if tool_calls:
                assistant["tool_calls"] = [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                    for c in tool_calls
                ]
            if spoken or tool_calls:
                self.history.append(assistant)
            if spoken:
                self.recorder.event(self.call_sid, "transcript", {"role": "agent", "text": spoken, "source": "llm"})
            self.recorder.event(self.call_sid, "llm", {
                "pass": llm_pass,
                "model": self.llm.model,
                "tool_choice": tool_choice,
                "directive": directive.instruction,
                "first_token_ms": first_token_ms,
                "total_ms": int((time.perf_counter() - started) * 1000),
                "tool_calls": [{"name": c.name, "arguments": c.arguments} for c in tool_calls],
            })
        return tool_calls

    async def _say(self, directive: Directive) -> None:
        """Speak an engine-owned line verbatim, no LLM involved."""
        assert directive.say is not None
        await self._send_text(directive.say)
        self.history.append({"role": "assistant", "content": directive.say})
        self.recorder.event(self.call_sid, "transcript", {"role": "agent", "text": directive.say, "source": "engine"})
        self.flow.mark_said(directive)
        self._snapshot("said")
        if directive.end_call:
            self._hangup_task = asyncio.create_task(self._hang_up_after(directive.say))

    async def _send_text(self, text: str) -> None:
        await self.send({"type": "text", "token": text, "last": True})

    async def _hang_up_after(self, goodbye: str) -> None:
        """ConversationRelay has no 'finished speaking' event, so estimate playback time."""
        await asyncio.sleep(len(goodbye.split()) * SECONDS_PER_WORD + HANGUP_PADDING_SEC)
        handoff = {"reason": "completed", "collected": self.flow.collected_values()}
        self.recorder.event(self.call_sid, "session.end", handoff)
        self.ended = True
        await self.send({"type": "end", "handoffData": json.dumps(handoff)})

    # -- helpers ---------------------------------------------------------------

    def _drop_dangling_tool_calls(self) -> None:
        """A turn cancelled mid-flight can leave tool calls with no results, which OpenAI rejects."""
        if self.history and self.history[-1]["role"] == "assistant" and self.history[-1].get("tool_calls"):
            self.history.pop()

    def _state_block(self, directive: Directive) -> str:
        lines = ["CURRENT STATE"]
        for spec in FIELDS:
            state = self.flow.fields[spec.key]
            value = state.value if state.value else "(not collected)"
            lines.append(f"- {spec.key}: {value} [{state.status}]")
        if self.flow.pending:
            lines.append(f"Waiting for the caller to confirm the readback of: {self.flow.pending.target}")
        lines.append("")
        lines.append(f"CURRENT DIRECTIVE: {directive.instruction}")
        return "\n".join(lines)

    def _snapshot(self, reason: str) -> None:
        self.recorder.event(self.call_sid, "snapshot", {"reason": reason, **self.flow.snapshot()})
