"""Settings (BYOK).

- API keys: keys.json in the repo root, written by you, gitignored.
  Format: {"gemini": "..."}. A <PROVIDER>_API_KEY env var overrides it.
- Provider and model: picked with `kivu setup`, saved to ~/.config/kivu/config.json.
  Flags (--provider, -m) and KIVU_PROVIDER / KIVU_MODEL override it.

Still to build:
- cost and time limits
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# The first model in each list is the default.
PROVIDERS = {
    "gemini": {
        "models": [
            "gemini-flash-latest",
            "gemini-pro-latest",
            "gemini-flash-lite-latest",
            "gemini-3.8-flash",
        ],
        "key_url": "https://aistudio.google.com/app/apikey",
    },
    "openai": {
        "models": ["gpt-4o"],
        "key_url": "https://platform.openai.com/account/api-keys",
    },
}

KEYS_PATH = Path(__file__).resolve().parent.parent / "keys.json"
CONFIG_PATH = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config") / "kivu" / "config.json"


@dataclass
class Config:
    provider: str
    model: str
    api_key: str
    max_steps: int = 20


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def read_file() -> dict:
    return _read(CONFIG_PATH)


def api_key(provider: str) -> str | None:
    return os.getenv(f"{provider.upper()}_API_KEY") or _read(KEYS_PATH).get(provider) or None


def save(provider: str, model: str) -> None:
    data = read_file()
    data["provider"] = provider
    data["model"] = model
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2))


def load(provider: str | None = None, model: str | None = None) -> Config | None:
    """Return the settings, or None if no provider has been picked yet (run setup)."""
    data = read_file()
    provider = provider or os.getenv("KIVU_PROVIDER") or data.get("provider")
    if not provider:
        return None
    if provider not in PROVIDERS:
        raise SystemExit(f"unknown provider: {provider} (known: {', '.join(PROVIDERS)})")
    if not model:
        model = os.getenv("KIVU_MODEL")
    if not model:
        model = data.get("model") if data.get("provider") == provider else None
    model = model or PROVIDERS[provider]["models"][0]
    key = api_key(provider)
    if not key:
        raise SystemExit(f'no key for {provider}: add "{provider}": "..." to {KEYS_PATH}')
    return Config(provider, model, key)
