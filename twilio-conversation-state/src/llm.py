"""
A thin streaming LLM interface.

The agent only needs two things from a model: text tokens as they arrive (so
ConversationRelay can start speaking early) and any tool calls at the end. Keeping
that behind a tiny protocol lets the tests swap in a scripted fake model.
"""

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

from openai import AsyncOpenAI


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCalls:
    calls: list[ToolCall]


StreamItem = TextDelta | ToolCalls


class LLM(Protocol):
    model: str

    def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: str
    ) -> AsyncIterator[StreamItem]: ...


class OpenAILLM:
    def __init__(self, api_key: str | None, model: str):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: str
    ) -> AsyncIterator[StreamItem]:
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            tools=tools,  # type: ignore[arg-type]
            tool_choice=tool_choice,  # type: ignore[arg-type]
            temperature=0.3,
            max_tokens=300,
            stream=True,
        )
        # Tool call deltas arrive in pieces keyed by index; stitch them together.
        partial: dict[int, dict[str, str]] = {}
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield TextDelta(delta.content)
            for tc in delta.tool_calls or []:
                slot = partial.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments
        if partial:
            calls = []
            for slot in (partial[i] for i in sorted(partial)):
                try:
                    args = json.loads(slot["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append(ToolCall(id=slot["id"], name=slot["name"], arguments=args))
            yield ToolCalls(calls)
