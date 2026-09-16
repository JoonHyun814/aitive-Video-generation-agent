# video_gen_claude_p 프롬프트 및 파이프라인 절차 정리

대상 코드: `agent/video_gen_claude_p/prompts.py`, `planner.py`, `cli.py`, `executor.py`,
`schema.py`, `validators.py`

이 패키지는 `scenario_agent_claude(_litellm)`가 만든 `scenario.json`(장면별 인물/장소/
소품/대사/자막이 이미 확정된 광고 시나리오)을 입력받아, 실제 이미지/영상을 생성하는
후속 파이프라인이다. 앞 단계 에이전트와 달리 도구 호출(tool use)이 전혀 없는 **단발
구조화 출력 1회 + 결정론적 Python 실행기**로 구성된다.

---

## 1. 전체 절차 개요

```
scenario.json
   │
   ▼
[1단계] claude -p 1회 호출 (--json-schema, 도구 없음)
        → GenerationPlan(구조화 출력) 생성
        → pydantic 스키마 검증 + 시나리오 정합성 검증(validators.py)
        → 검증 실패 시 같은 세션에서 --resume 보수 호출 최대 N회(기본 2회)
   │  (--from-plan 지정 시 이 단계 전체를 건너뛰고 기존 plan.json을 그대로 검증만 수행)
   ▼
plan.json + plan_validation_report.md 저장 (여기서 --plan-only면 종료)
   │
   ▼
[2단계] 결정론적 실행기(executor.py)가 ComfyUI Workflow API를 순서대로 호출
   1) 등장 요소(character/location/prop)별 레퍼런스 이미지 — Flux, text-to-image
      (단, is_product=true인 prop 하나는 생성하지 않고 제품 상세페이지에서
      실제 사진을 다운로드해서 그대로 사용)
   2) 모든 장면(scene)의 컷 프레임 — Qwen-Edit, 최대 3장 참조 이미지 기반 편집
   3) 최종 영상 1편 — MiniMax H3, 참조 이미지 + 최대 3개 컷 프레임 기반 영상 생성
   │
   ▼
manifest.json(각 단계 성공/실패 기록) + report.html 저장
```

- 1단계는 LLM 호출(비결정적), 2단계는 순수 Python + HTTP 호출(결정론적) — 이 분리가
  패키지 설계의 핵심 원칙이다(`executor.py` 모듈 docstring).
- 2단계의 각 스텝은 개별적으로 실패해도 파이프라인 전체를 중단하지 않고, 의존하지
  않는 뒷단 스텝은 계속 시도한다(예: 특정 entity 이미지 생성 실패해도 그 이미지를
  참조하지 않는 다른 scene_cut은 계속 진행). 모든 성공/실패는 `manifest.json`에
  기록되어 `report.html`로 사람이 검토할 수 있게 한다.

---

## 2. 1단계 — 생성 계획(Plan) 수립 프롬프트

### 2-1. `SYSTEM_PROMPT` (`prompts.py:11-86`)
"비주얼 디렉터" 역할을 부여하고, 3단계 생성 파이프라인의 각 단계별 제약을 규칙으로
못박는다. 도구 호출 없이 시나리오 전체를 한 번에 읽고 계획만 세우므로, 규칙은
"무엇을 만들지"가 아니라 "각 모델의 하드 제약을 어떻게 스키마에 반영할지"에 집중된다.

**1단계 규칙 — Flux 레퍼런스 이미지 (`entity_images`)**
- characters/locations/props 각 항목당 정확히 1장, `generation_prompt` 하나만 사용
  (Flux는 참조 이미지 입력을 받지 않음).
- **Flux 프롬프트 작성 공식** (`docs/api/FLUX_PROMPT.md` 기준, 시스템 프롬프트에 그대로
  인용됨): 영어 문장형 서술, `[핵심 피사체] + [행동/포즈] + [배경 맥락] + [스타일·재질·
  조명] + [카메라 설정] + [텍스트 묘사(필요시)]` 순서로 배치(순서 = 가중치). 네거티브
  프롬프트가 사실상 무효이므로 "없앨 것" 대신 "있어야 할 것"만 긍정 표현으로 쓴다.
  이미지 내 텍스트/로고는 따옴표+폰트/색상 지정, 심도 제어는 f값+조명 키워드
  (`f/1.8`, `soft diffused studio lighting` 등). width/height는 64의 배수
  (권장: `1024x1024`, `1360x768`, `768x1360`, `1024x576`).
- 모든 프롬프트 끝에 공통 스타일 지시문을 붙여 "같은 광고 한 편"처럼 톤을 통일.
- 실제 광고 제품 자체를 나타내는 prop이 있으면 `is_product=true`로 표시(0개 또는
  1개만 허용) — 이 경우 이미지를 생성하지 않고 제품 상세페이지의 실사진을 그대로
  쓰므로 `generation_prompt`에는 "실제 제품 사진 사용, 생성 생략" 같은 안내문만 넣는다.

**2단계 규칙 — Qwen-Edit 컷 프레임 (`scene_cuts`)**
- 최종 영상 사용 여부와 무관하게 **모든 장면**에 대해 하나씩 컷 프레임을 만든다
  (전체 스토리보드 구성).
- 각 장면마다 `ref_entity_ids`는 최대 3장(Qwen-Edit 하드 제한). 그 장면에 실제
  등장하는 character/location/prop 중에서만 우선순위 (1) 제품 prop → (2) 장소 →
  (3) 인물 → (4) 기타 소품 순으로 최대 3개까지 선택.
- **Qwen-Edit 프롬프트 작성 규칙** (`docs/api/QWEN_PROMPT.md` 기준): `edit_prompt`는
  태그 나열이 아니라 자연어 지시문(무엇을 어떻게 바꿔서/합성해서 장면을 만들지)으로
  작성하고 scene의 action/camera_notes/visual_effects를 반영. 여러 컷에서 같은
  인물/제품을 다른 각도로 보여줘야 할 때는 정확히 정해진 카메라 앵글 키워드만 사용
  (방위각 8방향, 고도 4단계, 거리 3단계 — 변형 표현 금지, 각도 불안정 위험).
  `negative_prompt`는 기본 `"blurry, distorted, extra limbs, watermark, low quality"`,
  포토리얼 톤이면 `"plastic, smooth skin, airbrushed, 3d render, CGI, cartoon,
  illustration, perfect symmetry, overexposed"` 추가.

**3단계 규칙 — MiniMax H3 최종 영상 (`final_video`)**
- 전체 광고를 MiniMax 1회 호출로 생성. `cut_scene_nos`는 최대 3개(하드 제한) —
  서사적으로 가장 중요한 장면(훅/핵심 기능·데모/CTA)을 시간축에 고르게 분산 선택.
- `reference_entity_id`는 반드시 `is_product=true` entity(없으면 가장 대표적인 prop)로
  지정해 영상 전체가 실제 제품 사진에 앵커링되게 한다.
- `cut_times_s`는 선택한 scene들의 `start_s`와 정확히 같은 값을 시간 순으로.
- `total_duration_s`는 시나리오 `duration_s`와 같되 15.0초를 넘기지 않는다.
- `prompt`는 광고 전체 톤/흐름/`creative_strategy.rationale`을 반영한 영어 설명.

**공통 제약**: 모든 entity_id는 scenario.json에 실존하는 id와 정확히 일치해야 하며
지어낸 id 금지 — 시나리오에 이미 적힌 appearance/description/action 텍스트를 최대한
그대로 반영해 프롬프트를 쓰도록 지시한다.

### 2-2. `build_task_prompt(scenario)` (`prompts.py:89-94`)
scenario.json 전체를 JSON 문자열로 그대로 감싸 "이를 바탕으로 GenerationPlan을
스키마에 맞게 출력하라"고만 지시하는 단순 래퍼. 도구 호출이 없으므로 리서치 절차
안내가 필요 없고, 시스템 프롬프트의 규칙 + 이 시나리오 원문만으로 1턴에 답을 낸다.

### 2-3. `build_repair_prompt(errors)` (`prompts.py:97-104`)
검증 실패 시 오류 목록을 그대로 나열하고 "일부만 수정한 조각이 아니라 완전한 JSON
전체를 다시 출력할 것"을 강제 — `scenario_agent_claude`의 repair 프롬프트와 동일한
패턴(부분 patch가 아닌 전체 재출력 강제로 스키마 일관성 보장).

---

## 3. `planner.py` — `claude -p` 호출 방식

- `scenario_agent_claude/runner.py`와 달리 **도구 호출이 전혀 없다** (시나리오 전체를
  프롬프트에 붙여넣고 한 번에 추론하므로 `--mcp-config`/`--allowedTools` 불필요).
  대신 `--json-schema`로 `GenerationPlan`의 lean JSON Schema(`schema.py:_STRIP_KEYS`로
  `minItems`/`pattern` 등 구조에 불필요한 키워드 제거)를 강제한다.
- `run_plan_generation`: `claude -p <task_prompt> --system-prompt <SYSTEM_PROMPT>
  --output-format json --json-schema <...> --permission-mode bypassPermissions
  --max-budget-usd <budget> --model <model>` 실행, stdout의 JSON envelope에서
  `structured_output`/`session_id`/`total_cost_usd` 추출.
- `run_repair`: 실패한 세션에 `--resume <session_id>`로 붙어 `build_repair_prompt`
  결과를 재전달 — 시나리오 에이전트와 동일하게 "검증 실패 → 같은 세션에서 최대 1회
  더 보수 호출" 패턴을 재사용.
- CLI 쪽(`cli.py`)에서 이 repair 루프를 최대 `--repair-attempts`(기본 2)회까지 반복.

---

## 4. `cli.py` — 진입점 및 `--from-plan` 우회 경로

```
python -m video_gen_claude_p.cli --scenario-json <경로> [--plan-only]
python -m video_gen_claude_p.cli --scenario-json <경로> --from-plan <plan.json 경로>
```

- 기본 경로: 위 2절의 `claude -p` 계획 생성 + repair 루프를 거쳐 `structured_output`을
  얻는다.
- `--from-plan <path>` 지정 시: `claude -p` planning/repair 호출을 **전부 건너뛰고**
  기존 `plan.json` 파일을 읽어 곧바로 `_parse_and_validate`(pydantic 검증 +
  `validators.validate_plan`)만 수행한다. 이미 `--plan-only`로 뽑아 사람이 검토/수정한
  계획을 그대로 이어서 실행하고 싶을 때 사용 — 이 경로에서는 repair가 불가능하므로
  (`cost_usd`도 `"N/A (기존 plan.json 재사용, repair 불가)"`로 기록) 검증 실패 시
  바로 오류를 보고하고 종료한다.
- 두 경로 모두 결과를 `out_dir/plan.json` + `plan_validation_report.md`로 저장한 뒤
  검증이 `ok`이고 `--plan-only`가 아니면 `executor.run_pipeline`을 호출해 ComfyUI
  실행 단계로 진행한다.

---

## 5. 2단계 — 결정론적 실행기(`executor.py`)와 검증(`validators.py`)

- 실행 순서: `health_check` → (product 실사진 다운로드 or Flux 생성) → 나머지 entity
  Flux 생성 → 전체 scene_cuts Qwen-Edit 생성(생성된 entity 이미지 경로를 참조) →
  final_video MiniMax 생성(entity 이미지 + scene_cut 이미지를 참조).
- `is_product=true` entity는 `fetch.product_image_urls(scenario)`로 얻은 URL에서
  실사진을 다운로드해 그대로 `entities[...]`에 `ok` 상태로 기록하고, Flux 생성은
  건너뛴다(다운로드 실패 시 `error` 기록, 하지만 파이프라인은 계속 진행).
  이후 `_generate_entity_image`는 이 entity_id를 건너뛴다.
- ComfyUI 헬스체크 실패(`comfy_available=False`) 시 각 생성 스텝은 에러가 아니라
  `"skipped"` 상태로 기록되어, "생성 실패"와 "애초에 서버가 없어서 시도 자체를
  안 함"을 리포트에서 구분할 수 있게 한다.
- 각 스텝(Flux/Qwen-Edit/MiniMax)은 timeout 별도 설정 가능(`--flux-timeout-s` 기본
  600, `--qwen-timeout-s` 기본 1200, `--minimax-timeout-s` 기본 1800 — RTX PRO 6000
  Blackwell 실측 기준 `docs/api/Comfyui_API_SPEC.md` 권장값 인용).
- `validators.validate_plan`은 LLM이 만든 `GenerationPlan`이 실제 `scenario.json`과
  정합적인지 구조적으로 검사한다(하드 오류 → repair 트리거, 경고 → 통과하되 리포트에
  기록):
  - `entity_images`: 시나리오의 모든 character/location/prop id가 정확히 1번씩,
    올바른 `entity_type`으로 존재하는지. `is_product=true`가 0~1개인지, 있다면
    `entity_type=prop`인지.
  - `scene_cuts`: 시나리오의 모든 `scene_no`가 정확히 1번씩 존재하는지,
    `ref_entity_ids` 개수가 1~`MAX_QWEN_REF_IMAGES`(3)인지, 참조 id가 실존하는지,
    그 장면에 실제 등장하지 않는 entity를 참조하면 경고.
  - `final_video`: `cut_scene_nos` 개수가 1~`MAX_MINIMAX_CUTS`(3)인지, 중복/미존재
    scene_no 여부, `cut_times_s`가 각 scene의 `start_s`와 정확히 일치하고 시간순
    정렬인지, `reference_entity_id`가 실존하며(가능하면) 제품 entity와 일치하는지,
    `total_duration_s`가 0 초과이면서 API 상한(`config.MAX_TOTAL_DURATION_S`) 이하이고
    시나리오 `duration_s`와 근접한지.

---

## (참고) scenario_agent_claude(_litellm)와의 구조적 차이

| | scenario_agent_claude(_litellm) | video_gen_claude_p |
|---|---|---|
| 입력 | 제품명/URL | scenario.json (이미 완성된 시나리오) |
| LLM 호출 방식 | 다회 도구 호출 에이전트 루프 (`web_fetch`/`web_search`/`rag_*`) | 단발 구조화 출력 1회 (도구 없음) |
| 출력 | Scenario(장면/대사/자막 등 창작 산출물) | GenerationPlan(생성 파이프라인 실행 계획 — 프롬프트/참조/선택만 결정) |
| 실행 단계 | 없음(출력 자체가 최종 산출물) | LLM 계획 이후 결정론적 Python 실행기가 실제 ComfyUI 호출 수행 |
| repair 패턴 | 동일(`--resume` + 오류 목록 + 전체 재출력 강제) | 동일 |
