"""Reads LiteLLM gateway settings from the environment (.env in the project root)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(
            f"{name}가 설정되지 않았습니다. 프로젝트 루트의 .env 파일 또는 환경 변수로 지정하세요."
        )
    return value


def base_url() -> str:
    return _require("LITELLM_BASE_URL")


def api_key() -> str:
    return _require("LITELLM_API_KEY")


def default_model() -> str:
    return os.environ.get("LITELLM_MODEL", "claude-opus-5")


def litellm_model_id(model: str | None = None) -> str:
    """LiteLLM routes a custom OpenAI-compatible gateway via the `openai/` provider prefix."""
    return f"openai/{model or default_model()}"


# Anthropic list pricing ($ per 1M tokens), used only to *estimate* spend for a
# gateway model litellm itself doesn't know the price of. The gateway may bill
# differently -- treat this as an approximation, not a real invoice.
_PRICING_PER_1M = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-fable-5-1": (10.00, 50.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
}


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    pricing = _PRICING_PER_1M.get(model)
    if pricing is None:
        return None
    in_price, out_price = pricing
    return (prompt_tokens / 1_000_000) * in_price + (completion_tokens / 1_000_000) * out_price


def usage_dict(usage) -> dict:
    """Normalize a litellm response.usage object into a plain dict, including
    prompt-cache fields when the gateway populates them (field names vary
    slightly by provider translation, so this checks a few known spellings)."""
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details else None
    cache_write = None
    if details:
        cache_write = getattr(details, "cache_creation_tokens", None) or getattr(
            details, "cache_write_tokens", None
        )
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "cached_tokens": cached,
        "cache_write_tokens": cache_write,
    }
