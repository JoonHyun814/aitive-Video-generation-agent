"""CLI entrypoint: python -m scenario_agent_claude_litellm.cli --product-name ... --product-url ... [--duration 15]

LiteLLM-based counterpart to scenario_agent_claude.cli -- same inputs, same
output contract (scenario.json/.md/.html + validation_report.md), same
Scenario schema and validators (imported from scenario_agent_claude, the
data contract is shared and must stay in sync), different execution engine:
a manual tool-calling loop driven through the LiteLLM gateway instead of the
Claude Code CLI harness.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

from pydantic import ValidationError

from scenario_agent_claude.render import render_html, render_markdown
from scenario_agent_claude.schema import Scenario, lean_json_schema
from scenario_agent_claude.validators import ValidationResult, validate_scenario

from . import agent_loop, config
from .prompts import build_system_prompt, build_task_prompt
from .trace_render import render_trace_html

PROJECT_DIR = str(Path(__file__).resolve().parent.parent)


def _slug(text: str) -> str:
    s = re.sub(r"[^\w\-가-힣]+", "_", text).strip("_")
    return s or "product"


def _trace_totals(full_trace: list) -> dict:
    """Sum across every model call in the trace -- `result.total_cost_usd` only
    reflects the last (repair) call, not the whole run, since `result` is
    reassigned each repair round."""
    prompt_tokens = sum((s.get("usage") or {}).get("prompt_tokens") or 0 for s in full_trace)
    completion_tokens = sum((s.get("usage") or {}).get("completion_tokens") or 0 for s in full_trace)
    cached_tokens = sum((s.get("usage") or {}).get("cached_tokens") or 0 for s in full_trace)
    costs = [s.get("cost_usd") for s in full_trace if s.get("type") == "model_call"]
    cost_known = costs and all(c is not None for c in costs)
    total_cost = sum(c for c in costs if c is not None) if cost_known else None
    tool_calls = sum(1 for s in full_trace if s.get("type") == "tool_call")
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
        "total_cost_usd": total_cost,
        "tool_calls": tool_calls,
    }


def _format_report(
    validation: ValidationResult,
    totals: dict,
    repair_attempts_used: int,
) -> str:
    lines = ["# 검증 리포트 (LiteLLM 버전)\n"]
    lines.append(f"- model: {config.default_model()} (via {config.base_url()})")
    cost = (
        f"${totals['total_cost_usd']:.4f} (추정치 — 실제 청구액과 다를 수 있음)"
        if totals["total_cost_usd"] is not None
        else "알 수 없음(게이트웨이 모델 단가 미상)"
    )
    lines.append(f"- 누적 비용(전체 호출 합산): {cost}")
    lines.append(f"- 토큰: prompt={totals['prompt_tokens']}, completion={totals['completion_tokens']}, "
                 f"캐시 히트={totals['cached_tokens']}")
    lines.append(f"- 도구 호출 횟수: {totals['tool_calls']}")
    lines.append(f"- repair 시도 횟수: {repair_attempts_used}")
    lines.append(f"- 최종 상태: {'PASS' if validation.ok else 'FAIL (수동 검토 필요)'}")
    lines.append("- 단계별 상세 기록: trace.html\n")

    lines.append(f"## 오류 ({len(validation.errors)})")
    lines += [f"- {e}" for e in validation.errors] or ["- (없음)"]
    lines.append(f"\n## 경고 ({len(validation.warnings)})")
    lines += [f"- {w}" for w in validation.warnings] or ["- (없음)"]

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="광고 영상 제작 직전 시나리오 생성 에이전트 (LiteLLM)")
    ap.add_argument("--product-name", required=True)
    ap.add_argument("--product-url", required=True)
    ap.add_argument("--duration", type=float, default=15.0)
    ap.add_argument("--output-dir", default=str(Path(PROJECT_DIR) / "output"))
    ap.add_argument("--model", default=None, help="기본값: .env의 LITELLM_MODEL")
    ap.add_argument("--max-budget-usd", type=float, default=4.0)
    ap.add_argument("--repair-attempts", type=int, default=2)
    args = ap.parse_args(argv)

    try:
        config.base_url()
        config.api_key()
    except config.ConfigError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 1

    system_prompt = build_system_prompt()
    task_prompt = build_task_prompt(args.product_name, args.product_url, args.duration)
    schema = lean_json_schema()

    print(f"[1/3] 에이전트 실행 중 (model={args.model or config.default_model()}, budget=${args.max_budget_usd})...")
    result = agent_loop.run_scenario_generation(
        system_prompt=system_prompt,
        task_prompt=task_prompt,
        schema=schema,
        model=args.model,
        max_budget_usd=args.max_budget_usd,
    )
    full_trace: list = list(result.trace)
    if not result.ok:
        print(f"실패: {result.error_message}", file=sys.stderr)
        return 1

    scenario, validation = _parse_and_validate(result.structured_output)
    repair_attempts_used = 0

    while (
        (scenario is None or not validation.ok)
        and repair_attempts_used < args.repair_attempts
    ):
        repair_attempts_used += 1
        print(f"[repair {repair_attempts_used}/{args.repair_attempts}] 검증 실패, 수정 요청 중...")
        previous_output = result.structured_output
        result = agent_loop.run_repair(
            messages=result.messages,
            errors=validation.errors,
            schema=schema,
            previous_output=previous_output,
            model=args.model,
            max_budget_usd=args.max_budget_usd / 2,
            attempt=repair_attempts_used,
        )
        full_trace.extend(result.trace)
        if not result.ok:
            print(f"repair 호출 실패: {result.error_message}", file=sys.stderr)
            break
        scenario, validation = _parse_and_validate(result.structured_output)

    out_dir = Path(args.output_dir) / f"{_slug(args.product_name)}_{datetime.datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)

    totals = _trace_totals(full_trace)

    (out_dir / "scenario.json").write_text(
        json.dumps(result.structured_output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "transcript.json").write_text(
        json.dumps(result.messages, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "trace.json").write_text(
        json.dumps(full_trace, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    trace_meta = {"product_name": args.product_name, **totals}
    (out_dir / "trace.html").write_text(render_trace_html(full_trace, trace_meta), encoding="utf-8")
    if scenario is not None:
        (out_dir / "scenario.md").write_text(render_markdown(scenario), encoding="utf-8")
        (out_dir / "scenario.html").write_text(render_html(scenario), encoding="utf-8")
    report = _format_report(validation, totals, repair_attempts_used)
    (out_dir / "validation_report.md").write_text(report, encoding="utf-8")

    print(f"\n[2/3] 출력 위치: {out_dir}")
    print(f"[3/3] {'PASS' if validation.ok else 'FAIL — validation_report.md 확인 필요'}")
    if validation.warnings:
        print(f"경고 {len(validation.warnings)}건 (validation_report.md 참고)")
    return 0 if validation.ok else 2


def _parse_and_validate(structured: dict | None) -> tuple[Scenario | None, ValidationResult]:
    if structured is None:
        return None, ValidationResult(errors=["structured_output이 없습니다."])
    try:
        scenario = Scenario.model_validate(structured)
    except ValidationError as e:
        return None, ValidationResult(errors=[f"스키마 검증 실패: {e}"])
    return scenario, validate_scenario(scenario)


if __name__ == "__main__":
    raise SystemExit(main())
