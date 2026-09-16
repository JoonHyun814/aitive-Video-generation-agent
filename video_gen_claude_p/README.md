# video_gen_claude_p

`scenario_agent_claude(_litellm)`이 만든 `scenario.json`을 입력받아, 실제 이미지/영상
생성 서비스(ComfyUI)를 호출해 아래 3단계 산출물을 만든다.

1. **등장 요소별 레퍼런스 이미지** — characters/locations/props 각 항목마다 Flux
   (text-to-image) 1회 호출로 1장. 광고하는 제품 자체를 나타내는 prop(`is_product=true`)
   은 아예 생성하지 않고, scenario.json의 `product.appearance[].source_url`(원래
   OCR/외형 분석에 쓰였던 실제 제품 사진 URL)에서 다시 다운로드해 그대로 사용한다.
2. **컷(장면)별 프레임** — 모든 장면 각각에 대해, 그 장면에 등장하는 entity의
   레퍼런스 이미지(최대 3장, Qwen-Edit 하드 제한)를 참조로 삼아 한 장으로 합성.
3. **최종 영상 1편** — 위 컷 중 최대 3개(MiniMax H3 하드 제한)를 키프레임으로,
   제품 실사진을 reference_image로 삼아 15초 내외 영상 1편을 생성.

Flux(엔티티)와 Qwen-Edit(컷 합성) 프롬프트는 각각 `docs/api/FLUX_PROMPT.md` /
`docs/api/QWEN_PROMPT.md`의 프롬프팅 가이드(순서/구체성/카메라 앵글 키워드/네거티브
프롬프트 권장값 등)를 반영해 `prompts.py`의 planning 시스템 프롬프트에 녹여뒀다 —
claude -p가 GenerationPlan을 쓸 때 이 규칙을 따라 프롬프트를 작성한다.

> 엔티티 이미지에 Flux -> Qwen-Edit 2단 보정을 추가했다가(품질 개선 목적) 되돌렸다:
> Qwen-Edit 프롬프트 품질을 더 신경 써서 쓰는 쪽으로 대신 대응 — cut 합성은 원래부터
> Qwen-Edit을 쓰고 있었으므로 그대로 유지.

## 아키텍처

`scenario_agent_claude`와 같은 패턴: **`claude -p` 구조화 출력 1회 호출 + 결정론적
Python 실행기**. 에이전틱 툴콜 루프가 아니다 — 이미지/영상 생성 호출은 30초~15분씩
걸리므로, "계획을 세우는 것"과 "그 계획을 실행하는 것"을 분리해서 실행 단계는 LLM
없이 순수 HTTP 호출/폴링/다운로드만 하도록 만들었다.

```
scenario.json
   │  (claude -p, 도구 없이 구조화 출력 1회, --json-schema)
   ▼
GenerationPlan (plan.json) ── validators.validate_plan() ── 실패 시 최대 N회 --resume 보수
   │
   ▼
executor.run_pipeline()  (ComfyUI Workflow API, 순수 Python, LLM 호출 없음)
   │
   ▼
manifest.json + report.html
```

## 사용법

```bash
python -m video_gen_claude_p.cli --scenario-json "output/제이에스티나_.../scenario.json"
# 계획만 보고 싶을 때 (ComfyUI 호출 생략):
python -m video_gen_claude_p.cli --scenario-json ... --plan-only
# 이미 만들어둔 plan.json으로 (claude -p planning 생략하고) 바로 실행:
python -m video_gen_claude_p.cli --scenario-json ... --from-plan "output/video_gen/.../plan.json"
```

출력은 `output/video_gen/<slug>_<timestamp>/`에:
- `plan.json`, `plan_validation_report.md` — 생성 계획과 검증 결과
- `manifest.json` — 각 단계 실행 결과(성공/실패/건너뜀, 파일 경로, ComfyUI prompt_id)
- `assets/{entities,cuts,product,final}/` — 실제 생성/다운로드된 파일
- `report.html` — 위 모든 것을 한 페이지로 보여주는 결과 리포트

## ComfyUI Workflow API 연결

- 설정: `.env`의 `COMFYUI_API_BASE_URL` (기본값 `http://10.110.56.116:8001`).
- `docs/api/Comfyui_API_SPEC.md`는 예시 서버가 포트 8000이라고 적혀 있지만, 실제로는
  같은 호스트의 **다른 포트(8001)**에서 돈다 — 8000은 별개의 SeedVR2 업스케일 API다.
  헷갈리지 말 것.
- 이 문서(마크다운)는 일부 stale하다: `/flux/generate`는 문서와 달리
  `application/x-www-form-urlencoded`다 (multipart 아님). 이 리포의 `comfy_client.py`는
  실제 서버의 `/openapi.json`을 기준으로 맞췄다.
- `/result/{id}` 계열 응답의 `outputs[].url`은 ComfyUI 자신의 `:8188`을 가리키며
  `127.0.0.1`로 반환된다 — 외부에서 받으려면 실제 호스트로 바꿔야 한다
  (`comfy_client.download_output`이 처리, 호스트는 `COMFYUI_VIEW_HOST`로 오버라이드 가능).
- API 래퍼(`:8001`)에는 자체 다운로드/뷰 엔드포인트가 없다 — 오직 `outputs[].url`
  (즉 ComfyUI `:8188`)을 통해서만 파일을 받을 수 있다.
- `wait_result()`는 블로킹 `/result/{id}/wait`를 쓰지 않고 `/result/{id}`를 짧은
  간격(기본 10초)으로 폴링한다 — 문서가 명시적으로 경고하듯, MiniMax처럼 10분 이상
  걸리는 요청을 단일 HTTP 연결로 블로킹 대기하면 중간 프록시/클라이언트 타임아웃에
  먼저 끊길 위험이 있기 때문.
- 권장 타임아웃(`docs/api/Comfyui_API_SPEC.md` 실측 기준): Flux 600초, Qwen-Edit 1200초,
  MiniMax 1800초 — `cli.py`의 `--flux-timeout-s` / `--qwen-timeout-s` /
  `--minimax-timeout-s` 기본값으로 반영되어 있다.

### 알려진 제약 사항

- Qwen-Edit 최대 참조 이미지 3장, MiniMax 최대 컷 3개, 영상 길이 최대 약 15초 — 모두
  `config.py`의 상수로 관리되고 `validators.py`가 강제한다.
- 제품 실사진은 CDN에서 그대로 받아오므로(예: LotteOn 상품이미지 554x554) 저해상도일
  수 있다 — Qwen-Edit/MiniMax 참조 품질에 영향을 줄 수 있다.
- ComfyUI가 꺼져 있으면(`GET /health` → `comfyui:"unreachable"`) `executor.run_pipeline`은
  모든 생성 단계를 "skipped"로 기록하고, 생성이 필요 없는 제품 실사진 다운로드만
  정상 수행한다 — 서버 머신에서 `run_network.bat` → `run_api.bat` 순서로 띄워야 한다.
- ComfyUI 워크플로 자체의 서버 쪽 오류(모델/노드 설정 문제 등)는 이 클라이언트가 고칠
  수 없다 — `POST /*/generate`가 큐잉에는 성공하고 그래프 실행 중 실패하면 `/result`
  응답의 `status:"error"`/HTTP 502로 나타난다 (예: Flux2 CLIP 로더의 `MistralConverter`
  인자 누락, MiniMax 그래프의 `400 Bad Request` — 둘 다 2026-09-15에 서버 쪽에서 확인/
  수정됨). 이런 에러는 manifest.json의 `detail`에 ComfyUI traceback이 그대로 담기니
  서버 운영자에게 그걸 그대로 전달하면 된다.
- SeedVR2 업스케일 API(`10.110.56.116:8000`)는 연결은 확인했지만 이 파이프라인에 아직
  연결하지 않았다(요청받지 않음) — 최종 영상 해상도를 높이고 싶으면 추가 단계로 붙일 수 있다.
