"""Structural validation of a GenerationPlan against the source scenario.

Adapted from video_gen_claude_p (v1)'s validators.py for this package's
SceneCutPlan/FinalVideoPlan shapes (schema.py): because a cut's three
reference groups (character_ids/location_id/prop_ids) are separate fields
instead of one flat ref_entity_ids list, the "group count > MAX_QWEN_REF_IMAGES"
check v1 needed no longer applies -- it's structurally impossible to exceed
3 groups. What remains is checking each field's ids actually belong to that
scene and to the right entity type.
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

    expected_type = {}
    for eid in char_ids:
        expected_type[eid] = "character"
    for eid in loc_ids:
        expected_type[eid] = "location"
    for eid in prop_ids:
        expected_type[eid] = "prop"

    # --- character_assets ---
    plan_char_ids = [c.entity_id for c in plan.character_assets]
    plan_char_id_set = set(plan_char_ids)
    if len(plan_char_ids) != len(plan_char_id_set):
        errors.append("character_assets에 중복된 entity_id가 있습니다.")

    for c in plan.character_assets:
        want = expected_type.get(c.entity_id)
        if want and want != "character":
            errors.append(
                f"entity_id={c.entity_id}는 시나리오상 {want}인데 character_assets에 있습니다."
            )
        if c.width <= 0 or c.height <= 0:
            errors.append(f"entity_id={c.entity_id}의 width/height가 올바르지 않습니다.")
        if not c.clothing_reference_url and not c.clothing_generation_prompt:
            errors.append(
                f"entity_id={c.entity_id}: clothing_reference_url과 "
                f"clothing_generation_prompt가 둘 다 비어 있어 복장을 만들 방법이 없습니다."
            )
        if not c.hair_reference_url and not c.hair_generation_prompt:
            errors.append(
                f"entity_id={c.entity_id}: hair_reference_url과 hair_generation_prompt가 "
                f"둘 다 비어 있어 머리를 만들 방법이 없습니다."
            )

    # --- entity_images (locations/props) ---
    plan_entity_ids = [e.entity_id for e in plan.entity_images]
    plan_entity_id_set = set(plan_entity_ids)
    if len(plan_entity_ids) != len(plan_entity_id_set):
        errors.append("entity_images에 중복된 entity_id가 있습니다.")

    # Usage-driven, not scenario-driven: a scenario character/location/prop
    # never picked by any scene_cuts field or final_video anchor field (e.g.
    # an off-screen narrator) is legitimately absent here.
    plan_all_ids = plan_char_id_set | plan_entity_id_set
    referenced_ids: set[str] = set()
    for sc in plan.scene_cuts:
        referenced_ids.update(sc.character_ids)
        if sc.location_id:
            referenced_ids.add(sc.location_id)
        referenced_ids.update(sc.prop_ids)
    referenced_ids.update(plan.final_video.anchor_character_ids)
    referenced_ids.update(plan.final_video.anchor_prop_ids)

    missing = referenced_ids & (all_entity_ids - plan_all_ids)
    if missing:
        errors.append(
            f"scene_cuts/final_video가 참조하지만 character_assets/entity_images에 없는 "
            f"entity: {sorted(missing)}"
        )
    extra = plan_all_ids - all_entity_ids
    if extra:
        errors.append(f"시나리오에 없는 entity_id가 있습니다: {sorted(extra)}")
    unused = plan_all_ids - referenced_ids
    if unused:
        warnings.append(
            f"만들었지만 어느 scene_cut/final_video에서도 참조되지 않아 생성이 낭비되는 "
            f"entity: {sorted(unused)}"
        )

    for e in plan.entity_images:
        want = expected_type.get(e.entity_id)
        if want and e.entity_type != want:
            errors.append(
                f"entity_id={e.entity_id}의 entity_type이 {e.entity_type}이지만 "
                f"시나리오상 {want}입니다."
            )
        if e.width <= 0 or e.height <= 0:
            errors.append(f"entity_id={e.entity_id}의 width/height가 올바르지 않습니다.")
        if e.is_logo and not e.reference_image_url:
            errors.append(
                f"entity_id={e.entity_id}: is_logo=true인데 reference_image_url이 "
                f"비어 있습니다 — 로고는 반드시 웹검색으로 실제 이미지를 찾아야 합니다."
            )

    product_entities = [e for e in plan.entity_images if e.is_product]
    if len(product_entities) > 1:
        errors.append(
            f"is_product=true인 entity가 {len(product_entities)}개입니다 (최대 1개여야 함): "
            f"{[e.entity_id for e in product_entities]}"
        )
    elif len(product_entities) == 0:
        warnings.append(
            "is_product=true로 표시된 entity가 없습니다 — 앵커 프레임이 실제 제품 사진 대신 "
            "생성 이미지를 참조하게 됩니다."
        )
    if product_entities and product_entities[0].entity_type != "prop":
        errors.append("is_product=true인 entity는 반드시 entity_type=prop이어야 합니다.")

    # --- 리서치 활용도(soft signal) ---
    search_opportunities = 0
    search_filled = 0
    for c in plan.character_assets:
        search_opportunities += 2
        search_filled += bool(c.clothing_reference_url) + bool(c.hair_reference_url)
    for e in plan.entity_images:
        if e.is_product or e.is_logo:
            continue
        search_opportunities += 1
        search_filled += bool(e.reference_image_url)
    if search_opportunities >= 3 and search_filled == 0:
        warnings.append(
            f"검색으로 채울 수 있었던 참조 필드가 {search_opportunities}개인데 하나도 "
            f"채워지지 않았습니다 — web_search/web_fetch 리서치를 충분히 시도하지 않았을 "
            f"수 있습니다(시스템 프롬프트의 '0단계 리서치 절차' 참고)."
        )

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
        if not sc.character_ids and not sc.location_id and not sc.prop_ids:
            errors.append(f"scene {sc.scene_no}: character_ids/location_id/prop_ids가 모두 비어 있습니다.")
        if len(sc.character_ids) != len(set(sc.character_ids)):
            errors.append(f"scene {sc.scene_no}: character_ids에 중복된 entity_id가 있습니다.")
        if len(sc.prop_ids) != len(set(sc.prop_ids)):
            errors.append(f"scene {sc.scene_no}: prop_ids에 중복된 entity_id가 있습니다.")

        unknown_chars = [eid for eid in sc.character_ids if eid not in char_ids]
        if unknown_chars:
            errors.append(f"scene {sc.scene_no}: 존재하지 않는 character entity_id 참조: {unknown_chars}")
        if sc.location_id and sc.location_id not in loc_ids:
            errors.append(f"scene {sc.scene_no}: 존재하지 않는 location entity_id 참조: {sc.location_id}")
        unknown_props = [eid for eid in sc.prop_ids if eid not in prop_ids]
        if unknown_props:
            errors.append(f"scene {sc.scene_no}: 존재하지 않는 prop entity_id 참조: {unknown_props}")

        scene = scenes_by_no.get(sc.scene_no)
        if scene is not None:
            usable_chars = set(scene.get("character_ids", []))
            usable_loc = scene.get("location_id")
            usable_props = set(scene.get("prop_ids", []))
            off_scene = (
                [eid for eid in sc.character_ids if eid in char_ids and eid not in usable_chars]
                + ([sc.location_id] if sc.location_id and sc.location_id != usable_loc else [])
                + [eid for eid in sc.prop_ids if eid in prop_ids and eid not in usable_props]
            )
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

    if not fv.anchor_character_ids and not fv.anchor_prop_ids:
        errors.append(
            "final_video: anchor_character_ids와 anchor_prop_ids가 모두 비어 있어 앵커 "
            "프레임을 만들 수 없습니다."
        )
    unknown_anchor_chars = [eid for eid in fv.anchor_character_ids if eid not in char_ids]
    if unknown_anchor_chars:
        errors.append(f"final_video.anchor_character_ids에 존재하지 않는 entity_id: {unknown_anchor_chars}")
    unknown_anchor_props = [eid for eid in fv.anchor_prop_ids if eid not in prop_ids]
    if unknown_anchor_props:
        errors.append(f"final_video.anchor_prop_ids에 존재하지 않는 entity_id: {unknown_anchor_props}")
    if not fv.anchor_prompt:
        errors.append("final_video.anchor_prompt가 비어 있습니다.")
    if product_entities and product_entities[0].entity_id not in fv.anchor_prop_ids:
        warnings.append(
            f"final_video.anchor_prop_ids가 제품 entity({product_entities[0].entity_id})를 "
            "포함하지 않습니다 — 앵커 프레임이 제품 사진을 참조하지 않게 됩니다."
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
