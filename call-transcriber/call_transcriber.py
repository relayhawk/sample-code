#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "websockets>=13",
# ]
# ///
"""Live call transcriber for macOS: your mic + the call audio -> a text file.

Two independent streams, each with its own reconnect loop:
  Me   - your microphone, captured by ffmpeg
  Them - whatever the Mac plays (the Zoom, Meet or Teams call), captured by the
         audiotap helper with a Core Audio process tap

On laptop speakers the mic also hears the far side, so a Me line that repeats a
nearby Them line is dropped as echo. Lines are held briefly so that check can run
and so both sides land in time order.

    uv run call_transcriber.py start --name acme-discovery
    uv run call_transcriber.py status
    uv run call_transcriber.py stop
    uv run call_transcriber.py run      # foreground, Ctrl-C to stop

Every start writes a new transcript, ~/call-transcripts/<date>_<time>[_<name>].txt,
so no call is ever appended to or overwritten by another. latest.txt points at the
newest one.

Speech-to-text is pluggable: see engines/__init__.py. API keys come from the
environment or a .env file next to this script, and are never printed or logged.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import difflib
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

import engines  # noqa: E402

TAP_SRC = APP_DIR / "audiotap"
TAP_BIN = TAP_SRC / "build" / "audiotap"
# In the home folder, not a repo (call content could get committed) and not
# ~/Documents (iCloud Desktop & Documents sync would upload it).
DEFAULT_OUT_DIR = Path.home() / "call-transcripts"
DEFAULT_DEVICE = "default"  # the system input device; see `ffmpeg -f avfoundation -list_devices true -i ""`
DEFAULT_ENGINE = "deepgram"
MIC_RATE = 16000
HOLD = 2.5  # seconds a line waits for the echo check and time ordering
ECHO_WINDOW = 6.0  # seconds either side of a Me line to look for the Them original
ECHO_RATIO = 0.6  # share of a Me line's words found in nearby Them speech
STALL = 5  # seconds without audio before a capture is restarted


def log(msg: str) -> None:
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def load_dotenv() -> None:
    """Fill unset environment variables from .env next to this script."""
    path = APP_DIR / ".env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def build_tap() -> Path:
    """Compile the Swift tap helper on first use, or when its source changed."""
    src = TAP_SRC / "main.swift"
    newest_src = max(src.stat().st_mtime, (TAP_SRC / "Info.plist").stat().st_mtime)
    if TAP_BIN.exists() and TAP_BIN.stat().st_mtime >= newest_src:
        return TAP_BIN
    if not shutil.which("swiftc"):
        sys.exit("swiftc not found: install the Xcode command line tools (xcode-select --install)")
    TAP_BIN.parent.mkdir(exist_ok=True)
    print("Building the audio tap helper (one time)...", flush=True)
    subprocess.run(
        ["swiftc", "-O", str(src), "-o", str(TAP_BIN),
         # Embeds Info.plist, which macOS needs before it allows system audio capture.
         "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist",
         "-Xlinker", str(TAP_SRC / "Info.plist")],
        check=True,
    )
    return TAP_BIN


def words_of(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


class Writer:
    """Holds lines for HOLD seconds, drops Me lines that echo Them, writes in time order."""

    def __init__(self, out):
        self.out = out
        self.pending: list[tuple[float, float, str, str]] = []  # (release_at, ts, label, text)
        self.them: list[tuple[float, list[str]]] = []  # (ts, words) of recent Them lines

    def add(self, label: str, ts: float, text: str) -> None:
        if label.startswith("Them"):
            self.them.append((ts, words_of(text)))
        self.pending.append((time.time() + HOLD, ts, label, text))

    def is_echo(self, ts: float, text: str) -> bool:
        mine = words_of(text)
        if not mine:
            return False
        near = [w for t, ws in self.them if abs(t - ts) <= ECHO_WINDOW for w in ws]
        if not near:
            return False
        sm = difflib.SequenceMatcher(None, mine, near, autojunk=False)
        matched = sum(b.size for b in sm.get_matching_blocks())
        return matched / len(mine) >= ECHO_RATIO

    def flush(self, force: bool = False) -> None:
        now = time.time()
        due = [p for p in self.pending if force or p[0] <= now]
        if not due:
            return
        self.pending = [p for p in self.pending if p not in due]
        for _, ts, label, text in sorted(due, key=lambda p: p[1]):
            if label == "Me" and self.is_echo(ts, text):
                log("dropped a Me line as speaker echo")
                continue
            stamp = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            self.out.write(f"[{stamp}] {label}: {text}\n")
        self.out.flush()
        os.fsync(self.out.fileno())
        self.them = [(t, w) for t, w in self.them if now - t < 60]


class Stream:
    """One side of the call: a capture process feeding one engine session at a time."""

    def __init__(self, label: str, rate: int | None, diarize: bool, cmd: list[str], engine):
        self.label = label
        self.fixed_rate = rate  # None: read it from the capture's "streaming rate=N" line
        self.rate = rate or 0
        self.diarize = diarize
        self.cmd = cmd
        self.engine = engine
        self.proc: asyncio.subprocess.Process | None = None

    @property
    def chunk(self) -> int:
        return self.rate // 10 * 2  # 100 ms of 16-bit mono

    async def spawn(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.cmd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL if self.fixed_rate else asyncio.subprocess.PIPE)
        log(f"{self.label}: capture started (pid {self.proc.pid})")
        if self.fixed_rate:
            return
        # The tap streams at the output device's rate, which can differ per Mac and
        # change when headphones are plugged in, so take it from the helper each time.
        while line := (await self.proc.stderr.readline()).decode(errors="replace"):
            log(f"{self.label}: {line.strip()}")
            if m := re.search(r"streaming rate=(\d+)", line):
                self.rate = int(m.group(1))
                asyncio.ensure_future(self.drain(self.proc))
                return
        await self.proc.wait()
        self.proc = None
        raise RuntimeError("capture failed to start")

    async def drain(self, proc: asyncio.subprocess.Process) -> None:
        while line := await proc.stderr.readline():
            log(f"{self.label}: {line.decode(errors='replace').strip()}")

    def speaker_label(self, spk: int) -> str:
        if self.label == "Them" and spk:
            return f"Them {spk + 1}"
        return self.label

    async def session(self, writer: Writer) -> None:
        async with self.engine.connect(self.rate, self.diarize) as session:
            log(f"{self.label}: connected to {self.engine.name}")

            async def sender():
                while True:
                    try:
                        data = await asyncio.wait_for(self.proc.stdout.read(self.chunk), STALL)
                    except asyncio.TimeoutError:
                        raise RuntimeError("capture stalled")
                    if not data:
                        raise RuntimeError("capture stopped producing audio")
                    await session.send(data)

            async def receiver():
                async for u in session.results():
                    writer.add(self.speaker_label(u.speaker), u.started, u.text)

            tasks = [asyncio.ensure_future(sender()), asyncio.ensure_future(receiver())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            for t in done:
                if not t.cancelled() and t.exception():
                    raise t.exception()
            raise RuntimeError("session closed")

    async def run(self, writer: Writer, stop: asyncio.Event) -> None:
        backoff = 1
        while not stop.is_set():
            if self.proc is None or self.proc.returncode is not None:
                try:
                    await self.spawn()
                except RuntimeError as err:
                    log(f"{self.label}: {err}; retrying in {backoff}s")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                    continue
            began = time.time()
            session = asyncio.ensure_future(self.session(writer))
            stopper = asyncio.ensure_future(stop.wait())
            await asyncio.wait([session, stopper], return_when=asyncio.FIRST_COMPLETED)
            if stop.is_set():
                session.cancel()
                await asyncio.gather(session, return_exceptions=True)
                break
            stopper.cancel()
            err = session.exception()
            if time.time() - began > 60:
                backoff = 1
            # Log only our own reasons or the exception type, never library text,
            # so a request header (and the API key in it) can never reach the log.
            reason = str(err) if type(err) is RuntimeError else type(err).__name__
            log(f"{self.label}: session ended ({reason}); reconnecting in {backoff}s")
            if type(err) is RuntimeError and "capture" in str(err):
                if self.proc.returncode is None:
                    self.proc.kill()
                await self.proc.wait()
                self.proc = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
        await self.close()

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                self.proc.kill()


async def transcribe(args: argparse.Namespace) -> None:
    engine = engines.load(args.engine)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ffmpeg not found: brew install ffmpeg")
    streams = [Stream("Me", MIC_RATE, diarize=False, engine=engine, cmd=[
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "avfoundation", "-i", f":{args.device}",
        "-ac", "1", "-ar", str(MIC_RATE), "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"])]
    if not args.mic_only:
        cmd = [str(build_tap())] + (["--app", args.tap_app] if args.tap_app else [])
        streams.append(Stream("Them", None, diarize=True, engine=engine, cmd=cmd))

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    transcript = args.transcript or new_transcript(args.out_dir, args.name)
    log(f"transcript: {transcript} (engine: {engine.name})")
    with open(transcript, "a", buffering=1) as out:
        writer = Writer(out)

        async def flusher():
            while not stop.is_set():
                writer.flush()
                await asyncio.sleep(0.25)

        await asyncio.gather(flusher(), *(s.run(writer, stop) for s in streams))
        writer.flush(force=True)
    log("stopped")


def state_dir(out_dir: Path) -> Path:
    path = out_dir / ".state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_transcript(out_dir: Path, name: str | None) -> Path:
    """A fresh file for this call; latest.txt is repointed at it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o700)  # call content: readable by this account only
    slug = "_" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") if name else ""
    now = datetime.datetime.now()
    path = out_dir / f"{now:%Y-%m-%d_%H%M}{slug}.txt"
    if path.exists():  # two calls started in the same minute
        path = out_dir / f"{now:%Y-%m-%d_%H%M%S}{slug}.txt"
    path.touch()
    latest = out_dir / "latest.txt"
    latest.unlink(missing_ok=True)
    latest.symlink_to(path.name)
    return path


def running_pid(out_dir: Path) -> int | None:
    pidfile = state_dir(out_dir) / "transcriber.pid"
    try:
        pid = int(pidfile.read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def cmd_start(args: argparse.Namespace) -> None:
    if pid := running_pid(args.out_dir):
        print(f"already running (pid {pid})")
        return
    engines.load(args.engine)  # fail here on missing keys, not silently in the background
    if not args.mic_only:
        build_tap()
    transcript = new_transcript(args.out_dir, args.name)
    cmd = [sys.executable, str(Path(__file__).resolve()), "run", "--engine", args.engine,
           "--out-dir", str(args.out_dir), "--device", args.device, "--transcript", str(transcript)]
    if args.tap_app:
        cmd += ["--tap-app", args.tap_app]
    if args.mic_only:
        cmd.append("--mic-only")
    logfile = state_dir(args.out_dir) / "transcriber.log"
    with open(logfile, "a") as logf:
        proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    (state_dir(args.out_dir) / "transcriber.pid").write_text(str(proc.pid))
    print(f"started (pid {proc.pid})")
    print(f"transcript: {transcript}")
    print(f"log:        {logfile}")


def cmd_stop(args: argparse.Namespace) -> None:
    pid = running_pid(args.out_dir)
    if not pid:
        print("not running")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        time.sleep(0.1)
        if running_pid(args.out_dir) is None:
            break
    else:
        os.killpg(pid, signal.SIGKILL)  # its own session: takes ffmpeg and the tap too
    (state_dir(args.out_dir) / "transcriber.pid").unlink(missing_ok=True)
    print("stopped")


def cmd_status(args: argparse.Namespace) -> None:
    pid = running_pid(args.out_dir)
    print(f"running (pid {pid})" if pid else "not running")
    latest = args.out_dir / "latest.txt"
    if latest.exists():
        transcript = latest.resolve()
        lines = transcript.read_text().splitlines()
        print(f"transcript: {transcript} ({len(lines)} lines)")
        for line in lines[-3:]:
            print(f"  {line}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["start", "stop", "status", "run"])
    parser.add_argument("--name", help="short label for the call, added to the transcript's file name")
    parser.add_argument("--engine", default=DEFAULT_ENGINE, choices=engines.available(),
                        help=f"speech-to-text engine (default {DEFAULT_ENGINE})")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                        help=f"where transcripts go (default {DEFAULT_OUT_DIR})")
    parser.add_argument("--transcript", type=Path, help=argparse.SUPPRESS)  # set by start for run
    parser.add_argument("--device", default=DEFAULT_DEVICE, help=f"mic name (default {DEFAULT_DEVICE!r})")
    parser.add_argument("--tap-app", help="only capture this app's audio, by bundle id prefix (e.g. us.zoom)")
    parser.add_argument("--mic-only", action="store_true", help="skip the call-audio tap; mic only")
    args = parser.parse_args()
    args.out_dir = args.out_dir.expanduser()
    load_dotenv()
    if args.command == "run":
        asyncio.run(transcribe(args))
    else:
        {"start": cmd_start, "stop": cmd_stop, "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    main()
