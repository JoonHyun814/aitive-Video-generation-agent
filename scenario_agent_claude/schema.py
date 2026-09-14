"""Scenario data model.

This is the single source of truth: the same pydantic models produce the
JSON Schema handed to `claude -p --json-schema` (via ``lean_json_schema``)
and validate the structured output that comes back.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# Matches the `role` enum of the RAG server's `search_graph_pattern` tool
# (see docs/rag/rag_server_info.md) so scenario scenes and RAG-retrieved
# creative-element statistics share one vocabulary.
NarrativeRole = Literal[
    "HOOK",
    "ESTABLISH_CONTEXT",
    "PROBLEM",
    "EMOTIONAL_APPEAL",
    "FEATURE",
    "DEMO",
    "TESTIMONIAL",
    "SOCIAL_PROOF",
    "CTA",
    "BRAND_CLOSE",
]

SupportLevel = Literal["fully_supported", "partially_supported", "no_support"]
# No "not_applicable" escape hatch by design: a category that genuinely doesn't
# apply to the product (e.g. no ingredients for a hair styler) should be an
# empty list, explained in ProductFacts.unverifiable_notes -- not a claim
# entry that dodges the source_quote requirement.

FcbQuadrant = Literal["informative", "affective", "habit_formation", "self_satisfaction"]

HierarchyStage = Literal[
    "awareness", "knowledge", "liking", "preference", "conviction", "purchase"
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourcedClaim(Strict):
    claim: str
    source_quote: str
    source_url: str
    support: SupportLevel


class ProductFacts(Strict):
    name: str
    source_url: str
    ingredients: list[SourcedClaim]
    features: list[SourcedClaim]
    appearance: list[SourcedClaim]
    other_claims: list[SourcedClaim]
    unverifiable_notes: str


class Character(Strict):
    id: str
    name: str
    role_description: str
    appearance: str


class Location(Strict):
    id: str
    name: str
    description: str


class Prop(Strict):
    id: str
    name: str
    description: str
    related_claim: str


class Scene(Strict):
    scene_no: int
    start_s: float
    end_s: float
    narrative_role: NarrativeRole
    location_id: str
    character_ids: list[str]
    prop_ids: list[str]
    action: str
    dialogue: str
    subtitle: str
    sound_music_sfx: str
    visual_effects: str
    camera_notes: str


class CreativeStrategy(Strict):
    fcb_quadrant: FcbQuadrant
    target_hierarchy_stage: HierarchyStage
    rationale: str


class Scenario(Strict):
    product: ProductFacts
    duration_s: float
    characters: list[Character]
    locations: list[Location]
    props: list[Prop]
    creative_strategy: CreativeStrategy
    scenes: list[Scene]
    sources: list[str]
    rag_queries_used: list[str]


# Validation keywords that are risky/undocumented for `claude -p --json-schema`
# (advisor guidance: keep the handed-to-the-model schema to the keywords that
# actually shape structure -- type/properties/required/enum/$ref -- and let
# pydantic enforce string/array length constraints on the Python side).
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
    """JSON Schema for `claude -p --json-schema`, stripped of untested keywords."""
    return _strip(Scenario.model_json_schema())
