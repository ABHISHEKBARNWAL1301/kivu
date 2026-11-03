"""Assemble what goes into each model call.

To build:
- system prompt (purpose, boundaries, when to stop)
- as many recent messages as fit the token budget
- summary of older turns once the budget is exceeded
- never split a tool call from its result
"""
