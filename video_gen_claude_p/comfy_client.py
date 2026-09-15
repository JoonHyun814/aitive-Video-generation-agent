"""Thin HTTP client for the ComfyUI Workflow API (docs/api/Comfyui_API_SPEC.md).

Endpoint/content-type details here follow the LIVE /openapi.json of the
server, cross-checked against the markdown doc (2026-09-15 revision):
/flux/generate is application/x-www-form-urlencoded (no files) -- the live
schema is authoritative there, the doc says multipart but openapi.json still
says urlencoded.

wait_result() polls GET /result/{id} on a short-lived connection instead of
blocking on GET /result/{id}/wait for the full duration -- the doc itself
warns that a 10+ minute single HTTP connection (MiniMax routinely takes
10-25 minutes) risks being killed by an intermediate proxy/client timeout
before ComfyUI actually finishes.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

from . import config, fetch

_ERR_TEXT_LIMIT = 2000


class ComfyUIError(RuntimeError):
    pass


def health_check() -> dict:
    r = requests.get(f"{config.comfyui_base_url()}/health", timeout=10)
    r.raise_for_status()
    return r.json()


def _post(path: str, *, data: dict, files: list[tuple] | None = None) -> str:
    r = requests.post(f"{config.comfyui_base_url()}{path}", data=data, files=files, timeout=30)
    if r.status_code >= 400:
        raise ComfyUIError(f"POST {path} 실패: HTTP {r.status_code}: {r.text[:_ERR_TEXT_LIMIT]}")
    body = r.json()
    prompt_id = body.get("prompt_id")
    if not prompt_id:
        raise ComfyUIError(f"POST {path} 응답에 prompt_id가 없습니다: {body}")
    return prompt_id


def generate_flux(positive_prompt: str, width: int, height: int, seed: int | None = None) -> str:
    data = {"positive_prompt": positive_prompt, "width": width, "height": height}
    if seed is not None:
        data["seed"] = seed
    return _post("/flux/generate", data=data)


def generate_qwen_edit(
    positive_prompt: str,
    negative_prompt: str,
    image_paths: list[Path],
    seed: int | None = None,
) -> str:
    data: dict = {"positive_prompt": positive_prompt}
    if negative_prompt:
        data["negative_prompt"] = negative_prompt
    if seed is not None:
        data["seed"] = seed
    handles = [open(p, "rb") for p in image_paths]
    try:
        files = [
            ("images", (p.name, fh, fetch.guess_mime(p))) for p, fh in zip(image_paths, handles)
        ]
        return _post("/qwen-edit/generate", data=data, files=files)
    finally:
        for fh in handles:
            fh.close()


def generate_minimax(
    prompt: str,
    total_duration: float,
    aspect_ratio: str,
    cut_times: list[float],
    reference_image_path: Path,
    cut_image_paths: list[Path],
    megapixels: float = 0.4,
    seed: int | None = None,
) -> str:
    data = {
        "prompt": prompt,
        "total_duration": total_duration,
        "aspect_ratio": aspect_ratio,
        "cut_times": json.dumps(cut_times),
        "megapixels": megapixels,
    }
    if seed is not None:
        data["seed"] = seed
    ref_fh = open(reference_image_path, "rb")
    cut_fhs = [open(p, "rb") for p in cut_image_paths]
    try:
        files = [("reference_image", (reference_image_path.name, ref_fh, fetch.guess_mime(reference_image_path)))]
        files += [
            ("cut_images", (p.name, fh, fetch.guess_mime(p))) for p, fh in zip(cut_image_paths, cut_fhs)
        ]
        return _post("/minimax/generate", data=data, files=files)
    finally:
        ref_fh.close()
        for fh in cut_fhs:
            fh.close()


def wait_result(prompt_id: str, timeout_s: float, poll_interval_s: float = 10.0) -> dict:
    """Polls GET /result/{id} (non-blocking, short request) instead of the
    blocking /wait endpoint -- see module docstring."""
    deadline = time.monotonic() + timeout_s
    last_status = "pending"
    while True:
        r = requests.get(f"{config.comfyui_base_url()}/result/{prompt_id}", timeout=30)
        if r.status_code >= 400:
            raise ComfyUIError(f"결과 조회 실패: HTTP {r.status_code}: {r.text[:_ERR_TEXT_LIMIT]}")
        body = r.json()
        last_status = body.get("status")
        if last_status == "success":
            return body
        if last_status == "error":
            raise ComfyUIError(
                f"생성 실패 (prompt_id={prompt_id}): {str(body.get('error', '알 수 없는 오류'))[:_ERR_TEXT_LIMIT]}"
            )
        if time.monotonic() >= deadline:
            raise ComfyUIError(
                f"생성 타임아웃 (prompt_id={prompt_id}, {timeout_s:g}초 초과, 마지막 상태={last_status})"
            )
        time.sleep(poll_interval_s)


def download_output(output: dict, dest: Path) -> None:
    url = output.get("url", "")
    if not url:
        raise ComfyUIError(f"출력에 url이 없습니다: {output}")
    # ComfyUI answers on its own :8188, and returns URLs against 127.0.0.1 --
    # only valid on the ComfyUI machine itself. Rewrite to the real host.
    rewritten = re.sub(r"://[^/]+", f"://{config.comfyui_view_host()}:8188", url, count=1)
    resp = requests.get(rewritten, timeout=120)
    if resp.status_code >= 400:
        raise ComfyUIError(f"결과 다운로드 실패({rewritten}): HTTP {resp.status_code}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
