"""LLM providers. Each one adapts a vendor API to kivu.messages.

Add a provider: write providers/<name>.py with a class matching base.Provider,
then add a branch to create() below.
"""

from __future__ import annotations

from .base import Provider


def create(name: str, model: str, api_key: str) -> Provider:
    # Imports live inside the branches so you only need the SDK you use.
    if name == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider(model, api_key)
    raise ValueError(f"unknown provider: {name}")
