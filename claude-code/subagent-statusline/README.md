# Subagent Status Line: Show the Model of Each Subagent

Claude Code's default subagent status line already shows the task description and token usage — but not **which model** each subagent is running on. This small custom status line script adds the model to the line.

![Subagent status line showing the model of the subagent](screenshot.png)

## Why This Matters

When your main session runs an expensive model like Fable 5, subagents inherit that model unless you (or Claude) say otherwise. That means a Fable session can quietly delegate work to *another Fable agent* — and burn through your token limits much faster than you expected.

With the model in the status line, you can see at a glance when a subagent is running on a pricier model than the task needs, and interrupt or redirect before it eats your usage. In the screenshot above, the subagent is confirmed to be running `claude-haiku-4-5-20251001` — a cheap model for a cheap task.

## What You Get

Each subagent's status line will show:

```
local_agent · claude-haiku-4-5-20251001 · Echo, sleep, done message · ↓ 28.8k tokens
```

The **model** is the new piece of information; the type, description, and token count are also included so the line stays a full replacement for the default.

## Setup

**Requirements:** [`jq`](https://jqlang.github.io/jq/) must be installed (`brew install jq` on macOS).

### 1. Add the script

Copy [`subagent-statusline.sh`](subagent-statusline.sh) to `~/.claude/subagent-statusline.sh` and make it executable:

```bash
cp subagent-statusline.sh ~/.claude/subagent-statusline.sh
chmod +x ~/.claude/subagent-statusline.sh
```

### 2. Configure Claude Code

Add this key to your `~/.claude/settings.json`:

```json
  "subagentStatusLine": {
    "type": "command",
    "command": "~/.claude/subagent-statusline.sh"
  },
```

### 3. Try it out

Start a new Claude Code session and ask it to spawn a subagent, for example:

```
Create a sub-agent that echoes "hello world", sleeps for 5 seconds, and then
says "I'm done waiting". This subagent should be run with a haiku model.
```

While the subagent runs, you'll see its model in the status line at the bottom of your terminal.

## How It Works

Claude Code pipes JSON describing the running subagent tasks to the command configured in `subagentStatusLine`. The script uses `jq` to transform each task into a `{id, content}` object, where `content` is the text rendered in the status line. That means you can customize the format however you like — reorder fields, add emoji, drop the token count, etc.
