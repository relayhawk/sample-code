# Call Transcriber: Live Transcripts of Both Sides of a Call on macOS

A small command-line tool that writes a live transcript of your Zoom, Meet, Teams or softphone call to a text file, one line per finished sentence, labelled by side:

```
[13:47:27] Me: Good morning. This is Sam from the product team.
[13:47:41] Them: Hello. Our next meeting is October 12 at 10 AM.
[13:47:52] Them 2: And the number to call is (555) 010-0199.
```

No bot joins the meeting and nothing is installed into the call app. `Me` is your microphone. `Them` is whatever your Mac plays, captured straight from the audio output, so it works on speakers or headphones and with any call app.

It was built so [Claude Code](https://claude.com/claude-code) can watch the transcript during a call and keep a notes doc of decisions, open questions and follow-ups up to date while you talk. The [skill](#use-it-with-claude-code) included here teaches Claude how to do that.

## Why Split by Side Instead of Diarization?

A single mixed recording has to guess who is speaking, and those guesses swap mid-call. This tool records the two sides as separate audio streams, so `Me` and `Them` are always right. Speaker diarization runs only on the far side, where it splits several remote voices into `Them`, `Them 2`, and so on.

## Requirements

- macOS 14.2 or later (for Core Audio process taps).
- [uv](https://docs.astral.sh/uv/) to run the script and its one dependency.
- `ffmpeg`: `brew install ffmpeg`.
- The Swift compiler from the Xcode command line tools: `xcode-select --install`. The audio tap helper builds itself on first `start` into `audiotap/build/`.
- A [Deepgram](https://deepgram.com) API key for the default engine, or [your own engine](#adding-a-speech-to-text-engine).

## Setup

1. Put your key in `.env` next to the script (it is git-ignored), or export it:

   ```bash
   echo 'DEEPGRAM_API_KEY=your-key' > .env
   ```

2. Grant your terminal app two permissions in **System Settings → Privacy & Security**:
   - **Microphone**, for the `Me` side.
   - **Screen & System Audio Recording → System Audio Recording Only**, for the `Them` side.

   The grant belongs to the app you run the command from (Terminal, iTerm, VS Code, Cursor), so start the tool from that app. **Without the second grant, the `Them` side records silence and macOS shows no error.**

3. Check both sides work:

   ```bash
   uv run call_transcriber.py start --name test
   say "Transcriber check"          # should appear as a Them line
   uv run call_transcriber.py status
   uv run call_transcriber.py stop
   ```

## Use

```bash
uv run call_transcriber.py start --name acme-discovery   # in the background
uv run call_transcriber.py status                        # running? last lines
uv run call_transcriber.py stop
uv run call_transcriber.py run                           # foreground; Ctrl-C stops
```

Every `start` writes a new file, such as `~/call-transcripts/2026-10-07_1347_acme-discovery.txt` (`--name` is optional), so one call never appends to or overwrites another. `~/call-transcripts/latest.txt` always points at the newest, so `tail -f ~/call-transcripts/latest.txt` follows the current call. The log is `~/call-transcripts/.state/transcriber.log`.

| Flag | What it does |
|---|---|
| `--name` | A label added to the transcript's file name. |
| `--out-dir` | Where transcripts go. Default `~/call-transcripts`. |
| `--device` | The microphone, by name. Default is the system input. List them with `ffmpeg -f avfoundation -list_devices true -i ""`. |
| `--tap-app` | Capture only one app's audio, by bundle ID prefix, such as `us.zoom`. Default is everything the Mac plays, notification sounds included. |
| `--mic-only` | Skip the call audio. |
| `--engine` | The speech-to-text engine. Default `deepgram`. |

The transcript folder is in your home directory on purpose. Inside a repo, call content could get committed. Under `~/Documents`, iCloud Desktop & Documents sync would upload it. The tool makes the folder readable by your account only.

## Use It with Claude Code

The [`transcribing-live-calls`](skills/transcribing-live-calls/SKILL.md) skill teaches Claude Code to start and stop the transcriber, watch the transcript as it grows, and keep a notes doc updated during the call. It quotes the speaker and time for every note, marks questions as answered, and never adds anything the transcript doesn't say.

Install it by linking it into your skills folder. A link rather than a copy lets the skill find the script:

```bash
ln -s "$PWD/skills/transcribing-live-calls" ~/.claude/skills/transcribing-live-calls
```

Then, before a call, tell Claude something like:

> Start transcribing my call with Acme and keep live notes in a new doc: decisions, open questions, and follow-ups. Our open questions are when the next meeting is and who owns the next step.

Claude starts the transcriber, watches the file, and updates the doc as answers come up. Any doc tool Claude can use works: a Claude doc, Google Docs through a connector, or a local Markdown file.

## How It Works

```
mic ──ffmpeg──► Me stream ───► engine ──┐
                                        ├─► echo check + time order ─► transcript file
Mac output ──audiotap──► Them stream ─► engine ──┘
```

- `audiotap/main.swift` creates a Core Audio process tap on the Mac's output and streams it as 16-bit mono PCM. The tap sends nothing while the Mac is silent, so it pads silence in real time to keep the stream steady.
- Each side has its own engine session and its own reconnect loop, with backoff from 1 to 30 seconds. A capture that sends no audio for 5 seconds is restarted.
- On speakers the mic also hears the far side. A `Me` line whose words mostly repeat a `Them` line within 6 seconds is dropped as echo. To allow that check, every line waits 2.5 seconds before it is written.
- Timestamps come from when each result arrives, not from counting audio, because the mic capture can lag and the two sides would drift apart.
- API keys are never printed or logged. Errors are logged by type only, so a request header can't leak into the log.

## Adding a Speech-to-Text Engine

Engines live in [`engines/`](engines/). Each is one file with an `Engine` class, and the transcriber handles everything else: capture, reconnects, echo removal and the file. [`engines/deepgram.py`](engines/deepgram.py) is a complete example, and [`engines/__init__.py`](engines/__init__.py) documents the interface:

```python
class Engine:
    name = "My Engine"

    def __init__(self):              # read keys/config from env; SystemExit if missing
        ...

    @asynccontextmanager
    async def connect(self, rate: int, diarize: bool):
        yield Session(...)           # one session; reconnects open a new one

class Session:
    async def send(self, pcm: bytes): ...      # ~100 ms of 16-bit mono PCM at `rate`
    async def results(self):                   # yield engines.Utterance(text, started, speaker)
        ...
```

Then run with `--engine <file name>`. If your engine needs packages beyond `websockets`, add them with `uv run --with <package> call_transcriber.py start --engine <name>` or to the script's inline dependency header.

**Example: local Whisper.** A local engine (for example with `faster-whisper` or `mlx-whisper`) keeps audio on your Mac. `send` appends PCM to a buffer. `results` waits for a pause in speech, using a voice activity detector or a simple energy threshold, transcribes the buffered phrase in a worker thread so the event loop keeps receiving audio, and yields one `Utterance` per phrase with `started` set to the wall-clock time the phrase began. Whisper has no streaming diarization, so return speaker `0`. Contributions are welcome.

## Known Limits

- If you read the other side's words back ("so that's 555-010-0199?"), your line can be dropped as echo. Their original line is kept. Headphones avoid echo entirely.
- Without `--tap-app`, `Them` includes every sound the Mac plays.
- Diarization labels on the `Them` side are a best guess and can swap mid-call.
- macOS only. The `Them` side depends on Core Audio process taps.

## Recording Consent

Recording or transcribing a call can require the consent of everyone on it, depending on where you and they are. Tell the other side you are transcribing, and check the rules that apply to you.
