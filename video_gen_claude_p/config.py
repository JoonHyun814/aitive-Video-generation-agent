"""ComfyUI generation-server settings (.env in the project root).

Separate from scenario_agent_claude_litellm/config.py (that one is about the
LiteLLM text-gateway); this one is about the image/video generation backend
(docs/api/Comfyui_API_SPEC.md).
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


def comfyui_base_url() -> str:
    """Base URL of the ComfyUI Workflow API wrapper (Flux/Qwen-Edit/MiniMax), e.g.
    http://10.110.56.116:8001 -- NOT the same server/port as the SeedVR2 upscale
    API that happens to also listen on :8000 on a different host."""
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
