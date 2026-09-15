"""Structural validation of a GenerationPlan against the source scenario.

Mirrors scenario_agent_claude/validators.py: hard errors trigger a repair
round, warnings are reported but don't block execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .schema import GenerationPlan


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_plan(plan: GenerationPlan, scenario: dict) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    char_ids = {c["id"] for c in scenario.get("characters", [])}
    loc_ids = {l["id"] for l in scenario.get("locations", [])}
    prop_ids = {p["id"] for p in scenario.get("props", [])}
    all_entity_ids = char_ids | loc_ids | prop_ids
    scene_nos = {s["scene_no"] for s in scenario.get("scenes", [])}
    scenes_by_no = {s["scene_no"]: s for s in scenario.get("scenes", [])}
    duration_s = float(scenario.get("duration_s", 0.0))

    # --- entity_images ---
    plan_entity_ids = [e.entity_id for e in plan.entity_images]
    plan_entity_id_set = set(plan_entity_ids)
    if len(plan_entity_ids) != len(plan_entity_id_set):
        errors.append("entity_images에 중복된 entity_id가 있습니다.")

    missing = all_entity_ids - plan_entity_id_set
    if missing:
        errors.append(f"entity_images에 다음 entity가 빠졌습니다: {sorted(missing)}")
    extra = plan_entity_id_set - all_entity_ids
    if extra:
        errors.append(f"entity_images에 시나리오에 없는 entity_id가 있습니다: {sorted(extra)}")

    expected_type = {}
    for eid in char_ids:
        expected_type[eid] = "character"
    for eid in loc_ids:
        expected_type[eid] = "location"
    for eid in prop_ids:
        expected_type[eid] = "prop"
    for e in plan.entity_images:
        want = expected_type.get(e.entity_id)
        if want and e.entity_type != want:
            errors.append(
                f"entity_id={e.entity_id}의 entity_type이 {e.entity_type}이지만 "
                f"시나리오상 {want}입니다."
            )
        if e.width <= 0 or e.height <= 0:
            errors.append(f"entity_id={e.entity_id}의 width/height가 올바르지 않습니다.")

    product_entities = [e for e in plan.entity_images if e.is_product]
    if len(product_entities) > 1:
        errors.append(
            f"is_product=true인 entity가 {len(product_entities)}개입니다 (최대 1개여야 함): "
            f"{[e.entity_id for e in product_entities]}"
        )
    elif len(product_entities) == 0:
        warnings.append(
            "is_product=true로 표시된 entity가 없습니다 — 최종 영상의 reference_entity_id가 "
            "실제 제품 사진 대신 생성 이미지를 참조하게 됩니다."
        )
    if product_entities and product_entities[0].entity_type != "prop":
        errors.append("is_product=true인 entity는 반드시 entity_type=prop이어야 합니다.")

    # --- scene_cuts ---
    plan_scene_nos = [sc.scene_no for sc in plan.scene_cuts]
    if set(plan_scene_nos) != scene_nos:
        missing_scenes = scene_nos - set(plan_scene_nos)
        extra_scenes = set(plan_scene_nos) - scene_nos
        if missing_scenes:
            errors.append(f"scene_cuts에 빠진 scene_no가 있습니다: {sorted(missing_scenes)}")
        if extra_scenes:
            errors.append(f"scene_cuts에 시나리오에 없는 scene_no가 있습니다: {sorted(extra_scenes)}")
    if len(plan_scene_nos) != len(set(plan_scene_nos)):
        errors.append("scene_cuts에 중복된 scene_no가 있습니다.")

    for sc in plan.scene_cuts:
        if not (1 <= len(sc.ref_entity_ids) <= config.MAX_QWEN_REF_IMAGES):
            errors.append(
                f"scene {sc.scene_no}: ref_entity_ids 개수는 1~{config.MAX_QWEN_REF_IMAGES}개여야 "
                f"하는데 {len(sc.ref_entity_ids)}개입니다."
            )
        unknown = [eid for eid in sc.ref_entity_ids if eid not in all_entity_ids]
        if unknown:
            errors.append(f"scene {sc.scene_no}: 존재하지 않는 entity_id 참조: {unknown}")
        scene = scenes_by_no.get(sc.scene_no)
        if scene is not None:
            usable = set(scene.get("character_ids", [])) | {scene.get("location_id")} | set(
                scene.get("prop_ids", [])
            )
            off_scene = [eid for eid in sc.ref_entity_ids if eid in all_entity_ids and eid not in usable]
            if off_scene:
                warnings.append(
                    f"scene {sc.scene_no}: 그 장면에 등장하지 않는 entity를 참조합니다: {off_scene}"
                )

    # --- final_video ---
    fv = plan.final_video
    if not (1 <= len(fv.cut_scene_nos) <= config.MAX_MINIMAX_CUTS):
        errors.append(
            f"final_video.cut_scene_nos 개수는 1~{config.MAX_MINIMAX_CUTS}개여야 하는데 "
            f"{len(fv.cut_scene_nos)}개입니다."
        )
    if len(fv.cut_scene_nos) != len(set(fv.cut_scene_nos)):
        errors.append("final_video.cut_scene_nos에 중복된 scene_no가 있습니다.")
    unknown_cut_scenes = [n for n in fv.cut_scene_nos if n not in scene_nos]
    if unknown_cut_scenes:
        errors.append(f"final_video.cut_scene_nos에 존재하지 않는 scene_no가 있습니다: {unknown_cut_scenes}")
    if len(fv.cut_times_s) != len(fv.cut_scene_nos):
        errors.append(
            f"final_video.cut_times_s 개수({len(fv.cut_times_s)})가 cut_scene_nos "
            f"개수({len(fv.cut_scene_nos)})와 다릅니다."
        )
    else:
        for n, t in zip(fv.cut_scene_nos, fv.cut_times_s):
            scene = scenes_by_no.get(n)
            if scene is not None and abs(scene["start_s"] - t) > 0.01:
                errors.append(
                    f"final_video: scene {n}의 cut_times_s({t})가 실제 start_s"
                    f"({scene['start_s']})와 다릅니다."
                )
        if sorted(fv.cut_times_s) != list(fv.cut_times_s):
            errors.append("final_video.cut_times_s는 시간 순으로 정렬되어 있어야 합니다.")

    if fv.reference_entity_id not in all_entity_ids:
        errors.append(f"final_video.reference_entity_id가 존재하지 않는 entity입니다: {fv.reference_entity_id}")
    elif product_entities and fv.reference_entity_id != product_entities[0].entity_id:
        warnings.append(
            f"final_video.reference_entity_id({fv.reference_entity_id})가 제품 entity"
            f"({product_entities[0].entity_id})가 아닙니다 — '제품이미지 참조' 요구사항과 다를 수 있습니다."
        )

    if fv.total_duration_s <= 0:
        errors.append("final_video.total_duration_s는 0보다 커야 합니다.")
    if fv.total_duration_s > config.MAX_TOTAL_DURATION_S:
        errors.append(
            f"final_video.total_duration_s({fv.total_duration_s})가 API 상한"
            f"({config.MAX_TOTAL_DURATION_S}초)을 초과합니다."
        )
    if abs(fv.total_duration_s - duration_s) > 0.5:
        warnings.append(
            f"final_video.total_duration_s({fv.total_duration_s})가 시나리오 duration_s"
            f"({duration_s})와 다릅니다."
        )

    return ValidationResult(errors=errors, warnings=warnings)
