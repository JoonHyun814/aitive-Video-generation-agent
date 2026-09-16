"""Downloads the real product photo(s) referenced in scenario.json, plus a
shared helper for downloading any reference image found during planning.
Copied from video_gen_claude_p (v1)'s fetch.py -- unchanged content, just
made part of this self-contained package.
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
    """Unique image URLs cited as evidence for the product's appearance, in
    the order they first appear -- the best available real photos.

    appearance claims' source_url is trusted unconditionally: those URLs were
    already established as images by the scenario agent's vision/OCR pass, so
    a plain file-extension check would wrongly reject CDN dynamic-resize URLs
    that carry no extension."""
    urls: list[str] = []
    for claim in scenario.get("product", {}).get("appearance", []):
        url = claim.get("source_url", "")
        if url and url not in urls:
            urls.append(url)
    if not urls:
        for url in scenario.get("sources", []):
            if _looks_like_image(url) and url not in urls:
                urls.append(url)
    return urls


def _looks_like_image(url: str) -> bool:
    return url.lower().split("?")[0].endswith(_IMAGE_EXT)


def download_image(url: str, dest_png: Path) -> None:
    """Downloads `url` and saves it as a PNG at `dest_png` (converting from
    whatever format the source served, e.g. webp). Shared by the
    product-photo path (URL trusted from the scenario's own appearance
    claims) and the reference-image-search path (URL picked by the planning
    agent from web_search/web_fetch results) -- both are "download a real
    photo someone already found" cases."""
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=30)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if not (content_type.startswith("image/") or _looks_like_image(url)):
        raise ValueError(f"{url}의 Content-Type이 이미지가 아닙니다: {content_type or '(unknown)'}")

    img = Image.open(BytesIO(resp.content))
    img = img.convert("RGB")
    dest_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest_png, format="PNG")


# Kept as a name distinct from download_image at call sites -- same
# implementation, but the two call sites mean different things by "failure"
# (see executor.py: a failed product download is a hard error, a failed
# reference-image download just falls back to plain Flux generation).
download_product_image = download_image


def guess_mime(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"
