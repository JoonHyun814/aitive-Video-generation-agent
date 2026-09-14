"""Manual tool-calling agent loop (LiteLLM's counterpart to `claude -p`'s built-in harness).

Unlike the Claude Code CLI build, LiteLLM is just a model-calling layer, so
this module owns the whole `while has_tool_calls: execute, append, re-call`
loop itself, then makes one final call with `response_format: json_schema`
to get the structured Scenario out -- mirroring `--json-schema`'s behavior.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field

import litellm

from . import config, tools
from .prompts import FINAL_JSON_INSTRUCTION, build_repair_prompt


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
                              completion_tokens, tool_calls_made, f"모델 호출 실패: {e}", messages)

        usage = resp.usage
        prompt_tokens += usage.prompt_tokens
        completion_tokens += usage.completion_tokens
        est = config.estimate_cost_usd(model, usage.prompt_tokens, usage.completion_tokens)
        if est is None:
            cost_known = False
        else:
            cost_usd += est
        if max_budget_usd is not None and cost_known and cost_usd > max_budget_usd:
            return RunResult(False, None, cost_usd, prompt_tokens, completion_tokens,
                              tool_calls_made, f"예산 초과(${cost_usd:.2f} > ${max_budget_usd})", messages)

        msg = resp.choices[0].message
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
            else:
                result_text = tools.dispatch(tc.function.name, args)
            print(f"  [tool] {tc.function.name} -> {len(result_text)}자", file=sys.stderr, flush=True)
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result_text}
            )
    else:
        return RunResult(False, None, cost_usd if cost_known else None, prompt_tokens,
                          completion_tokens, tool_calls_made,
                          f"도구 호출 반복 한도({max_tool_iters}) 초과", messages)

    print("  [final] 구조화 출력 호출 중...", file=sys.stderr, flush=True)
    structured, err, final_usage_cost = _final_structured_call(model, messages, schema)
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
        messages=messages,
    )


def _final_structured_call(model: str, messages: list, schema: dict):
    final_messages = messages + [{"role": "user", "content": FINAL_JSON_INSTRUCTION}]
    try:
        resp = _call(model, final_messages, None, _final_json_schema(schema), max_tokens=16000, timeout=300)
    except Exception as e:
        return None, f"최종 구조화 출력 호출 실패: {e}", None

    usage = resp.usage
    cost = config.estimate_cost_usd(model, usage.prompt_tokens, usage.completion_tokens)
    content = resp.choices[0].message.content
    messages.append({"role": "user", "content": FINAL_JSON_INSTRUCTION})
    messages.append({"role": "assistant", "content": content})
    try:
        return json.loads(content), None, (usage.prompt_tokens, usage.completion_tokens, cost)
    except (json.JSONDecodeError, TypeError) as e:
        return None, f"최종 출력이 JSON이 아닙니다: {e}\n{str(content)[:1000]}", (
            usage.prompt_tokens, usage.completion_tokens, cost
        )


def run_repair(
    *,
    messages: list,
    errors: list[str],
    schema: dict,
    model: str | None = None,
    max_budget_usd: float | None = 2.0,
) -> RunResult:
    model = model or config.default_model()
    messages = messages + [{"role": "user", "content": build_repair_prompt(errors)}]
    structured, err, usage_cost = _final_structured_call(model, messages, schema)

    prompt_tokens = completion_tokens = 0
    cost_usd = None
    if usage_cost:
        prompt_tokens, completion_tokens, cost = usage_cost
        cost_usd = cost
        if max_budget_usd is not None and cost is not None and cost > max_budget_usd:
            return RunResult(False, None, cost, prompt_tokens, completion_tokens, 0,
                              f"repair 예산 초과(${cost:.2f} > ${max_budget_usd})", messages)

    return RunResult(
        ok=err is None,
        structured_output=structured,
        total_cost_usd=cost_usd,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tool_calls_made=0,
        error_message=err,
        messages=messages,
    )
