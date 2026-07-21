# Claude Code Customizations

A collection of small, practical customizations for [Claude Code](https://claude.com/claude-code) — Anthropic's agentic coding tool. Each subdirectory is a self-contained tip you can drop into your own setup in a couple of minutes.

## Customizations

### [Subagent Status Line: Show the Model of Each Subagent](subagent-statusline/)

Add the model name (e.g., `claude-haiku-4-5-20251001`) to the subagent status line. The default status line already shows the task and token count — adding the model lets you catch an expensive model (like Fable 5) delegating to another expensive agent before it eats your token limits.

---

More customizations coming soon — we use Claude Code heavily at [Relay Hawk](https://www.relayhawk.com) and will share the tweaks we find useful.
