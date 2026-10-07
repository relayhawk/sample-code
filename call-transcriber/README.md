# Call Transcriber: Live Transcripts of Both Sides of a Call on macOS

Put [Claude Code](https://claude.com/claude-code) on your calls. This tool streams a live transcript of both sides of a Zoom, Meet or Teams call to a file. Claude reads it as the call happens and does the work behind the scenes: it looks things up, answers questions, sets things up and keeps your notes, so you don't have to say "let me get back to you."

## What That Looks Like

You're on a sales call. The prospect asks two technical questions you can't answer from memory:

```
[14:02:11] Them: Before we go further, do you integrate with Salesforce?
           We'd need call notes on the contact record.
[14:02:19] Me: Let me check on that for you.
[14:02:31] Them 2: And is there a cap on API calls? We'd push about 5,000 a day.
```

While you keep talking, Claude, running in your product's repo:

- searches the integrations code and finds that the Salesforce sync writes call notes to the contact's activity history,
- finds the rate limit in the API config: 10,000 requests a day on the standard plan,
- puts both answers in your notes doc, with links to the files it checked,
- adds a follow-up to raise before the call ends: *Ask: who owns the Salesforce admin side?*

Thirty seconds later, you answer, and the prospect asks for more:

```
[14:02:58] Me: Good news, yes to both. Notes land on the Salesforce contact,
           and you're well under the 10,000-a-day limit.
[14:03:10] Them: Great. Could we try that with our team this week?
[14:03:14] Me: Absolutely. Let me get that going while we talk.
```

Before the call you told Claude it could set up trial accounts. It has your product's admin API, so while you walk through pricing it:

- creates a trial workspace for the prospect's company,
- turns on the Salesforce integration and puts the workspace on the standard plan, which covers their 5,000 calls a day,
- sends an invite to the email the prospect gave earlier in the call,
- adds what it did to the notes doc: *Done: trial workspace created, Salesforce on, standard plan, invite sent.*

```
[14:05:40] Me: You should have an invite in your inbox now. Your workspace
           is already set up, with Salesforce turned on and room for your
           5,000 calls a day.
[14:05:47] Them: Oh wow, it's there. That was fast.
```

The prospect sees the setup finished before the call ends, and you never touched the keyboard.

That example is illustrative. What Claude can do depends on what you give it access to: a codebase, docs, your product's API or admin interface, a CRM or logs through MCP servers. It only takes actions like creating accounts if you allowed them before the call. The [skill](#use-it-with-claude-code) included here teaches it to watch the call and keep a notes doc of decisions, open questions and follow-ups. It quotes the speaker and time for every note, and labels anything it looked up with the source, so you can tell what was said from what Claude found.

## The Transcriber

One line per finished sentence, labelled by side. No bot joins the meeting and nothing is installed into the call app. `Me` is your microphone. `Them` is whatever your Mac plays, captured straight from the audio output, so it works on speakers or headphones and with any call app.

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

> Start transcribing my call with Acme and keep live notes in a new doc. When they ask a product question, look up the answer in this repo and put it in the doc. If they want to try it, you can create a trial workspace with the admin API. Our open questions are pricing for 40 seats and who signs off on budget.

Claude starts the transcriber, watches the file, answers questions and handles the setup you allowed as they come up, and updates the doc. Any doc tool Claude can use works: a Claude doc, Google Docs through a connector, or a local Markdown file.

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
