"""Speech-to-text engines.

An engine turns a stream of raw audio into finished utterances. The transcriber owns
everything else: capturing audio, reconnecting, echo removal, and writing the file.

To add an engine, create `engines/<name>.py` with a class called `Engine` that has:

    name: str
        Shown in the log, e.g. "Deepgram".

    def __init__(self) -> None
        Read config (API keys, model names) from environment variables and raise
        SystemExit with a clear message if something is missing. `start` builds the
        engine once up front, so a bad setup fails in the foreground, not silently in
        the background.

    def connect(self, rate: int, diarize: bool) -> AsyncContextManager[Session]
        Open one session. `rate` is the sample rate of the audio you will be sent
        (16-bit signed little-endian mono PCM). `diarize` asks for speaker numbers
        (only the call side asks for it; return speaker 0 if you can't).

and a Session with:

    async def send(self, pcm: bytes) -> None
        About 100 ms of audio per call, in real time.

    def results(self) -> AsyncIterator[Utterance]
        Yield an Utterance per finished phrase. Return or raise when the session dies;
        the transcriber reconnects with backoff.

Then run with `--engine <name>`. See `deepgram.py` for a complete example.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Utterance:
    text: str
    started: float  # wall-clock time.time() when the phrase began
    speaker: int = 0  # 0, 1, 2... on the diarized side; always 0 otherwise


def available() -> list[str]:
    here = Path(__file__).parent
    return sorted(p.stem for p in here.glob("*.py") if not p.stem.startswith("_"))


def load(name: str):
    """Build the named engine. Raises SystemExit if it is unknown or misconfigured."""
    if name not in available():
        raise SystemExit(f"unknown engine {name!r}; available: {', '.join(available())}")
    return importlib.import_module(f"engines.{name}").Engine()
