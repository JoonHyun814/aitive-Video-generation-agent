"""Renders the agent_loop step-by-step trace (model calls + tool calls, with
token/cache/cost per step) into a single readable HTML log. Copied from
scenario_agent_claude_litellm/trace_render.py -- the trace shape agent_loop.py
produces here is identical, just for a GenerationPlan run instead of a
Scenario run."""

from __future__ import annotations

import html as _html
import json as _json

_PHASE_LABEL = {"research": "리서치", "final": "최종 출력", "repair": "수정(repair)"}
_PHASE_COLOR = {"research": "#0091ff", "final": "#30a46c", "repair": "#f5a623"}


def _e(text) -> str:
    return _html.escape(str(text), quote=True)


def _fmt_int(n) -> str:
    return f"{n:,}" if isinstance(n, (int, float)) else "—"


def _usage_badges(usage: dict | None) -> str:
    if not usage:
        return ""
    parts = [f"입력 {_fmt_int(usage.get('prompt_tokens'))}", f"출력 {_fmt_int(usage.get('completion_tokens'))}"]
    cached = usage.get("cached_tokens")
    if cached:
        parts.append(f"캐시 히트 {_fmt_int(cached)}")
    cache_write = usage.get("cache_write_tokens")
    if cache_write:
        parts.append(f"캐시 생성 {_fmt_int(cache_write)}")
    return "".join(f'<span class="usage-pill">{_e(p)}</span>' for p in parts)


def _sub_calls_html(sub_calls: list | None) -> str:
    if not sub_calls:
        return ""
    items = "".join(
        f'<li><span class="sub-tool">{_e(s.get("tool"))}</span> {_usage_badges(s.get("usage"))}</li>'
        for s in sub_calls
    )
    return f'<div class="sub-calls"><div class="sub-title">내부 호출</div><ul>{items}</ul></div>'


def _step_html(step: dict) -> str:
    phase = step.get("phase", "?")
    color = _PHASE_COLOR.get(phase, "#8e8e93")
    phase_label = _PHASE_LABEL.get(phase, phase)
    seq = step.get("seq")
    iteration = step.get("iteration")
    iter_label = f" · 반복 {iteration}" if iteration else ""

    if step.get("type") == "model_call":
        cost = step.get("cost_usd")
        cost_label = f"${cost:.4f}" if isinstance(cost, (int, float)) else "—"
        return f"""\
<div class="step model-call">
  <div class="step-head">
    <span class="seq">#{seq}</span>
    <span class="phase-badge" style="background:{color}">{_e(phase_label)}{iter_label}</span>
    <span class="step-kind">모델 호출</span>
    <span class="cost">{cost_label}</span>
  </div>
  <div class="step-body">
    <div class="usage-row">{_usage_badges(step.get("usage"))}</div>
    <div class="summary">{_e(step.get("result_summary", ""))}</div>
  </div>
</div>"""

    args = step.get("arguments") or {}
    args_json = _e(_json.dumps(args, ensure_ascii=False))
    preview = _e(step.get("result_preview", ""))
    length = step.get("result_length")
    return f"""\
<div class="step tool-call">
  <div class="step-head">
    <span class="seq">#{seq}</span>
    <span class="phase-badge" style="background:{color}">{_e(phase_label)}{iter_label}</span>
    <span class="step-kind">도구: {_e(step.get("tool"))}</span>
    <span class="cost">{_fmt_int(length)}자</span>
  </div>
  <div class="step-body">
    <details>
      <summary>인자</summary>
      <pre class="args">{args_json}</pre>
    </details>
    <pre class="result-preview">{preview}</pre>
    {_sub_calls_html(step.get("sub_calls"))}
  </div>
</div>"""


def render_trace_html(trace: list[dict], meta: dict) -> str:
    steps_html = "".join(_step_html(s) for s in trace)

    n_model = sum(1 for s in trace if s.get("type") == "model_call")
    n_tool = sum(1 for s in trace if s.get("type") == "tool_call")
    total_prompt = sum((s.get("usage") or {}).get("prompt_tokens") or 0 for s in trace)
    total_completion = sum((s.get("usage") or {}).get("completion_tokens") or 0 for s in trace)
    total_cached = sum((s.get("usage") or {}).get("cached_tokens") or 0 for s in trace)

    return f"""\
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(meta.get("product_name", "실행"))} — 계획 수립 실행 로그</title>
<style>
  :root {{ --bg:#f7f7f8; --card:#fff; --text:#1a1a1e; --muted:#6b6b72; --border:#e6e6e9; --accent:#3454d1; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
    font-family:-apple-system,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif; line-height:1.5; }}
  .wrap {{ max-width: 900px; margin:0 auto; padding: 28px 20px 80px; }}
  header {{ margin-bottom: 22px; }}
  header h1 {{ font-size: 22px; margin: 0 0 10px; }}
  .summary-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:10px; }}
  .summary-card {{ background:var(--card); border:1px solid var(--border); border-radius:10px; padding:10px 14px; }}
  .summary-card .k {{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }}
  .summary-card .v {{ font-size:17px; font-weight:700; }}
  .step {{ background:var(--card); border:1px solid var(--border); border-radius:10px;
    padding:12px 14px; margin-bottom:10px; }}
  .step-head {{ display:flex; align-items:center; gap:8px; font-size:12.5px; margin-bottom:6px; flex-wrap:wrap; }}
  .seq {{ font-weight:800; color:var(--muted); }}
  .phase-badge {{ color:#fff; border-radius:999px; padding:2px 9px; font-size:11px; font-weight:700; }}
  .step-kind {{ font-weight:600; }}
  .cost {{ margin-left:auto; color:var(--muted); font-variant-numeric: tabular-nums; }}
  .usage-row {{ margin-bottom:4px; }}
  .usage-pill {{ display:inline-block; background:var(--bg); border:1px solid var(--border);
    border-radius:999px; padding:1px 8px; font-size:11px; color:var(--muted); margin-right:4px; }}
  .summary {{ font-size:13.5px; }}
  pre.args, pre.result-preview {{ background:var(--bg); border:1px solid var(--border); border-radius:8px;
    padding:8px 10px; font-size:12px; white-space:pre-wrap; word-break:break-all; margin:6px 0 0; }}
  details summary {{ cursor:pointer; font-size:12px; color:var(--accent); }}
  .sub-calls {{ margin-top:6px; padding-top:6px; border-top:1px dashed var(--border); }}
  .sub-title {{ font-size:11px; color:var(--muted); margin-bottom:2px; }}
  .sub-calls ul {{ margin:0; padding-left:16px; font-size:12px; }}
  .sub-tool {{ font-weight:600; }}
  footer {{ text-align:center; color:var(--muted); font-size:12px; margin-top:30px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{_e(meta.get("product_name", ""))} — 계획 수립 실행 로그</h1>
    <div class="summary-grid">
      <div class="summary-card"><div class="k">모델 호출</div><div class="v">{n_model}</div></div>
      <div class="summary-card"><div class="k">도구 호출</div><div class="v">{n_tool}</div></div>
      <div class="summary-card"><div class="k">입력 토큰</div><div class="v">{_fmt_int(total_prompt)}</div></div>
      <div class="summary-card"><div class="k">출력 토큰</div><div class="v">{_fmt_int(total_completion)}</div></div>
      <div class="summary-card"><div class="k">캐시 히트</div><div class="v">{_fmt_int(total_cached)}</div></div>
      <div class="summary-card"><div class="k">추정 비용</div><div class="v">{f"${meta.get('total_cost_usd'):.4f}" if meta.get("total_cost_usd") is not None else "—"}</div></div>
    </div>
  </header>
  {steps_html}
  <footer>video_gen_claude_p_v2 계획 수립 실행 로그 — agent_loop.py의 trace 기록을 렌더링</footer>
</div>
</body>
</html>
"""
