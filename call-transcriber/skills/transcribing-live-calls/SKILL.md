---
name: transcribing-live-calls
description: Runs the local live call transcriber (call_transcriber.py) that writes a Me/Them transcript of a Zoom, Meet, Teams or phone call on this Mac to a new file per call under ~/call-transcripts/, and keeps a doc of decisions, open questions and follow-ups updated from it while the call is happening. Use whenever the user wants to transcribe a call live, start or stop the transcriber, take live notes or update a doc during a meeting, watch the call transcript, or asks why the transcriber is silent, echoing, or mislabeling speakers. Not for analyzing a finished recording afterwards.
---

# Transcribing live calls

The transcriber captures the mic as `Me` and the Mac's audio output (the call) as
`Them`, streams both to a speech-to-text engine, and appends one line per finished
utterance:

    [13:47:41] Them: Hello. Our next meeting is October 12 at 10 AM.

Lines land about 2.5 s after they are spoken (they wait for an echo check). Setup,
flags and limits are in the tool's `README.md`.

## Finding the script

This skill is installed as a symlink into the tool's folder. Resolve it once and use
the absolute path for every command below:

    CT="$(realpath "<this skill's base directory>/../../call_transcriber.py")"
    uv run "$CT" status

If that file does not exist, the skill was copied instead of linked: ask the user
where they cloned the call transcriber.

## Commands

    uv run "$CT" start --name <short-call-label>
    uv run "$CT" status | stop

`start` runs it in the background, builds the Swift tap helper on first use, and
prints the new transcript's path, e.g. `~/call-transcripts/2026-10-07_1347_acme.txt`.
Every call gets its own file, so earlier calls are never touched. `latest.txt` points
at the newest; the log is `~/call-transcripts/.state/transcriber.log`.

## Before a call

1. `status`. If something is running from an earlier call, ask before stopping it.
2. `start --name <label>` (a customer or meeting name the user gave, if any). Note the
   transcript path it prints: everything below watches that exact file.
3. Read the log after ~3 s: `Me: connected` and `Them: connected` should both be there.
   `start` exits with a message if the engine's API key is missing; relay it.
4. If this is the first run on this Mac or terminal, check the `Them` side hears
   anything: run `say "transcriber check"` and confirm a `Them:` line appears. A
   silent `Them` side means the terminal app lacks **System Audio Recording Only**
   permission (README, Setup). macOS shows no error in that case.

## Watching the transcript

Watch the file and the log together, with a background monitor if you have one.
Including failures matters: a crashed or reconnecting transcriber otherwise looks the
same as a quiet call.

    tail -n 0 -F <transcript path> ~/call-transcripts/.state/transcriber.log 2>&1 \
      | grep --line-buffered -E '^\[|session ended|Traceback|Error|stopped'

If the monitor expires, re-arm it for longer calls. `session ended` followed by
`connected` is the auto-reconnect working; repeated `capture stalled` lines mean a
capture keeps freezing, so tell the user.

## Updating a doc live

When the user wants notes kept live, use the doc they name or create a new one, never
another doc:

- Write when a meaningful point lands, or batch about every 30 s. One edit per batch
  keeps the doc readable as it changes.
- Quote the speaker label and timestamp on every bullet, e.g.
  `Next meeting is October 12 at 10 AM. (Them, 13:34:41)`. The user checks notes
  against the transcript, so the label and time have to match the line exactly.
- Mark an open question answered by appending `(answered: …)` to it rather than
  rewriting it, so the original question stays visible.
- Add `Ask:` follow-ups only for gaps the transcript actually shows (a time with no
  place, a number with no owner).
- Never add content the transcript does not contain. If a line is garbled, quote it
  as transcribed rather than guessing what was meant.

## After the call

`stop`, then confirm with `pgrep -fl 'call_transcriber|audiotap'` that nothing is
left. Tell the user the transcript's path; it stays as the record of the call.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| No `Them:` lines, log looks healthy | Terminal app lacks System Audio Recording permission. The grant belongs to the app that launched the command; start from that app. |
| The same sentence appears as both `Me` and `Them` | Echo check missed it (very late or garbled echo). Harmless for notes: quote the `Them` line. Headphones remove it entirely. |
| A `Me` line the user said is missing | They repeated the other side's words, so it matched as echo. The `Them` original carries the content. |
| `Me: session ended (capture stalled)` repeatedly | The mic stopped delivering audio. Another app may have taken it; ask the user, try `--device` with a name from `ffmpeg -f avfoundation -list_devices true -i ""`. |
| Notification sounds or music transcribed as `Them` | The tap records all Mac output. Restart with `--tap-app us.zoom` (or the call app's bundle ID). |
| `Them: audiotap: waiting for an app matching …` | `--tap-app` was given and that app is not playing audio yet. It starts once the call does. |
