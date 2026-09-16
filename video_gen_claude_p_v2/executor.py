"""Deterministic executor: drives the ComfyUI Workflow API against a
validated GenerationPlan. No LLM calls happen here -- the plan (produced by
agent_loop.py's tool-calling research) already decided every prompt/
reference/selection; this just makes the HTTP calls, polls, downloads
outputs, composites grids, blurs privacy-sensitive references, and records
what happened.

Adapted from video_gen_claude_p (v1)'s executor.py. Two structural
differences from v1:
  - Scene cuts build their (up to 3) Qwen-Edit reference images from
    SceneCutPlan's three fixed fields (character_ids/location_id/prop_ids)
    instead of grouping a flat ref_entity_ids list by type -- see
    imaging.make_person_grid/make_grid.
  - A new anchor-frame step runs between scene cuts and the final video: one
    extra Qwen-Edit composite (person grid of anchor_character_ids + prop
    grid of anchor_prop_ids) becomes MiniMax's single reference_image, so it
    can ground both protagonist-face continuity and product/logo accuracy at
    once (schema.py's FinalVideoPlan docstring explains why a raw label grid
    isn't used directly).

Every step is wrapped so one failure (a timed-out Qwen-Edit call, an
unreachable ComfyUI, a dead reference URL) doesn't abort the whole run --
later steps that don't depend on the failed one still get attempted, and
everything is recorded in the manifest for the HTML report.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import comfy_client, config, fetch, imaging, privacy
from .comfy_client import ComfyUIError
from .schema import CharacterAssetPlan, GenerationPlan

_DEFAULT_NEGATIVE_PROMPT = "blurry, distorted, extra limbs, watermark, low quality"


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
    anchor_frame: StepStatus = field(default_factory=lambda: StepStatus("skipped"))
    final_video: StepStatus = field(default_factory=lambda: StepStatus("skipped"))

    def to_dict(self) -> dict:
        return {
            "health": self.health,
            "comfy_available": self.comfy_available,
            "product_image": self.product_image.to_dict(),
            "entities": {k: v.to_dict() for k, v in self.entities.items()},
            "scene_cuts": {str(k): v.to_dict() for k, v in self.scene_cuts.items()},
            "anchor_frame": self.anchor_frame.to_dict(),
            "final_video": self.final_video.to_dict(),
        }


ASSETS_ENTITIES = "assets/entities"
ASSETS_CUTS = "assets/cuts"
ASSETS_PRODUCT = "assets/product"
ASSETS_ANCHOR = "assets/anchor"
ASSETS_FINAL = "assets/final"
ASSETS_REFERENCES = "assets/references"


def run_pipeline(
    plan: GenerationPlan,
    scenario: dict,
    out_dir: Path,
    *,
    # Per docs/api/Comfyui_API_SPEC.md "권장 timeout_s 값" (RTX PRO 6000 Blackwell
    # measurements: Flux ~6min, Qwen-Edit ~8-15min, MiniMax H3 ~10-25min,
    # excluding queue wait).
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

    # Panel-level paths (face_body/clothing/hair) per character, kept apart
    # from entity_paths so a cut with 2+ characters can build its person
    # grid from full-resolution panels instead of nesting each character's
    # own pre-built grid image (see imaging.make_person_grid's docstring).
    character_panels: dict[str, tuple[Path, Path, Path]] = {}
    for char in plan.character_assets:
        status, panels = _generate_character_asset(char, out_dir, manifest.comfy_available, flux_timeout_s)
        manifest.entities[char.entity_id] = status
        if panels is not None:
            character_panels[char.entity_id] = panels

    for entity in plan.entity_images:
        if entity.entity_id == product_entity_id:
            continue  # already handled by _download_product_image
        manifest.entities[entity.entity_id] = _generate_entity_image(
            entity, out_dir, manifest.comfy_available, flux_timeout_s
        )

    # entity_paths only carries the location/prop (and character *display*
    # grid, for report.html) images now -- character reference-group
    # composition goes through character_panels instead.
    entity_paths = {
        eid: (out_dir / s.path) for eid, s in manifest.entities.items() if s.status == "ok" and s.path
    }
    if manifest.product_image.status == "ok" and product_entity_id:
        entity_paths[product_entity_id] = out_dir / manifest.product_image.path

    for scene_cut in plan.scene_cuts:
        manifest.scene_cuts[scene_cut.scene_no] = _generate_scene_cut(
            scene_cut, entity_paths, character_panels, out_dir, manifest.comfy_available, qwen_timeout_s,
        )

    cut_paths = {
        no: (out_dir / s.path) for no, s in manifest.scene_cuts.items() if s.status == "ok" and s.path
    }

    manifest.anchor_frame = _generate_anchor_frame(
        plan.final_video, entity_paths, character_panels, out_dir, manifest.comfy_available, qwen_timeout_s,
    )
    anchor_path = out_dir / manifest.anchor_frame.path if manifest.anchor_frame.status == "ok" else None

    manifest.final_video = _generate_final_video(
        plan, anchor_path, cut_paths, out_dir, manifest.comfy_available, minimax_timeout_s
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
    raw_path = out_dir / f"{ASSETS_REFERENCES}/product_raw.png"
    last_err = ""
    for url in urls:
        try:
            fetch.download_product_image(url, raw_path)
            shutil.copy(raw_path, dest)
            # 제품/로고는 텍스트 블러 예외지만 얼굴 블러는 항상 실행 -- 제품 실사진에
            # 우연히 사람이 찍혀 있을 가능성을 방어한다 (privacy.py 모듈 docstring 참고).
            privacy.blur_faces_and_text(dest, skip_text=True)
            manifest.product_image = StepStatus("ok", f"source: {url}", path=rel_path)
            manifest.entities[product_entity_id] = StepStatus(
                "ok", "실제 제품 사진 사용(생성 생략, 텍스트 블러 예외)", path=rel_path
            )
            return product_entity_id
        except Exception as e:
            last_err = f"{type(e).__name__}: {e} ({url})"

    manifest.product_image = StepStatus("error", last_err)
    manifest.entities[product_entity_id] = StepStatus("error", f"제품 실사진 다운로드 실패: {last_err}")
    return product_entity_id


def _flux_generate(prompt: str, width: int, height: int, dest: Path, timeout_s: float) -> str | None:
    """Runs one Flux call and downloads its output to `dest`. Returns the
    ComfyUI prompt_id on success."""
    prompt_id = comfy_client.generate_flux(prompt, width, height)
    result = comfy_client.wait_result(prompt_id, timeout_s)
    outputs = result.get("outputs") or []
    if not outputs:
        raise ComfyUIError("생성은 완료됐지만 outputs가 비어 있습니다.")
    comfy_client.download_output(outputs[0], dest)
    return prompt_id


def _generate_character_asset(
    char: CharacterAssetPlan, out_dir: Path, comfy_available: bool, flux_timeout_s: float
) -> tuple[StepStatus, tuple[Path, Path, Path] | None]:
    """face_body is always a fresh Flux call (no reference); clothing/hair
    each resolve independently via _resolve_character_part. The three panels
    are composited into this character's own display grid (via
    imaging.make_person_grid, single-character case) for manifest/report
    purposes, and the panel paths themselves are returned so a cut with 2+
    characters can build its person grid straight from full-resolution
    panels instead of nesting this display grid (see
    imaging.make_person_grid's docstring for why nesting was wrong)."""
    if not comfy_available:
        return StepStatus("skipped", "ComfyUI에 연결할 수 없어 건너뜀 (health check 실패)."), None

    face_body_path = out_dir / f"{ASSETS_ENTITIES}/{char.entity_id}_face_body.png"
    try:
        _flux_generate(char.face_body_prompt, char.width, char.height, face_body_path, flux_timeout_s)
    except ComfyUIError as e:
        return StepStatus("error", f"face/body 생성 실패: {e}"), None
    except Exception as e:
        return StepStatus("error", f"face/body 생성 실패: {type(e).__name__}: {e}"), None

    clothing_path, clothing_detail = _resolve_character_part(
        char.entity_id, "clothing", char.clothing_reference_url, char.clothing_generation_prompt,
        out_dir, char.width, char.height, flux_timeout_s,
    )
    if clothing_path is None:
        return StepStatus("error", f"복장 생성 실패: {clothing_detail}"), None

    hair_path, hair_detail = _resolve_character_part(
        char.entity_id, "hair", char.hair_reference_url, char.hair_generation_prompt,
        out_dir, char.width, char.height, flux_timeout_s,
    )
    if hair_path is None:
        return StepStatus("error", f"머리 생성 실패: {hair_detail}"), None

    grid_rel = f"{ASSETS_ENTITIES}/{char.entity_id}.png"
    try:
        imaging.make_person_grid(
            [(char.entity_id, face_body_path, clothing_path, hair_path)], out_dir / grid_rel,
        )
    except Exception as e:
        return StepStatus("error", f"그리드 합성 실패: {type(e).__name__}: {e}"), None

    status = StepStatus("ok", f"clothing: {clothing_detail} / hair: {hair_detail}", path=grid_rel)
    return status, (face_body_path, clothing_path, hair_path)


def _resolve_character_part(
    entity_id: str,
    part: str,
    reference_url: str,
    generation_prompt: str,
    out_dir: Path,
    width: int,
    height: int,
    flux_timeout_s: float,
) -> tuple[Path | None, str]:
    """Returns (final_image_path, detail_text) for one character part
    (clothing/hair): either a privacy-scrubbed search result, or a Flux
    generation -- exactly one path runs. A character's clothing/hair is
    never is_product/is_logo, so both face and text blur always run. A
    failed reference (download or blur) falls back to Flux rather than
    failing the whole character, same policy as props/locations."""
    final_path = out_dir / f"{ASSETS_ENTITIES}/{entity_id}_{part}.png"
    fallback_note = ""

    if reference_url:
        raw_path = out_dir / f"{ASSETS_REFERENCES}/{entity_id}_{part}_raw.png"
        try:
            fetch.download_image(reference_url, raw_path)
            shutil.copy(raw_path, final_path)
            privacy.blur_faces_and_text(final_path)
            return final_path, f"reference: {reference_url}"
        except Exception as e:
            fallback_note = f"참조 실패({type(e).__name__}: {e}) — Flux로 대체: "

    if not generation_prompt:
        return None, f"{fallback_note}generation_prompt도 비어 있어 대체 생성이 불가능합니다."

    try:
        _flux_generate(generation_prompt, width, height, final_path, flux_timeout_s)
    except ComfyUIError as e:
        return None, f"{fallback_note}Flux 생성 실패: {e}"
    except Exception as e:
        return None, f"{fallback_note}Flux 생성 실패: {type(e).__name__}: {e}"

    return final_path, f"{fallback_note}Flux 생성"


def _generate_entity_image(entity, out_dir: Path, comfy_available: bool, flux_timeout_s: float) -> StepStatus:
    """Locations and non-character props: either a privacy-scrubbed search
    result, or a Flux generation -- never both. is_logo entities are the one
    exception: reference_image_url is mandatory and never falls back to Flux
    (a redrawn logo is a brand-accuracy failure, not a style choice) -- and
    text blur is skipped (the logo's own text/mark is the point), but face
    blur still runs (see privacy.py's module docstring)."""
    if not comfy_available:
        return StepStatus("skipped", "ComfyUI에 연결할 수 없어 건너뜀 (health check 실패).")

    rel_path = f"{ASSETS_ENTITIES}/{entity.entity_id}.png"
    dest = out_dir / rel_path

    if entity.is_logo:
        if not entity.reference_image_url:
            return StepStatus("error", "is_logo=true인데 reference_image_url이 비어 있습니다.")
        raw_path = out_dir / f"{ASSETS_REFERENCES}/{entity.entity_id}_raw.png"
        try:
            fetch.download_image(entity.reference_image_url, raw_path)
            shutil.copy(raw_path, dest)
            privacy.blur_faces_and_text(dest, skip_text=True)
            return StepStatus(
                "ok", f"reference(logo, 텍스트 블러 예외): {entity.reference_image_url}", path=rel_path
            )
        except Exception as e:
            return StepStatus("error", f"로고 다운로드 실패: {type(e).__name__}: {e}")

    fallback_note = ""
    if entity.reference_image_url:
        raw_path = out_dir / f"{ASSETS_REFERENCES}/{entity.entity_id}_raw.png"
        try:
            fetch.download_image(entity.reference_image_url, raw_path)
            shutil.copy(raw_path, dest)
            privacy.blur_faces_and_text(dest)
            return StepStatus("ok", f"reference: {entity.reference_image_url}", path=rel_path)
        except Exception as e:
            fallback_note = f"참조 실패({type(e).__name__}: {e}) — Flux로 대체: "

    try:
        prompt_id = _flux_generate(entity.generation_prompt, entity.width, entity.height, dest, flux_timeout_s)
        return StepStatus("ok", fallback_note, path=rel_path, prompt_id=prompt_id)
    except ComfyUIError as e:
        return StepStatus("error", f"{fallback_note}{e}")
    except Exception as e:
        return StepStatus("error", f"{fallback_note}{type(e).__name__}: {e}")


def _build_reference_groups(
    character_ids: list[str],
    location_id: str,
    prop_ids: list[str],
    entity_paths: dict[str, Path],
    character_panels: dict[str, tuple[Path, Path, Path]],
    grid_dir: Path,
    grid_stem: str,
) -> tuple[list[Path] | None, list[str]]:
    """Shared by scene-cut and anchor-frame generation: turns the three
    fixed reference-group fields into at most 3 actual image paths (one
    "person grid", one location image, one "prop grid"). Returns
    (ref_paths, missing_entity_ids) -- missing is non-empty if any listed id
    has no successfully-generated asset yet. Characters are looked up in
    character_panels (per-character face/clothing/hair panels), not
    entity_paths -- see imaging.make_person_grid's docstring for why the
    person grid is built from panels rather than nested display grids."""
    missing = (
        [eid for eid in character_ids if eid not in character_panels]
        + [eid for eid in ([location_id] if location_id else []) + prop_ids if eid not in entity_paths]
    )
    if missing:
        return None, missing

    ref_paths: list[Path] = []
    if character_ids:
        person_items = [(cid, *character_panels[cid]) for cid in character_ids]
        ref_paths.append(imaging.make_person_grid(person_items, grid_dir / f"{grid_stem}_person_grid.png"))
    if location_id:
        ref_paths.append(entity_paths[location_id])
    if prop_ids:
        if len(prop_ids) == 1:
            ref_paths.append(entity_paths[prop_ids[0]])
        else:
            prop_grid_path = grid_dir / f"{grid_stem}_props_grid.png"
            imaging.make_grid([(entity_paths[eid], eid) for eid in prop_ids], prop_grid_path)
            ref_paths.append(prop_grid_path)
    return ref_paths, []


def _generate_scene_cut(
    scene_cut,
    entity_paths: dict[str, Path],
    character_panels: dict[str, tuple[Path, Path, Path]],
    out_dir: Path,
    comfy_available: bool,
    timeout_s: float,
) -> StepStatus:
    rel_path = f"{ASSETS_CUTS}/scene_{scene_cut.scene_no}.png"
    ref_paths, missing = _build_reference_groups(
        scene_cut.character_ids, scene_cut.location_id, scene_cut.prop_ids,
        entity_paths, character_panels, out_dir / ASSETS_CUTS, f"scene_{scene_cut.scene_no}",
    )
    if missing:
        return StepStatus("error", f"참조 이미지가 없는 entity: {missing}")
    if not comfy_available:
        return StepStatus("skipped", "ComfyUI에 연결할 수 없어 건너뜀 (health check 실패).")
    if not ref_paths:
        return StepStatus("error", "character_ids/location_id/prop_ids가 모두 비어 있습니다.")

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


def _generate_anchor_frame(
    fv,
    entity_paths: dict[str, Path],
    character_panels: dict[str, tuple[Path, Path, Path]],
    out_dir: Path,
    comfy_available: bool,
    timeout_s: float,
) -> StepStatus:
    """Builds the single "brand anchor" frame that becomes MiniMax's
    reference_image: one more Qwen-Edit composite (person grid of
    anchor_character_ids + prop grid of anchor_prop_ids), not a raw label
    grid handed straight to MiniMax -- see schema.py's FinalVideoPlan
    docstring for why."""
    rel_path = f"{ASSETS_ANCHOR}/anchor.png"
    ref_paths, missing = _build_reference_groups(
        fv.anchor_character_ids, "", fv.anchor_prop_ids,
        entity_paths, character_panels, out_dir / ASSETS_ANCHOR, "anchor",
    )
    if missing:
        return StepStatus("error", f"참조 이미지가 없는 entity: {missing}")
    if not comfy_available:
        return StepStatus("skipped", "ComfyUI에 연결할 수 없어 건너뜀 (health check 실패).")
    if not ref_paths:
        return StepStatus("error", "anchor_character_ids/anchor_prop_ids가 모두 비어 있습니다.")

    try:
        prompt_id = comfy_client.generate_qwen_edit(fv.anchor_prompt, _DEFAULT_NEGATIVE_PROMPT, ref_paths)
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
    anchor_path: Path | None,
    cut_paths: dict[int, Path],
    out_dir: Path,
    comfy_available: bool,
    timeout_s: float,
) -> StepStatus:
    fv = plan.final_video
    if anchor_path is None:
        return StepStatus("error", "앵커 프레임이 없어 최종 영상을 만들 수 없습니다.")
    missing_cuts = [n for n in fv.cut_scene_nos if n not in cut_paths]
    if missing_cuts:
        return StepStatus("error", f"컷 프레임이 없는 scene_no: {missing_cuts}")
    if not comfy_available:
        return StepStatus("skipped", "ComfyUI에 연결할 수 없어 건너뜀 (health check 실패).")

    rel_path = f"{ASSETS_FINAL}/final.mp4"
    cut_image_paths = [cut_paths[n] for n in fv.cut_scene_nos]
    try:
        prompt_id = comfy_client.generate_minimax(
            fv.prompt,
            fv.total_duration_s,
            fv.aspect_ratio,
            fv.cut_times_s,
            anchor_path,
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
