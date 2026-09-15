"""Drives `claude -p` for the single-shot planning call.

No tool use is needed here (the whole scenario is pasted into the prompt), so
this is a plain --json-schema call -- no --mcp-config/--allowedTools, unlike
scenario_agent_claude/runner.py which needs WebFetch/WebSearch/RAG tools for
its research phase. A failed validation triggers at most one `--resume`
repair call in the same session, same pattern as the scenario agent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass

from .prompts import SYSTEM_PROMPT
from .schema import lean_json_schema


@dataclass
class PlanRunResult:
    ok: bool
    session_id: str | None
    structured_output: dict | None
    total_cost_usd: float | None
    error_message: str | None
    raw: dict | None


def _find_claude() -> str:
    path = shutil.which("claude")
    if not path:
        raise RuntimeError(
            "claude CLI를 PATH에서 찾을 수 없습니다. Claude Code CLI가 설치되어 있는지 확인하세요."
        )
    return path


def _base_args(model: str, max_budget_usd: float) -> list[str]:
    return [
        "--system-prompt", SYSTEM_PROMPT,
        "--output-format", "json",
        "--json-schema", json.dumps(lean_json_schema()),
        "--permission-mode", "bypassPermissions",
        "--max-budget-usd", str(max_budget_usd),
        "--model", model,
    ]


def _run(cmd: list[str], cwd: str, timeout_s: int) -> PlanRunResult:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s, encoding="utf-8"
        )
    except subprocess.TimeoutExpired:
        return PlanRunResult(False, None, None, None, f"claude -p 타임아웃({timeout_s}초)", None)

    if proc.returncode != 0:
        return PlanRunResult(
            False, None, None, None,
            f"claude -p 종료 코드 {proc.returncode}\nstderr: {proc.stderr[-4000:]}",
            None,
        )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return PlanRunResult(
            False, None, None, None, f"claude -p 출력이 JSON이 아닙니다: {e}\n{proc.stdout[:2000]}", None
        )

    is_error = bool(envelope.get("is_error"))
    structured = envelope.get("structured_output")
    err = None if not is_error else envelope.get("result", "알 수 없는 오류")
    if not is_error and structured is None:
        err = "claude -p가 structured_output을 반환하지 않았습니다."

    return PlanRunResult(
        ok=not is_error and structured is not None,
        session_id=envelope.get("session_id"),
        structured_output=structured,
        total_cost_usd=envelope.get("total_cost_usd"),
        error_message=err,
        raw=envelope,
    )


def run_plan_generation(
    *,
    project_dir: str,
    task_prompt: str,
    model: str = "opus",
    max_budget_usd: float = 1.5,
    timeout_s: int = 300,
) -> PlanRunResult:
    claude = _find_claude()
    cmd = [claude, "-p", task_prompt] + _base_args(model, max_budget_usd)
    return _run(cmd, cwd=project_dir, timeout_s=timeout_s)


def run_repair(
    *,
    project_dir: str,
    session_id: str,
    repair_prompt: str,
    model: str = "opus",
    max_budget_usd: float = 1.0,
    timeout_s: int = 240,
) -> PlanRunResult:
    claude = _find_claude()
    cmd = [claude, "-p", repair_prompt, "--resume", session_id] + _base_args(model, max_budget_usd)
    return _run(cmd, cwd=project_dir, timeout_s=timeout_s)
