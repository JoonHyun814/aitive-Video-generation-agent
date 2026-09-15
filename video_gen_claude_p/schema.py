"""Generation-plan data model.

The pipeline is: scenario.json -> (this schema, written by `claude -p` in one
structured-output call) -> deterministic Python executor that drives the
ComfyUI Workflow API (docs/api/Comfyui_API_SPEC.md). Mirrors
scenario_agent_claude/schema.py: pydantic is the single source of truth for
both the JSON Schema handed to `--json-schema` and the validation of what
comes back.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

EntityType = Literal["character", "location", "prop"]

AspectRatio = Literal[
    "16:9 (Widescreen)",
    "9:16 (Portrait)",
    "1:1 (Square)",
    "4:3 (Standard)",
    "21:9 (Cinematic)",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntityImagePlan(Strict):
    entity_type: EntityType
    entity_id: str
    is_product: bool
    # English Flux text-to-image prompt -- produces a draft. When is_product
    # is true this is never sent to Flux (the real product photo collected
    # from the source URL is used instead) -- still required non-empty so the
    # model records *why* (e.g. "실제 제품 사진 사용, 생성 생략").
    generation_prompt: str
    # English Qwen-Edit instruction that refines the Flux draft into the
    # final entity image (Qwen-Edit always takes an input image -- there is
    # no text-only entry point -- so the draft is the seed). Should describe
    # a quality/realism polish (fix anatomy, sharpen detail, correct
    # lighting/texture) WITHOUT changing subject or composition. Same
    # "실제 제품 사진 사용, 생성 생략" placeholder rule applies when is_product.
    refine_prompt: str
    width: int
    height: int


class SceneCutPlan(Strict):
    scene_no: int
    # 1-3 entity_ids (Qwen-Edit's hard cap), ordered by importance to this cut.
    ref_entity_ids: list[str]
    edit_prompt: str
    negative_prompt: str


class FinalVideoPlan(Strict):
    prompt: str
    total_duration_s: float
    aspect_ratio: AspectRatio
    reference_entity_id: str
    # <=3 scene_no values (MiniMax H3's hard cap), chronological order.
    cut_scene_nos: list[int]
    cut_times_s: list[float]


class GenerationPlan(Strict):
    entity_images: list[EntityImagePlan]
    scene_cuts: list[SceneCutPlan]
    final_video: FinalVideoPlan


# Same rationale as scenario_agent_claude/schema.py: keep the schema handed to
# `claude -p --json-schema` limited to the keywords that shape structure.
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
