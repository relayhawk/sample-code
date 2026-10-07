"""Deepgram streaming speech-to-text (nova-3 by default).

Needs DEEPGRAM_API_KEY. Optional: DEEPGRAM_MODEL (default nova-3).
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
from contextlib import asynccontextmanager

import websockets

from engines import Utterance


class Engine:
    name = "Deepgram"

    def __init__(self) -> None:
        self.key = os.environ.get("DEEPGRAM_API_KEY")
        if not self.key:
            raise SystemExit("DEEPGRAM_API_KEY is not set (environment or .env)")
        self.model = os.environ.get("DEEPGRAM_MODEL", "nova-3")

    @asynccontextmanager
    async def connect(self, rate: int, diarize: bool):
        params = {
            "model": self.model,
            "encoding": "linear16",
            "sample_rate": str(rate),
            "channels": "1",
            "diarize": "true" if diarize else "false",
            "smart_format": "true",
            "punctuate": "true",
            "endpointing": "300",  # ms of silence that ends a phrase
            "interim_results": "false",
        }
        url = "wss://api.deepgram.com/v1/listen?" + urllib.parse.urlencode(params)
        async with websockets.connect(
            url, additional_headers={"Authorization": f"Token {self.key}"},
            ping_interval=10, ping_timeout=20, max_size=None,
        ) as ws:
            yield Session(ws)


class Session:
    def __init__(self, ws) -> None:
        self.ws = ws

    async def send(self, pcm: bytes) -> None:
        await self.ws.send(pcm)

    async def results(self):
        async for raw in self.ws:
            if isinstance(raw, str):
                for utterance in parse(json.loads(raw)):
                    yield utterance


def parse(msg: dict) -> list[Utterance]:
    """One final result -> one Utterance per run of words by the same speaker."""
    if msg.get("type") != "Results" or not msg.get("is_final"):
        return []
    alt = (msg.get("channel", {}).get("alternatives") or [{}])[0]
    words = alt.get("words") or []
    if not alt.get("transcript", "").strip() or not words:
        return []
    runs: list[list] = []  # [speaker, start offset, words]
    for w in words:
        spk = w.get("speaker", 0)
        text = w.get("punctuated_word") or w.get("word", "")
        if runs and runs[-1][0] == spk:
            runs[-1][2].append(text)
        else:
            runs.append([spk, w.get("start", 0.0), [text]])
    # Stamp from arrival time, not audio offset: offsets drift if a capture lags or
    # bursts, which would skew the two sides apart and defeat the echo check.
    end = msg.get("start", 0.0) + msg.get("duration", 0.0)
    received = time.time()
    return [Utterance(" ".join(texts), received - (end - start), spk) for spk, start, texts in runs]
