"""CLI entrypoint: python -m scenario_agent_claude.cli --product-name ... --product-url ... [--duration 15]"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

from pydantic import ValidationError

from . import runner
from .prompts import build_system_prompt, build_task_prompt
from .render import render_html, render_markdown
from .runner import RAG_SERVER_NAME, RAG_SERVER_URL
from .schema import Scenario
from .validators import ValidationResult, validate_scenario

PROJECT_DIR = str(Path(__file__).resolve().parent.parent)


def _slug(text: str) -> str:
    s = re.sub(r"[^\w\-가-힣]+", "_", text).strip("_")
    return s or "product"


def _format_report(
    validation: ValidationResult,
    cost_usd: float | None,
    session_id: str | None,
    permission_denials: list,
    repair_attempts_used: int,
) -> str:
    lines = ["# 검증 리포트\n"]
    lines.append(f"- session_id: {session_id}")
    lines.append(f"- 누적 비용(USD): {cost_usd}")
    lines.append(f"- repair 시도 횟수: {repair_attempts_used}")
    lines.append(f"- 최종 상태: {'PASS' if validation.ok else 'FAIL (수동 검토 필요)'}\n")

    lines.append(f"## 오류 ({len(validation.errors)})")
    lines += [f"- {e}" for e in validation.errors] or ["- (없음)"]
    lines.append(f"\n## 경고 ({len(validation.warnings)})")
    lines += [f"- {w}" for w in validation.warnings] or ["- (없음)"]

    if permission_denials:
        lines.append("\n## 거부된 도구 호출")
        lines += [f"- {d}" for d in permission_denials]

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="광고 영상 제작 직전 시나리오 생성 에이전트")
    ap.add_argument("--product-name", required=True)
    ap.add_argument("--product-url", required=True)
    ap.add_argument("--duration", type=float, default=15.0)
    ap.add_argument("--output-dir", default=str(Path(PROJECT_DIR) / "output"))
    ap.add_argument("--model", default="opus", help="claude 모델 별칭 (opus/sonnet/fable 등)")
    ap.add_argument("--max-budget-usd", type=float, default=4.0)
    ap.add_argument("--repair-attempts", type=int, default=2)
    ap.add_argument("--rag-server-url", default=RAG_SERVER_URL)
    args = ap.parse_args(argv)

    system_prompt = build_system_prompt(RAG_SERVER_NAME)
    task_prompt = build_task_prompt(args.product_name, args.product_url, args.duration)

    print(f"[1/3] claude -p 실행 중 (model={args.model}, budget=${args.max_budget_usd})...")
    result = runner.run_scenario_generation(
        project_dir=PROJECT_DIR,
        system_prompt=system_prompt,
        task_prompt=task_prompt,
        model=args.model,
        max_budget_usd=args.max_budget_usd,
        rag_server_url=args.rag_server_url,
    )
    if not result.ok:
        print(f"실패: {result.error_message}", file=sys.stderr)
        return 1

    scenario, validation = _parse_and_validate(result.structured_output)
    repair_attempts_used = 0

    while (
        (scenario is None or not validation.ok)
        and repair_attempts_used < args.repair_attempts
        and result.session_id
    ):
        repair_attempts_used += 1
        print(f"[repair {repair_attempts_used}/{args.repair_attempts}] 검증 실패, 수정 요청 중...")
        repair_prompt = _build_repair_prompt(validation)
        result = runner.run_repair(
            project_dir=PROJECT_DIR,
            session_id=result.session_id,
            repair_prompt=repair_prompt,
            system_prompt=system_prompt,
            model=args.model,
            max_budget_usd=args.max_budget_usd,
            rag_server_url=args.rag_server_url,
        )
        if not result.ok:
            print(f"repair 호출 실패: {result.error_message}", file=sys.stderr)
            break
        scenario, validation = _parse_and_validate(result.structured_output)

    out_dir = Path(args.output_dir) / f"{_slug(args.product_name)}_{datetime.datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "scenario.json").write_text(
        json.dumps(result.structured_output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "raw_cli_output.json").write_text(
        json.dumps(result.raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if scenario is not None:
        (out_dir / "scenario.md").write_text(render_markdown(scenario), encoding="utf-8")
        (out_dir / "scenario.html").write_text(render_html(scenario), encoding="utf-8")
    report = _format_report(
        validation, result.total_cost_usd, result.session_id, result.permission_denials,
        repair_attempts_used,
    )
    (out_dir / "validation_report.md").write_text(report, encoding="utf-8")

    print(f"\n[2/3] 출력 위치: {out_dir}")
    print(f"[3/3] {'PASS' if validation.ok else 'FAIL — validation_report.md 확인 필요'}")
    if validation.warnings:
        print(f"경고 {len(validation.warnings)}건 (validation_report.md 참고)")
    return 0 if validation.ok else 2


def _parse_and_validate(
    structured: dict | None,
) -> tuple[Scenario | None, ValidationResult]:
    if structured is None:
        return None, ValidationResult(errors=["structured_output이 없습니다."])
    try:
        scenario = Scenario.model_validate(structured)
    except ValidationError as e:
        return None, ValidationResult(errors=[f"스키마 검증 실패: {e}"])
    return scenario, validate_scenario(scenario)


def _build_repair_prompt(validation: ValidationResult) -> str:
    errors = "\n".join(f"- {e}" for e in validation.errors)
    return (
        "방금 만든 시나리오에 다음 검증 오류가 있다. 각 오류를 정확히 고쳐서 "
        "전체 시나리오를 다시 스키마에 맞게 완전한 형태로 다시 출력하라 "
        "(일부만 수정한 조각이 아니라 완전한 JSON 전체를 다시 출력할 것):\n\n"
        f"{errors}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
