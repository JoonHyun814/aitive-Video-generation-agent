"""Drives `claude -p` as the agent harness.

One session does the research (WebFetch/WebSearch/RAG MCP tools) and emits
the final scenario via `--json-schema` in a single call; a failed validation
triggers at most one `--resume` repair call in the same session (so it keeps
the retrieved evidence and the prompt cache instead of starting over).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass

from .schema import lean_json_schema

RAG_SERVER_NAME = "chromadb-explorer-network"
RAG_SERVER_URL = "http://10.110.56.157:8765/mcp"

ALLOWED_TOOLS = [
    "WebSearch",
    "WebFetch",
    f"mcp__{RAG_SERVER_NAME}__search_chromadb",
    f"mcp__{RAG_SERVER_NAME}__search_chromadb_hybrid",
    f"mcp__{RAG_SERVER_NAME}__fetch_by_video_id",
    f"mcp__{RAG_SERVER_NAME}__search_visual",
    f"mcp__{RAG_SERVER_NAME}__search_graph_pattern",
]


def mcp_config_json(rag_server_url: str = RAG_SERVER_URL) -> str:
    return json.dumps({"mcpServers": {RAG_SERVER_NAME: {"type": "http", "url": rag_server_url}}})


@dataclass
class RunResult:
    ok: bool
    session_id: str | None
    structured_output: dict | None
    total_cost_usd: float | None
    permission_denials: list
    error_message: str | None
    raw: dict | None


def _find_claude() -> str:
    path = shutil.which("claude")
    if not path:
        raise RuntimeError(
            "claude CLI를 PATH에서 찾을 수 없습니다. Claude Code CLI가 설치되어 있는지 확인하세요."
        )
    return path


def _base_args(
    system_prompt: str,
    rag_server_url: str,
    model: str,
    max_budget_usd: float,
) -> list[str]:
    return [
        "--system-prompt", system_prompt,
        "--mcp-config", mcp_config_json(rag_server_url),
        "--strict-mcp-config",
        "--allowedTools", ",".join(ALLOWED_TOOLS),
        "--output-format", "json",
        "--json-schema", json.dumps(lean_json_schema()),
        "--permission-mode", "bypassPermissions",
        "--max-budget-usd", str(max_budget_usd),
        "--model", model,
    ]


def _run(cmd: list[str], cwd: str, timeout_s: int) -> RunResult:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s, encoding="utf-8"
        )
    except subprocess.TimeoutExpired:
        return RunResult(False, None, None, None, [], f"claude -p 타임아웃({timeout_s}초)", None)

    if proc.returncode != 0:
        return RunResult(
            False, None, None, None, [],
            f"claude -p 종료 코드 {proc.returncode}\nstderr: {proc.stderr[-4000:]}",
            None,
        )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return RunResult(
            False, None, None, None, [], f"claude -p 출력이 JSON이 아닙니다: {e}\n{proc.stdout[:2000]}", None
        )

    is_error = bool(envelope.get("is_error"))
    structured = envelope.get("structured_output")
    err = None if not is_error else envelope.get("result", "알 수 없는 오류")
    if not is_error and structured is None:
        err = "claude -p가 structured_output을 반환하지 않았습니다."

    return RunResult(
        ok=not is_error and structured is not None,
        session_id=envelope.get("session_id"),
        structured_output=structured,
        total_cost_usd=envelope.get("total_cost_usd"),
        permission_denials=envelope.get("permission_denials", []),
        error_message=err,
        raw=envelope,
    )


def run_scenario_generation(
    *,
    project_dir: str,
    system_prompt: str,
    task_prompt: str,
    model: str = "opus",
    max_budget_usd: float = 4.0,
    rag_server_url: str = RAG_SERVER_URL,
    timeout_s: int = 900,
) -> RunResult:
    claude = _find_claude()
    cmd = [claude, "-p", task_prompt] + _base_args(
        system_prompt, rag_server_url, model, max_budget_usd
    )
    return _run(cmd, cwd=project_dir, timeout_s=timeout_s)


def run_repair(
    *,
    project_dir: str,
    session_id: str,
    repair_prompt: str,
    system_prompt: str,
    model: str = "opus",
    max_budget_usd: float = 2.0,
    rag_server_url: str = RAG_SERVER_URL,
    timeout_s: int = 600,
) -> RunResult:
    claude = _find_claude()
    cmd = [claude, "-p", repair_prompt, "--resume", session_id] + _base_args(
        system_prompt, rag_server_url, model, max_budget_usd
    )
    return _run(cmd, cwd=project_dir, timeout_s=timeout_s)
