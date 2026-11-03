"""Settings from the environment, with an optional .env file in the working directory."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MCP_URL = "https://api.worldfeedmonitor.com/mcp"
DEFAULT_MODEL = "claude-opus-5-5"

# The values in .env.example. Left unchanged, they are as good as missing.
PLACEHOLDERS = {"your-api-key", "your-api-secret", "your-anthropic-api-key"}


class ConfigError(Exception):
    """A required setting is missing. The message says which, and where to get it."""


@dataclass(frozen=True)
class Config:
    wfm_api_key: str
    wfm_api_secret: str
    mcp_url: str
    model: str

    def __repr__(self) -> str:  # never print the secret, even by accident
        return (
            f"Config(wfm_api_key='{self.wfm_api_key[:4]}…', mcp_url='{self.mcp_url}', model='{self.model}')"
        )


def load_dotenv(path: Path = Path(".env")) -> None:
    """KEY=VALUE lines into os.environ, without overriding what is already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("'\"")
        if value and value not in PLACEHOLDERS:
            os.environ.setdefault(key.strip(), value)


def load_config(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env

    def setting(name: str) -> str:
        value = env.get(name, "").strip()
        return "" if value in PLACEHOLDERS else value

    missing = [name for name in ("WFM_API_KEY", "WFM_API_SECRET") if not setting(name)]
    if missing:
        raise ConfigError(
            f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set. "
            "Create a free account at https://worldfeedmonitor.com/register, create API credentials "
            "in the dashboard, and put them in .env (see .env.example)."
        )

    # ANTHROPIC_API_KEY is left to the Anthropic SDK, which also accepts other
    # credential sources. An unusable key is reported when the first request fails.
    return Config(
        wfm_api_key=setting("WFM_API_KEY"),
        wfm_api_secret=setting("WFM_API_SECRET"),
        mcp_url=setting("WFM_MCP_URL") or DEFAULT_MCP_URL,
        model=setting("ANTHROPIC_MODEL") or DEFAULT_MODEL,
    )
