# Kivu

A terminal coding agent you bring your own model key to. It runs its own agent loop, tool registry and sandbox instead of relying on a vendor SDK's built-in function calling.

**Status:** v2 is being rewritten from scratch, one module at a time. The earlier Gemini-only agent is at tag [`v1.0`](https://github.com/ABHISHEKBARNWAL1301/kivu/tree/v1.0).

## Layout

```text
kivu/
  cli.py              entry point, REPL, one-shot mode
  config.py           provider, model, API key source, limits
  messages.py         provider-neutral message and tool-call types
  providers/          one adapter per LLM vendor, behind base.py
  agent/loop.py       the agent loop with limits
  agent/context.py    what goes into each model call
  tools/              registry plus file and shell tools
  sandbox/            path and command policy, user approval
  session.py          save and resume conversations
  tracing.py          one trace per run
  ui.py               terminal rendering
tests/
```

## Build order

1. `messages.py`: the shared types
2. `providers/base.py` + one provider
3. `tools/registry.py` + `tools/files.py`
4. `agent/loop.py`: first end-to-end run
5. `cli.py` + `ui.py`
6. `sandbox/` + `tools/shell.py`
7. `agent/context.py`, `session.py`, `tracing.py`

## Setup

```bash
conda create -n kivu python=3.12 -y
conda activate kivu
pip install -e .
```

Put your API keys in `keys.json` in the repo root. It's gitignored.

```json
{
  "gemini": "your-gemini-key"
}
```

Pick a provider and model, then start:

```bash
kivu setup
kivu
```

Your choice is saved to `~/.config/kivu/config.json`. Run `kivu setup` again to switch, or override once with `kivu -m <model>`. Run one prompt and exit with `kivu -p "..."`.

## Tests

```bash
python -m unittest discover tests
```
