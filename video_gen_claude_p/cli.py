"""CLI entrypoint.

python -m video_gen_claude_p.cli --scenario-json <scenario.json 경로> [--plan-only]
python -m video_gen_claude_p.cli --scenario-json <scenario.json 경로> --from-plan <plan.json 경로>

Pipeline (docs/api/Comfyui_API_SPEC.md 기반):
  1. claude -p 한 번으로 scenario.json -> GenerationPlan(구조화 출력) 생성, 검증 실패 시
     최대 --repair-attempts번 --resume 보수 호출.
     (--from-plan이 지정되면 이 단계 전체를 건너뛰고 기존 plan.json을 그대로 쓴다 --
     예: --plan-only로 뽑아둔 계획을 검토/수정한 뒤 이어서 실행하고 싶을 때.)
  2. (--plan-only가 아니면) 결정론적 Python 실행기가 ComfyUI Workflow API를 호출해
     entity 레퍼런스 이미지 -> 컷 프레임 -> 최종 영상을 순서대로 만든다.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

# Windows consoles often default to a legacy codepage (cp949/cp1252) that
# can't encode the em dashes etc. used in these status messages.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from pydantic import ValidationError

from . import planner
from .executor import run_pipeline
from .prompts import build_repair_prompt, build_task_prompt
from .report import render_html
from .schema import GenerationPlan
from .validators import ValidationResult, validate_plan

PROJECT_DIR = str(Path(__file__).resolve().parent.parent)


def _slug(text: str) -> str:
    s = re.sub(r"[^\w\-가-힣]+", "_", text).strip("_")
    return s or "video"


def _parse_and_validate(structured: dict | None, scenario: dict) -> tuple[GenerationPlan | None, ValidationResult]:
    if structured is None:
        return None, ValidationResult(errors=["structured_output이 없습니다."])
    try:
        plan = GenerationPlan.model_validate(structured)
    except ValidationError as e:
        return None, ValidationResult(errors=[f"스키마 검증 실패: {e}"])
    return plan, validate_plan(plan, scenario)


def _format_plan_report(validation: ValidationResult, cost_usd, repair_attempts_used: int) -> str:
    lines = ["# 생성 계획(Plan) 검증 리포트\n"]
    lines.append(f"- 누적 비용(USD, planning 호출만): {cost_usd}")
    lines.append(f"- repair 시도 횟수: {repair_attempts_used}")
    lines.append(f"- 최종 상태: {'PASS' if validation.ok else 'FAIL (수동 검토 필요)'}\n")
    lines.append(f"## 오류 ({len(validation.errors)})")
    lines += [f"- {e}" for e in validation.errors] or ["- (없음)"]
    lines.append(f"\n## 경고 ({len(validation.warnings)})")
    lines += [f"- {w}" for w in validation.warnings] or ["- (없음)"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="시나리오 -> 이미지/영상 생성 파이프라인 (ComfyUI 기반)")
    ap.add_argument("--scenario-json", required=True, help="scenario_agent_claude(_litellm) 산출물 scenario.json 경로")
    ap.add_argument("--output-dir", default=str(Path(PROJECT_DIR) / "output" / "video_gen"))
    ap.add_argument("--model", default="opus")
    ap.add_argument("--max-budget-usd", type=float, default=1.5)
    ap.add_argument("--repair-attempts", type=int, default=2)
    ap.add_argument("--plan-only", action="store_true", help="계획만 생성하고 ComfyUI 실행은 건너뜀")
    ap.add_argument(
        "--from-plan",
        help="이미 생성된 plan.json 경로. 지정하면 claude -p planning 호출(및 repair)을 "
        "전부 건너뛰고 이 계획을 검증한 뒤 바로 ComfyUI 실행 단계로 진행한다.",
    )
    ap.add_argument("--flux-timeout-s", type=float, default=600)
    ap.add_argument("--qwen-timeout-s", type=float, default=1200)
    ap.add_argument("--minimax-timeout-s", type=float, default=1800)
    args = ap.parse_args(argv)

    scenario_path = Path(args.scenario_json)
    if not scenario_path.is_file():
        print(f"scenario.json을 찾을 수 없습니다: {scenario_path}", file=sys.stderr)
        return 1
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    product_name = scenario.get("product", {}).get("name", scenario_path.stem)

    repair_attempts_used = 0

    if args.from_plan:
        from_plan_path = Path(args.from_plan)
        if not from_plan_path.is_file():
            print(f"plan.json을 찾을 수 없습니다: {from_plan_path}", file=sys.stderr)
            return 1
        print(f"[1/4] 기존 계획 재사용 (claude -p planning 생략): {from_plan_path}")
        structured_output = json.loads(from_plan_path.read_text(encoding="utf-8"))
        plan, validation = _parse_and_validate(structured_output, scenario)
        cost_usd: float | str | None = "N/A (기존 plan.json 재사용, repair 불가)"
    else:
        task_prompt = build_task_prompt(scenario)

        print(f"[1/4] claude -p 로 생성 계획 수립 중 (model={args.model}, budget=${args.max_budget_usd})...")
        result = planner.run_plan_generation(
            project_dir=PROJECT_DIR,
            task_prompt=task_prompt,
            model=args.model,
            max_budget_usd=args.max_budget_usd,
        )
        if not result.ok:
            print(f"실패: {result.error_message}", file=sys.stderr)
            return 1

        plan, validation = _parse_and_validate(result.structured_output, scenario)

        while (
            (plan is None or not validation.ok)
            and repair_attempts_used < args.repair_attempts
            and result.session_id
        ):
            repair_attempts_used += 1
            print(f"[repair {repair_attempts_used}/{args.repair_attempts}] 검증 실패, 수정 요청 중...")
            result = planner.run_repair(
                project_dir=PROJECT_DIR,
                session_id=result.session_id,
                repair_prompt=build_repair_prompt(validation.errors),
                model=args.model,
                max_budget_usd=args.max_budget_usd,
            )
            if not result.ok:
                print(f"repair 호출 실패: {result.error_message}", file=sys.stderr)
                break
            plan, validation = _parse_and_validate(result.structured_output, scenario)

        structured_output = result.structured_output
        cost_usd = result.total_cost_usd

    out_dir = Path(args.output_dir) / f"{_slug(product_name)}_{datetime.datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "plan.json").write_text(
        json.dumps(structured_output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "plan_validation_report.md").write_text(
        _format_plan_report(validation, cost_usd, repair_attempts_used), encoding="utf-8"
    )

    print(f"\n[2/4] 계획 산출 위치: {out_dir}")
    if not validation.ok:
        print("생성 계획 검증 실패 — plan_validation_report.md 확인 필요", file=sys.stderr)
        return 2
    if plan is None:
        print("생성 계획이 비어 있습니다.", file=sys.stderr)
        return 2

    if args.plan_only:
        print("[3/4] --plan-only 지정됨 — ComfyUI 실행 생략")
        print("[4/4] DONE (plan-only)")
        return 0

    print("[3/4] ComfyUI 파이프라인 실행 중 (entity 이미지 -> 컷 프레임 -> 최종 영상)...")
    manifest = run_pipeline(
        plan,
        scenario,
        out_dir,
        flux_timeout_s=args.flux_timeout_s,
        qwen_timeout_s=args.qwen_timeout_s,
        minimax_timeout_s=args.minimax_timeout_s,
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "report.html").write_text(render_html(manifest, plan, scenario), encoding="utf-8")

    n_ok = sum(1 for s in manifest.entities.values() if s.status == "ok")
    n_ok += sum(1 for s in manifest.scene_cuts.values() if s.status == "ok")
    n_total = len(manifest.entities) + len(manifest.scene_cuts)
    final_status = manifest.final_video.status

    print(f"\n[4/4] 출력 위치: {out_dir}")
    print(f"이미지 단계: {n_ok}/{n_total} 성공, 최종 영상: {final_status}")
    if not manifest.comfy_available:
        print("경고: ComfyUI가 연결되지 않아 생성 단계가 건너뛰어졌습니다 (report.html 참고).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
