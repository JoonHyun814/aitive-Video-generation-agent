"""Deterministic executor: drives the ComfyUI Workflow API against a
validated GenerationPlan. No LLM calls happen here -- the plan already
decided every prompt/reference/selection; this just makes the HTTP calls,
polls, downloads outputs, and records what happened.

Every step is wrapped so one failure (a timed-out Qwen-Edit call, an
unreachable ComfyUI) doesn't abort the whole run -- later steps that don't
depend on the failed one still get attempted, and everything is recorded in
the manifest for the HTML report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import comfy_client, fetch
from .comfy_client import ComfyUIError
from .schema import GenerationPlan


@dataclass
class StepStatus:
    status: str  # "ok" | "error" | "skipped"
    detail: str = ""
    path: str | None = None  # relative to out_dir
    prompt_id: str | None = None

    def to_dict(self) -> dict:
        return {"status": self.status, "detail": self.detail, "path": self.path, "prompt_id": self.prompt_id}


@dataclass
class Manifest:
    health: dict = field(default_factory=dict)
    comfy_available: bool = False
    product_image: StepStatus = field(default_factory=lambda: StepStatus("skipped"))
    entities: dict[str, StepStatus] = field(default_factory=dict)
    scene_cuts: dict[int, StepStatus] = field(default_factory=dict)
    final_video: StepStatus = field(default_factory=lambda: StepStatus("skipped"))

    def to_dict(self) -> dict:
        return {
            "health": self.health,
            "comfy_available": self.comfy_available,
            "product_image": self.product_image.to_dict(),
            "entities": {k: v.to_dict() for k, v in self.entities.items()},
            "scene_cuts": {str(k): v.to_dict() for k, v in self.scene_cuts.items()},
            "final_video": self.final_video.to_dict(),
        }


ASSETS_ENTITIES = "assets/entities"
ASSETS_CUTS = "assets/cuts"
ASSETS_PRODUCT = "assets/product"
ASSETS_FINAL = "assets/final"


def run_pipeline(
    plan: GenerationPlan,
    scenario: dict,
    out_dir: Path,
    *,
    # Per docs/api/Comfyui_API_SPEC.md "권장 timeout_s 값" (RTX PRO 6000 Blackwell
    # measurements: Flux ~6min, Qwen-Edit ~8-15min, MiniMax H3 ~10-25min,
    # excluding queue wait -- these are the doc's recommended /wait timeouts).
    flux_timeout_s: float = 600,
    qwen_timeout_s: float = 1200,
    minimax_timeout_s: float = 1800,
) -> Manifest:
    manifest = Manifest()

    try:
        manifest.health = comfy_client.health_check()
        manifest.comfy_available = manifest.health.get("comfyui") == "ok"
    except Exception as e:
        manifest.health = {"error": f"{type(e).__name__}: {e}"}
        manifest.comfy_available = False

    product_entity_id = _download_product_image(plan, scenario, out_dir, manifest)

    for entity in plan.entity_images:
        if entity.entity_id == product_entity_id:
            continue  # already handled by _download_product_image
        manifest.entities[entity.entity_id] = _generate_entity_image(
            entity, out_dir, manifest.comfy_available, flux_timeout_s
        )

    entity_paths = {
        eid: (out_dir / s.path) for eid, s in manifest.entities.items() if s.status == "ok" and s.path
    }
    if manifest.product_image.status == "ok" and product_entity_id:
        entity_paths[product_entity_id] = out_dir / manifest.product_image.path

    for scene_cut in plan.scene_cuts:
        manifest.scene_cuts[scene_cut.scene_no] = _generate_scene_cut(
            scene_cut, entity_paths, out_dir, manifest.comfy_available, qwen_timeout_s
        )

    cut_paths = {
        no: (out_dir / s.path) for no, s in manifest.scene_cuts.items() if s.status == "ok" and s.path
    }
    manifest.final_video = _generate_final_video(
        plan, entity_paths, cut_paths, out_dir, manifest.comfy_available, minimax_timeout_s
    )

    return manifest


def _download_product_image(
    plan: GenerationPlan, scenario: dict, out_dir: Path, manifest: Manifest
) -> str | None:
    product_entities = [e for e in plan.entity_images if e.is_product]
    if not product_entities:
        return None
    product_entity_id = product_entities[0].entity_id

    urls = fetch.product_image_urls(scenario)
    if not urls:
        manifest.product_image = StepStatus("error", "시나리오에서 제품 이미지 URL을 찾지 못했습니다.")
        manifest.entities[product_entity_id] = StepStatus(
            "error", "제품 실사진 다운로드 실패로 대체 이미지 없음."
        )
        return product_entity_id

    rel_path = f"{ASSETS_PRODUCT}/product.png"
    dest = out_dir / rel_path
    last_err = ""
    for url in urls:
        try:
            fetch.download_product_image(url, dest)
            manifest.product_image = StepStatus("ok", f"source: {url}", path=rel_path)
            manifest.entities[product_entity_id] = StepStatus(
                "ok", "실제 제품 사진 사용(생성 생략)", path=rel_path
            )
            return product_entity_id
        except Exception as e:
            last_err = f"{type(e).__name__}: {e} ({url})"

    manifest.product_image = StepStatus("error", last_err)
    manifest.entities[product_entity_id] = StepStatus("error", f"제품 실사진 다운로드 실패: {last_err}")
    return product_entity_id


def _generate_entity_image(entity, out_dir: Path, comfy_available: bool, timeout_s: float) -> StepStatus:
    if not comfy_available:
        return StepStatus("skipped", "ComfyUI에 연결할 수 없어 건너뜀 (health check 실패).")
    rel_path = f"{ASSETS_ENTITIES}/{entity.entity_id}.png"
    try:
        prompt_id = comfy_client.generate_flux(entity.generation_prompt, entity.width, entity.height)
        result = comfy_client.wait_result(prompt_id, timeout_s)
        outputs = result.get("outputs") or []
        if not outputs:
            return StepStatus("error", "생성은 완료됐지만 outputs가 비어 있습니다.", prompt_id=prompt_id)
        comfy_client.download_output(outputs[0], out_dir / rel_path)
        return StepStatus("ok", "", path=rel_path, prompt_id=prompt_id)
    except ComfyUIError as e:
        return StepStatus("error", str(e))
    except Exception as e:
        return StepStatus("error", f"{type(e).__name__}: {e}")


def _generate_scene_cut(
    scene_cut, entity_paths: dict[str, Path], out_dir: Path, comfy_available: bool, timeout_s: float
) -> StepStatus:
    missing = [eid for eid in scene_cut.ref_entity_ids if eid not in entity_paths]
    if missing:
        return StepStatus("error", f"참조 이미지가 없는 entity: {missing}")
    if not comfy_available:
        return StepStatus("skipped", "ComfyUI에 연결할 수 없어 건너뜀 (health check 실패).")

    rel_path = f"{ASSETS_CUTS}/scene_{scene_cut.scene_no}.png"
    ref_paths = [entity_paths[eid] for eid in scene_cut.ref_entity_ids]
    try:
        prompt_id = comfy_client.generate_qwen_edit(scene_cut.edit_prompt, scene_cut.negative_prompt, ref_paths)
        result = comfy_client.wait_result(prompt_id, timeout_s)
        outputs = result.get("outputs") or []
        if not outputs:
            return StepStatus("error", "생성은 완료됐지만 outputs가 비어 있습니다.", prompt_id=prompt_id)
        comfy_client.download_output(outputs[0], out_dir / rel_path)
        return StepStatus("ok", "", path=rel_path, prompt_id=prompt_id)
    except ComfyUIError as e:
        return StepStatus("error", str(e))
    except Exception as e:
        return StepStatus("error", f"{type(e).__name__}: {e}")


def _generate_final_video(
    plan: GenerationPlan,
    entity_paths: dict[str, Path],
    cut_paths: dict[int, Path],
    out_dir: Path,
    comfy_available: bool,
    timeout_s: float,
) -> StepStatus:
    fv = plan.final_video
    if fv.reference_entity_id not in entity_paths:
        return StepStatus("error", f"reference_entity_id({fv.reference_entity_id})의 이미지가 없습니다.")
    missing_cuts = [n for n in fv.cut_scene_nos if n not in cut_paths]
    if missing_cuts:
        return StepStatus("error", f"컷 프레임이 없는 scene_no: {missing_cuts}")
    if not comfy_available:
        return StepStatus("skipped", "ComfyUI에 연결할 수 없어 건너뜀 (health check 실패).")

    rel_path = f"{ASSETS_FINAL}/final.mp4"
    ref_path = entity_paths[fv.reference_entity_id]
    cut_image_paths = [cut_paths[n] for n in fv.cut_scene_nos]
    try:
        prompt_id = comfy_client.generate_minimax(
            fv.prompt,
            fv.total_duration_s,
            fv.aspect_ratio,
            fv.cut_times_s,
            ref_path,
            cut_image_paths,
        )
        result = comfy_client.wait_result(prompt_id, timeout_s)
        outputs = result.get("outputs") or []
        if not outputs:
            return StepStatus("error", "생성은 완료됐지만 outputs가 비어 있습니다.", prompt_id=prompt_id)
        comfy_client.download_output(outputs[0], out_dir / rel_path)
        return StepStatus("ok", "", path=rel_path, prompt_id=prompt_id)
    except ComfyUIError as e:
        return StepStatus("error", str(e))
    except Exception as e:
        return StepStatus("error", f"{type(e).__name__}: {e}")
