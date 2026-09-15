"""Manual tool-calling agent loop (LiteLLM's counterpart to `claude -p`'s built-in harness).

Unlike the Claude Code CLI build, LiteLLM is just a model-calling layer, so
this module owns the whole `while has_tool_calls: execute, append, re-call`
loop itself, then makes one final call with `response_format: json_schema`
to get the structured Scenario out -- mirroring `--json-schema`'s behavior.

Every model/tool call is also appended to a flat `trace` list (usage, cache
fields, tool name/arguments, result previews) so the full run can be written
to disk and rendered as a step-by-step HTML log -- see render_trace_html in
render.py.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field

import litellm

from . import config, tools
from .prompts import FINAL_JSON_INSTRUCTION, build_repair_prompt

_RESULT_PREVIEW_CHARS = 500


@dataclass
class RunResult:
    ok: bool
    structured_output: dict | None
    total_cost_usd: float | None
    prompt_tokens: int
    completion_tokens: int
    tool_calls_made: int
    error_message: str | None
    messages: list = field(default_factory=list)
    trace: list = field(default_factory=list)


def _call(
    model: str,
    messages: list,
    tools_spec: list | None,
    response_format: dict | None,
    max_tokens: int,
    timeout: int = 180,
):
    # Context grows across the loop (tool results accumulate), so a large
    # max_tokens + large accumulated context can legitimately take a few
    # minutes -- a too-short timeout just causes litellm to retry the same
    # slow request and never actually finish. num_retries=1 keeps one retry
    # for transient failures without compounding a real slow-request problem.
    kwargs = dict(
        model=config.litellm_model_id(model),
        api_base=config.base_url(),
        api_key=config.api_key(),
        messages=messages,
        max_tokens=max_tokens,
        timeout=timeout,
        num_retries=1,
    )
    if tools_spec:
        kwargs["tools"] = tools_spec
        kwargs["tool_choice"] = "auto"
    if response_format:
        kwargs["response_format"] = response_format
    return litellm.completion(**kwargs)


def _final_json_schema(schema: dict) -> dict:
    return {"type": "json_schema", "json_schema": {"name": "scenario", "schema": schema, "strict": True}}


def _trace_model_call(trace: list, *, phase: str, iteration: int | None, usage, cost_usd, summary: str):
    trace.append(
        {
            "seq": len(trace) + 1,
            "phase": phase,
            "type": "model_call",
            "iteration": iteration,
            "usage": config.usage_dict(usage),
            "cost_usd": cost_usd,
            "result_summary": summary,
        }
    )


def _trace_tool_call(trace: list, *, phase: str, iteration: int | None, name: str, arguments: dict, result: str):
    trace.append(
        {
            "seq": len(trace) + 1,
            "phase": phase,
            "type": "tool_call",
            "iteration": iteration,
            "tool": name,
            "arguments": arguments,
            "result_preview": result[:_RESULT_PREVIEW_CHARS],
            "result_length": len(result),
            "sub_calls": tools.pop_sub_calls(),
        }
    )


def run_scenario_generation(
    *,
    system_prompt: str,
    task_prompt: str,
    schema: dict,
    model: str | None = None,
    max_tool_iters: int = 25,
    max_budget_usd: float | None = 4.0,
) -> RunResult:
    model = model or config.default_model()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_prompt},
    ]
    trace: list = []

    prompt_tokens = 0
    completion_tokens = 0
    cost_usd = 0.0
    cost_known = True
    tool_calls_made = 0

    for iteration in range(max_tool_iters):
        print(f"  [loop {iteration + 1}/{max_tool_iters}] 모델 호출 중...", file=sys.stderr, flush=True)
        try:
            resp = _call(model, messages, tools.TOOL_SPECS, None, max_tokens=8192)
        except Exception as e:
            return RunResult(False, None, cost_usd if cost_known else None, prompt_tokens,
                              completion_tokens, tool_calls_made, f"모델 호출 실패: {e}", messages, trace)

        usage = resp.usage
        prompt_tokens += usage.prompt_tokens
        completion_tokens += usage.completion_tokens
        est = config.estimate_cost_usd(model, usage.prompt_tokens, usage.completion_tokens)
        if est is None:
            cost_known = False
        else:
            cost_usd += est

        msg = resp.choices[0].message
        n_calls = len(msg.tool_calls) if msg.tool_calls else 0
        _trace_model_call(
            trace, phase="research", iteration=iteration + 1, usage=usage, cost_usd=est,
            summary=f"도구 호출 {n_calls}건 요청" if n_calls else (msg.content or "")[:200],
        )
        if max_budget_usd is not None and cost_known and cost_usd > max_budget_usd:
            return RunResult(False, None, cost_usd, prompt_tokens, completion_tokens,
                              tool_calls_made, f"예산 초과(${cost_usd:.2f} > ${max_budget_usd})", messages, trace)

        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            break

        for tc in msg.tool_calls:
            tool_calls_made += 1
            print(f"  [tool] {tc.function.name}({tc.function.arguments})", file=sys.stderr, flush=True)
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                result_text = f"도구 인자 JSON 파싱 실패: {tc.function.arguments!r}"
                args = {}
            else:
                result_text = tools.dispatch(tc.function.name, args)
            print(f"  [tool] {tc.function.name} -> {len(result_text)}자", file=sys.stderr, flush=True)
            _trace_tool_call(
                trace, phase="research", iteration=iteration + 1,
                name=tc.function.name, arguments=args, result=result_text,
            )
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result_text}
            )
    else:
        return RunResult(False, None, cost_usd if cost_known else None, prompt_tokens,
                          completion_tokens, tool_calls_made,
                          f"도구 호출 반복 한도({max_tool_iters}) 초과", messages, trace)

    print("  [final] 구조화 출력 호출 중...", file=sys.stderr, flush=True)
    structured, err, final_usage_cost, raw_content = _final_structured_call(
        model, messages, schema, [{"role": "user", "content": FINAL_JSON_INSTRUCTION}],
        trace=trace, phase="final", iteration=None,
    )
    if final_usage_cost:
        p, c, cost_add = final_usage_cost
        prompt_tokens += p
        completion_tokens += c
        if cost_add is None:
            cost_known = False
        else:
            cost_usd += cost_add

    return RunResult(
        ok=err is None,
        structured_output=structured,
        total_cost_usd=cost_usd if cost_known else None,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tool_calls_made=tool_calls_made,
        error_message=err,
        # `messages` is the clean research-only history (no final/repair JSON
        # attempts folded in) -- see run_repair for why that matters.
        messages=messages,
        trace=trace,
    )


def _final_structured_call(
    model: str, base_messages: list, schema: dict, extra: list, *, trace: list, phase: str, iteration: int | None
):
    """Never mutates `base_messages`. Returns (structured, err, usage_cost, raw_content)."""
    final_messages = base_messages + extra
    try:
        resp = _call(model, final_messages, None, _final_json_schema(schema), max_tokens=16000, timeout=300)
    except Exception as e:
        trace.append({
            "seq": len(trace) + 1, "phase": phase, "type": "model_call", "iteration": iteration,
            "usage": None, "cost_usd": None, "result_summary": f"호출 실패: {e}",
        })
        return None, f"최종 구조화 출력 호출 실패: {e}", None, None

    usage = resp.usage
    cost = config.estimate_cost_usd(model, usage.prompt_tokens, usage.completion_tokens)
    content = resp.choices[0].message.content
    usage_cost = (usage.prompt_tokens, usage.completion_tokens, cost)
    is_valid = True
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        is_valid = False
        parsed = None
    _trace_model_call(
        trace, phase=phase, iteration=iteration, usage=usage, cost_usd=cost,
        summary="구조화 출력 생성 성공" if is_valid else "구조화 출력이 JSON 파싱 실패",
    )
    if is_valid:
        return parsed, None, usage_cost, content
    return None, f"최종 출력이 JSON이 아닙니다: {str(content)[:1000]}", usage_cost, content


def run_repair(
    *,
    messages: list,
    errors: list[str],
    schema: dict,
    previous_output: dict | str | None = None,
    model: str | None = None,
    max_budget_usd: float | None = 2.0,
    attempt: int = 1,
) -> RunResult:
    # Rebuilt from the clean research-only `messages` every time (not from a
    # previous repair round's messages) so repeated repairs don't stack prior
    # failed JSON attempts into context -- that compounding was what made a
    # 2nd repair round's request balloon past any reasonable timeout.
    model = model or config.default_model()
    trace: list = []
    repair_prompt = build_repair_prompt(errors, previous_output)
    structured, err, usage_cost, _content = _final_structured_call(
        model, messages, schema, [{"role": "user", "content": repair_prompt}],
        trace=trace, phase="repair", iteration=attempt,
    )

    prompt_tokens = completion_tokens = 0
    cost_usd = None
    if usage_cost:
        prompt_tokens, completion_tokens, cost = usage_cost
        cost_usd = cost
        if max_budget_usd is not None and cost is not None and cost > max_budget_usd:
            return RunResult(False, None, cost, prompt_tokens, completion_tokens, 0,
                              f"repair 예산 초과(${cost:.2f} > ${max_budget_usd})", messages, trace)

    return RunResult(
        ok=err is None,
        structured_output=structured,
        total_cost_usd=cost_usd,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tool_calls_made=0,
        error_message=err,
        # Keep returning the same clean base -- a second repair round should
        # still start from research context only, not this round's attempt.
        messages=messages,
        trace=trace,
    )
