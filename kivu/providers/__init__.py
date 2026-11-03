"""LLM providers. Each one adapts a vendor API to kivu.messages.

To build:
- a registry: provider name -> provider class
- one module per provider (e.g. anthropic.py, openai.py, gemini.py)
"""
