"""Deterministic checks on top of pydantic's structural validation.

Split into `errors` (structural/factual breaks -> trigger a repair pass) and
`warnings` (the ad-theory heuristics are guidance, not hard rules -- reported
to the user, never auto-repaired).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .schema import Scenario

_WS_RE = re.compile(r"\s+")
_PAREN_RE = re.compile(r"[\(（][^)）]*[\)）]")


def _normalize(text: str) -> str:
    return _WS_RE.sub("", text).lower()


def _primary_name(product_name: str) -> str:
    """Product names often carry a parenthetical translation ('다이슨 에어랩
    (Dyson Airwrap)') or a trailing model number ('다트비트 홈다트 DBH100') that real
    ad copy usually drops. Presence checks should match on the first token of the
    name (the actual brand/product word people say), not the full display string."""
    stripped = _PAREN_RE.sub("", product_name).strip()
    first_token = stripped.split()[0] if stripped.split() else stripped
    return _normalize(first_token)


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_scenario(scenario: Scenario) -> ValidationResult:
    result = ValidationResult()
    _check_duration_coverage(scenario, result)
    _check_referential_integrity(scenario, result)
    _check_claim_support(scenario, result)
    _check_product_presence(scenario, result)
    _check_brand_early_exposure(scenario, result)
    _check_closing_scene(scenario, result)
    _check_cut_count(scenario, result)
    _check_reading_speed(scenario, result)
    return result


def _check_duration_coverage(scenario: Scenario, result: ValidationResult) -> None:
    scenes = sorted(scenario.scenes, key=lambda s: s.start_s)
    if not scenes:
        result.errors.append("scenes가 비어 있습니다.")
        return

    eps = 0.05
    if abs(scenes[0].start_s - 0.0) > eps:
        result.errors.append(f"첫 씬은 0초에서 시작해야 합니다 (실제: {scenes[0].start_s}초).")
    if abs(scenes[-1].end_s - scenario.duration_s) > eps:
        result.errors.append(
            f"마지막 씬은 duration_s({scenario.duration_s}초)에서 끝나야 합니다 "
            f"(실제: {scenes[-1].end_s}초)."
        )
    for prev, nxt in zip(scenes, scenes[1:]):
        if prev.end_s <= prev.start_s:
            result.errors.append(f"씬 {prev.scene_no}: end_s가 start_s보다 크지 않습니다.")
        if abs(nxt.start_s - prev.end_s) > eps:
            result.errors.append(
                f"씬 {prev.scene_no}(끝 {prev.end_s}초)와 씬 {nxt.scene_no}"
                f"(시작 {nxt.start_s}초) 사이에 빈틈 또는 겹침이 있습니다."
            )


def _check_referential_integrity(scenario: Scenario, result: ValidationResult) -> None:
    char_ids = {c.id for c in scenario.characters}
    loc_ids = {l.id for l in scenario.locations}
    prop_ids = {p.id for p in scenario.props}

    for scene in scenario.scenes:
        if scene.location_id not in loc_ids:
            result.errors.append(
                f"씬 {scene.scene_no}: 존재하지 않는 location_id '{scene.location_id}'."
            )
        for cid in scene.character_ids:
            if cid not in char_ids:
                result.errors.append(f"씬 {scene.scene_no}: 존재하지 않는 character_id '{cid}'.")
        for pid in scene.prop_ids:
            if pid not in prop_ids:
                result.errors.append(f"씬 {scene.scene_no}: 존재하지 않는 prop_id '{pid}'.")


def _check_claim_support(scenario: Scenario, result: ValidationResult) -> None:
    all_claims = (
        scenario.product.ingredients
        + scenario.product.features
        + scenario.product.appearance
        + scenario.product.other_claims
    )
    scene_text = _normalize(
        " ".join(f"{s.dialogue} {s.subtitle} {s.action}" for s in scenario.scenes)
    )

    sources = set(scenario.sources)
    for claim in all_claims:
        if claim.support == "fully_supported":
            if not claim.source_quote.strip() or not claim.source_url.strip():
                result.errors.append(
                    f"claim '{claim.claim}'은 fully_supported인데 source_quote/source_url이 "
                    "비어 있습니다."
                )
            elif claim.source_url not in sources:
                result.errors.append(
                    f"claim '{claim.claim}'의 source_url({claim.source_url})이 "
                    "scenario.sources 목록에 없습니다 (출처 투명성 위반 — 인용한 URL은 "
                    "모두 sources에 나열되어야 합니다)."
                )
        else:
            needle = _normalize(claim.claim)
            if needle and needle in scene_text:
                result.errors.append(
                    f"claim '{claim.claim}'은 support='{claim.support}'인데 씬 텍스트에 "
                    "그대로 등장합니다 (사실 기반 원칙 위반)."
                )


def _check_product_presence(scenario: Scenario, result: ValidationResult) -> None:
    name = _primary_name(scenario.product.name)
    haystack = _normalize(
        " ".join(f"{s.dialogue} {s.subtitle} {s.action}" for s in scenario.scenes)
        + " ".join(p.name for p in scenario.props)
    )
    if name and name not in haystack:
        result.warnings.append(
            "제품명이 어떤 씬의 대사/자막/행동이나 소품 이름에도 등장하지 않습니다."
        )


def _check_brand_early_exposure(scenario: Scenario, result: ValidationResult) -> None:
    name = _primary_name(scenario.product.name)
    early = [s for s in scenario.scenes if s.start_s < 5.0]
    haystack = _normalize(
        " ".join(f"{s.dialogue} {s.subtitle} {s.action}" for s in early)
        + " ".join(
            p.name for p in scenario.props if p.id in {pid for s in early for pid in s.prop_ids}
        )
    )
    if name and name not in haystack:
        result.warnings.append(
            "Stewart & Furse(1986): 기억도 향상을 위해 보통 초반 3~5초 내 브랜드/제품 노출이 "
            "권장되나, 이 시나리오는 그렇지 않습니다 (의도된 훅 전략이면 무시 가능)."
        )


def _check_closing_scene(scenario: Scenario, result: ValidationResult) -> None:
    if not scenario.scenes:
        return
    last = max(scenario.scenes, key=lambda s: s.end_s)
    if last.narrative_role not in ("CTA", "BRAND_CLOSE"):
        result.warnings.append(
            f"마지막 씬(#{last.scene_no})의 narrative_role이 '{last.narrative_role}'입니다. "
            "보통 CTA 또는 BRAND_CLOSE로 마무리하는 것이 구매 전환에 유리합니다."
        )


def _check_cut_count(scenario: Scenario, result: ValidationResult) -> None:
    # Heuristic ceiling: roughly one cut per ~2.5s keeps a short ad legible (MOA-Opportunity).
    max_cuts = math.ceil(scenario.duration_s / 2.5) + 1
    if len(scenario.scenes) > max_cuts:
        result.warnings.append(
            f"씬(컷) 수가 {len(scenario.scenes)}개로, {scenario.duration_s:g}초 길이 대비 "
            f"권장 상한(~{max_cuts}개)을 초과합니다. 정보 과부하로 Opportunity가 저해될 수 있습니다."
        )


def _check_reading_speed(scenario: Scenario, result: ValidationResult) -> None:
    # Korean subtitle ~4-5 chars/sec, spoken dialogue ~5-6 syllables/sec.
    for s in scenario.scenes:
        dur = max(s.end_s - s.start_s, 0.01)
        if len(s.subtitle) > dur * 5:
            result.warnings.append(
                f"씬 {s.scene_no}: 자막 길이({len(s.subtitle)}자)가 씬 길이({dur:g}초) 대비 "
                "가독 속도(초당 ~5자)를 초과할 수 있습니다."
            )
        if len(s.dialogue) > dur * 6:
            result.warnings.append(
                f"씬 {s.scene_no}: 대사 길이({len(s.dialogue)}자)가 씬 길이({dur:g}초) 대비 "
                "발화 속도(초당 ~6음절)를 초과할 수 있습니다."
            )
