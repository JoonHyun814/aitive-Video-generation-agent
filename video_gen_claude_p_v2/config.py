"""Settings for both halves of this package: the LiteLLM gateway the planning
agent talks to (config borrowed from scenario_agent_claude_litellm/config.py)
and the ComfyUI generation backend the executor talks to (config borrowed
from video_gen_claude_p/config.py). Merged into one module because this
package -- unlike either source -- needs both at once.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

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


# --- LiteLLM gateway (planning agent) ---

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
# gateway model litellm itself doesn't know the price of -- see
# scenario_agent_claude_litellm/config.py for the same caveat.
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
    prompt-cache fields when the gateway populates them."""
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


# --- ComfyUI generation backend (executor) ---

def comfyui_base_url() -> str:
    """Base URL of the ComfyUI Workflow API wrapper (Flux/Qwen-Edit/MiniMax), e.g.
    http://10.110.56.116:8001 -- NOT the SeedVR2 upscale API that happens to
    also listen on :8000 on the same host."""
    url = os.environ.get("COMFYUI_API_BASE_URL")
    if not url:
        raise ConfigError(
            "COMFYUI_API_BASE_URL이 설정되지 않았습니다. .env에 지정하세요 "
            "(예: COMFYUI_API_BASE_URL=http://10.110.56.116:8001)."
        )
    return url.rstrip("/")


def comfyui_view_host() -> str:
    """Host ComfyUI's own :8188 /api/view endpoint should live on. Result
    payloads return output URLs as http://127.0.0.1:8188/..., which only
    resolves on the ComfyUI machine itself -- the download step rewrites the
    host to this value. Defaults to the API base URL's host; override with
    COMFYUI_VIEW_HOST if ComfyUI listens on a different host/IP than the API
    wrapper."""
    override = os.environ.get("COMFYUI_VIEW_HOST")
    if override:
        return override
    return urlparse(comfyui_base_url()).hostname or "127.0.0.1"


DEFAULT_ENTITY_IMAGE_SIZE = (1024, 1024)
DEFAULT_ASPECT_RATIO = "16:9 (Widescreen)"
MAX_QWEN_REF_IMAGES = 3
MAX_MINIMAX_CUTS = 3
MAX_TOTAL_DURATION_S = 15.0
