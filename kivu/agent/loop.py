"""The agent loop.

    while not done:
        response = provider.complete(context, tools)
        append response to history
        if response has tool calls:
            results = run each call through sandbox -> tool
            append results to history
        else:
            return response

To build:
- step, time and cost limits
- loop detection (same tool + same args repeated)
- cancellation between steps
- retry with backoff on provider errors (429 / 5xx)
"""
