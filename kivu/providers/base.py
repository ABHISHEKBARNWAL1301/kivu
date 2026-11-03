"""The interface every provider implements.

To build:
- complete(messages, tools) -> assistant message + usage
- convert tool schemas to the vendor's format
- map vendor errors to a small set kivu understands (auth, rate limit, server, bad request)
- optional: streaming
"""
