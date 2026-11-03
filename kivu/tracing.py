"""One trace per run, with a span for each model call and tool call.

To build:
- run id, outcome, stop reason, total time, tokens and cost
- per span: name, duration, status, token counts
- write traces somewhere you can read back (JSON lines file to start)
- never log API keys
"""
