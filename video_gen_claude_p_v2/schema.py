"""Generation-plan data model.

The pipeline is: scenario.json -> (this schema, written by the planning
agent's final structured-output call, see agent_loop.py) -> deterministic
Python executor that drives the ComfyUI Workflow API
(docs/api/Comfyui_API_SPEC.md).

Unlike video_gen_claude_p (v1)'s SceneCutPlan.ref_entity_ids (a flat list
whose "group count" validators.py had to check post-hoc, and could exceed
Qwen-Edit's 3-reference-slot limit for scenes with many characters),
SceneCutPlan here has exactly three fixed reference groups -- character_ids /
location_id / prop_ids -- so the number of Qwen-Edit reference slots a cut
needs (at most one grid per group) can never exceed 3 by construction. See
FinalVideoPlan for the analogous "anchor frame" fix for MiniMax's single
reference_image slot.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

EntityType = Literal["location", "prop"]

AspectRatio = Literal[
    "16:9 (Widescreen)",
    "9:16 (Portrait)",
    "1:1 (Square)",
    "4:3 (Standard)",
    "21:9 (Cinematic)",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CharacterAssetPlan(Strict):
    """A character is built from three independently-sourced pieces instead
    of one Flux call, so wardrobe/hair accuracy doesn't depend on Flux
    imagining them correctly:
      - face_body: always Flux, no reference -- the only piece that invents a
        face, deliberately generic/neutral (see prompts.py's fixed wording)
        so wardrobe accuracy never depends on it.
      - clothing / hair: WebSearch/WebFetch reference preferred (real photo,
        privacy-scrubbed by executor.py before use -- faces always blurred,
        text blurred too since a character's outfit reference is never
        is_product/is_logo), Flux fallback only when no usable reference was
        found.
    executor.py composites the three into one labeled grid image -- this
    character's single asset image, later combined with any other on-screen
    characters into a cut's "person grid" (see SceneCutPlan)."""

    entity_id: str
    face_body_prompt: str
    width: int
    height: int
    # "" if no usable real photo was found -- then clothing_generation_prompt
    # is used instead (Flux). Never both: exactly one of the two paths runs.
    clothing_reference_url: str
    clothing_generation_prompt: str
    hair_reference_url: str
    hair_generation_prompt: str


class EntityImagePlan(Strict):
    """Locations and (non-character) props. Unlike characters, these are
    single images -- either downloaded-and-privacy-scrubbed from a search
    result, or a plain Flux generation, never both."""

    entity_type: EntityType
    entity_id: str
    is_product: bool
    # A brand logo / wordmark prop -- unlike other props, reference_image_url
    # is REQUIRED for these (see validators.py): a redrawn logo is a brand
    # accuracy failure a generic Flux fallback can't safely paper over.
    is_logo: bool
    # English Flux text-to-image prompt (docs/api/FLUX_PROMPT.md
    # conventions). Used only when reference_image_url is "" (Flux path);
    # when is_product is true this is never sent to Flux at all (the real
    # product photo collected from the scenario's own product.appearance
    # URLs is used instead) -- still required non-empty so the plan records
    # *why* (e.g. "실제 제품 사진 사용, 생성 생략").
    generation_prompt: str
    width: int
    height: int
    # Direct image URL found via WebSearch/WebFetch during planning. Required
    # (non-empty) when is_logo; irrelevant and left "" when is_product (that
    # path pulls its photo from the scenario's own product.appearance URLs
    # instead). For everything else, preferred when a real photo would
    # materially help and "" otherwise -- then generation_prompt (Flux) is
    # used instead. The executor privacy-scrubs (always faces, plus text
    # unless is_product/is_logo) every reference it downloads here.
    reference_image_url: str


class SceneCutPlan(Strict):
    """Exactly three reference groups -- at most one Qwen-Edit image slot
    each -- so a cut can never need more than MAX_QWEN_REF_IMAGES regardless
    of how many characters/props it lists. All ids must belong to this
    scene's own character_ids/location_id/prop_ids in scenario.json."""

    scene_no: int
    # 0+ characters on screen in this cut. Every character listed here is
    # composited into a single "person grid" (each character's own
    # face/clothing/hair asset grid, arranged side by side with a per-
    # character label) -- one Qwen-Edit reference slot no matter how many.
    character_ids: list[str]
    # "" if no location is referenced in this cut, else exactly one
    # entity_id (a location's own asset image, one Qwen-Edit reference slot).
    location_id: str
    # 0+ props/logos in this cut, composited into a single "prop grid" --
    # one Qwen-Edit reference slot no matter how many.
    prop_ids: list[str]
    edit_prompt: str
    negative_prompt: str


class FinalVideoPlan(Strict):
    """MiniMax's /minimax/generate takes exactly one reference_image (the
    first-frame anchor) plus up to MAX_MINIMAX_CUTS cut_images. To satisfy
    "제품/로고 정확성 + 주인공 얼굴 유지"의 both at once in that single slot,
    the executor runs one extra Qwen-Edit composite (same mechanism as a
    scene cut, not a raw label grid handed to MiniMax) combining
    anchor_character_ids (person grid) and anchor_prop_ids (prop grid) per
    anchor_prompt, and that composite becomes reference_image."""

    prompt: str
    total_duration_s: float
    aspect_ratio: AspectRatio
    # Usually the protagonist(s) whose face must stay recognizable across the
    # ad. May be empty only if no character's identity needs anchoring.
    anchor_character_ids: list[str]
    # Usually the is_product/is_logo entities -- the things that must render
    # accurately. May be empty only if there's truly no product/logo prop.
    anchor_prop_ids: list[str]
    anchor_prompt: str
    # <=MAX_MINIMAX_CUTS scene_no values (MiniMax's hard cap), chronological order.
    cut_scene_nos: list[int]
    cut_times_s: list[float]


class GenerationPlan(Strict):
    character_assets: list[CharacterAssetPlan]
    entity_images: list[EntityImagePlan]
    scene_cuts: list[SceneCutPlan]
    final_video: FinalVideoPlan


# Keep the schema handed to response_format=json_schema limited to the
# keywords that shape structure -- same rationale as scenario_agent_claude's
# schema.py (extra constraint keywords add tokens without helping a strict
# json_schema call, which already enforces required/type/enum).
_STRIP_KEYS = {
    "minItems", "maxItems", "minLength", "maxLength",
    "pattern", "minimum", "maximum", "title", "default",
}


def _strip(node: object) -> object:
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k not in _STRIP_KEYS}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


def lean_json_schema() -> dict:
    return _strip(GenerationPlan.model_json_schema())
