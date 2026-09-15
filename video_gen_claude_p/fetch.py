"""Downloads the real product photo(s) referenced in scenario.json.

scenario_agent_claude(_litellm) never saves the images it OCR'd/analyzed --
it only records their URLs as SourcedClaim.source_url on product.appearance
claims (that's the "source" of each appearance observation). This module
re-downloads those same URLs so the video pipeline can use the real photo
instead of generating one for the product prop.
"""

from __future__ import annotations

import mimetypes
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

_UA = "Mozilla/5.0 (compatible; video-gen-agent/1.0)"
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def product_image_urls(scenario: dict) -> list[str]:
    """Unique image URLs cited as evidence for the product's appearance,
    in the order they first appear -- the best available real photos.

    appearance claims' source_url is trusted unconditionally: those URLs were
    already established as images by the scenario agent's vision/OCR pass
    (see scenario_agent_claude_litellm/tools.py:web_fetch), so a plain file-
    extension check would wrongly reject CDN dynamic-resize URLs that carry
    no extension (e.g. ".../dims/resizef/554X554/format/webp/optimize")."""
    urls: list[str] = []
    for claim in scenario.get("product", {}).get("appearance", []):
        url = claim.get("source_url", "")
        if url and url not in urls:
            urls.append(url)
    if not urls:
        # Fall back to any image-shaped URL in the general sources list --
        # here an extension check is the only signal available.
        for url in scenario.get("sources", []):
            if _looks_like_image(url) and url not in urls:
                urls.append(url)
    return urls


def _looks_like_image(url: str) -> bool:
    return url.lower().split("?")[0].endswith(_IMAGE_EXT)


def download_product_image(url: str, dest_png: Path) -> None:
    """Downloads `url` and saves it as a PNG at `dest_png` (converting from
    whatever format the CDN served, e.g. webp)."""
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=30)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if not (content_type.startswith("image/") or _looks_like_image(url)):
        raise ValueError(f"{url}의 Content-Type이 이미지가 아닙니다: {content_type or '(unknown)'}")

    img = Image.open(BytesIO(resp.content))
    img = img.convert("RGB")
    dest_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest_png, format="PNG")


def guess_mime(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"
