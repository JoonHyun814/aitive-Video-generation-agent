# video_gen_claude_p_v2

`scenario_agent_claude(_litellm)`이 만든 `scenario.json`을 입력받아, ComfyUI(Flux/
Qwen-Edit/MiniMax H3)로 등장 요소 레퍼런스 이미지 → 컷별 프레임 → 최종 영상을 생성한다.
`video_gen_claude_p`(v1)를 대체하는 완전히 새로운 독립 패키지다 — v1을 import하지 않고
필요한 파일(`comfy_client.py`/`fetch.py`/`imaging.py`/`privacy.py`)을 복사해 개조했다
(v1이 커밋되지 않은 변경사항이 진행 중이라 그대로 재사용하지 않기 위함).

## v1과의 핵심 차이 — 계획 수립이 진짜 tool-calling 에이전트다

v1은 계획 수립을 `claude -p` **단발성 구조화 출력 1회**로 했다(WebSearch/WebFetch는
허용했지만 그 호출 과정 자체는 기록되지 않았다). 이 패키지는 `scenario_agent_claude_litellm`
과 같은 패턴 — **매뉴얼 tool-calling 루프**(`agent_loop.py`)로 계획을 수립하고, 모든
모델/도구 호출을 `trace.json`/`trace.html`(`scenario_agent_claude_litellm/trace_render.py`
패턴)로 남긴다.

### 아키텍처: 하이브리드 (연구/판단 에이전트 + 결정론적 실행기)

```
scenario.json
   │
   ▼
agent_loop.run_plan_generation()   ← LiteLLM 매뉴얼 tool loop
   │  tools = [web_search, web_fetch]
   │  등장 인물/장소/소품을 훑으며 반복 검색→비전 분석으로 검증→판단(Flux 생성 vs 웹 레퍼런스)
   │  모든 모델/도구 호출을 trace에 기록, 마지막에 response_format=json_schema로 GenerationPlan 출력
   ▼
trace.json / trace.html  (연구 전 과정)
plan.json ── validators.validate_plan() ── 실패 시 최대 --repair-attempts회 repair
   ▼
executor.run_pipeline()  (ComfyUI Workflow API, 순수 Python, LLM 호출 없음)
   │  entity 레퍼런스 다운로드+블러 또는 Flux 생성 → 그리드 합성 → 컷별 Qwen-Edit
   │  → 브랜드 앵커 프레임 생성 → MiniMax 최종 영상
   ▼
manifest.json + report.html
```

**왜 ComfyUI 생성 호출 자체는 에이전트 tool이 아닌가**: Flux~6분/Qwen-Edit~8-15분/
MiniMax~10-25분이 ComfyUI 큐 순차 처리(동시 1개)로 실행되면, 등장 요소가 많은 시나리오는
계획 수립 이후 총 생성 시간이 수 시간에 달한다. 이걸 단일 LLM tool loop 안에서 실행하면
중간 실패 시 그 지점까지의 오케스트레이션이 통째로 날아가고 재개가 어렵다. 그래서 "계획을
세우는 것"만 진짜 에이전트로 만들고(어떤 참조 URL을 쓸지/Flux 프롬프트를 무엇으로 할지
판단하는 것 자체가 이 에이전트의 일), 다회 반복되는 생성 HTTP 호출은 v1과 동일하게 독립적
으로 재시도 가능한 결정론적 실행기가 담당한다(`--from-plan`으로 같은 plan.json을 재사용해
실행 단계만 다시 돌릴 수 있다).

## 컷 생성 — 3종 고정 그리드

v1은 컷마다 "그룹 수 ≤ 3"을 사후에 검증했고, 인물이 2명 이상 등장하면 그룹 수를 초과할 수
있었다(검증기가 배경 인물을 빼는 규칙을 따로 둬야 했다). 이 패키지는 `SceneCutPlan`에
`character_ids`/`location_id`/`prop_ids` 세 필드를 고정해, 몇 명이 등장하든 슬롯은 항상
"인물 그리드 1개 + 장소 1개 + 소품 그리드 1개"로 구조적으로 3을 넘지 않는다
(`imaging.make_person_grid`/`make_grid`, `executor._build_reference_groups`).

## 최종 영상 — 브랜드 앵커 프레임

ComfyUI `/minimax/generate`는 `reference_image` 딱 1장만 받는다. "제품/로고 정확성"과
"주인공 얼굴 정체성 유지"를 동시에 만족시키기 위해, 실제 컷 중 하나를 그대로 쓰는 대신
**컷과 동일한 방식으로 Qwen-Edit 합성을 1회 더 실행**해(`executor._generate_anchor_frame`)
"주인공 얼굴 + 제품/로고가 함께 보이는 브랜드 앵커 샷" 1장을 만들고, 그걸 MiniMax의
`reference_image`로 쓴다. 라벨이 찍힌 그리드를 MiniMax에 직접 넣지 않는 이유는, 그리드는
Qwen-Edit의 "참조 방법"으로만 검증되어 있고 MiniMax에 그대로 넣으면 그리드 자체를 첫
프레임으로 오인해 애니메이션할 위험이 있기 때문이다.

## 프라이버시 처리 — 얼굴 블러는 항상, 텍스트 블러는 제품/로고만 예외

`privacy.py`는 v1의 단일 `blur_faces_and_text()`(제품/로고는 통째로 건너뜀)를
`blur_faces()`/`blur_text()`로 분리했다:
- **얼굴 블러는 항상 실행**한다(제품/로고 이미지 포함 — 우연히 사람이 찍혀 있을 가능성을
  방어).
- **텍스트 블러는 `is_product`/`is_logo`일 때만 건너뛴다** — 제품/로고는 표기된 글자 자체가
  정확해야 하므로.
- 구현(OpenCV Haar cascade + easyocr)은 v1과 동일 — 정면이 아닌 얼굴이나 왜곡된 텍스트는
  놓칠 수 있는 best-effort이며, 완전한 프라이버시 보장이 아니다. 실패(다운로드/검출 실패
  포함)는 해당 참조를 통째로 버리고 Flux 생성으로 대체한다.

## 이미지 검색 도구

`tools.py`의 `web_search`(DuckDuckGo HTML 스크레이핑, 검색 API 키 없음 — 이 프로젝트의
알려진 제약)와 `web_fetch`(페이지→이미지 URL 목록, 이미지 URL→비전 OCR/외형/실제 인물
얼굴 노출 여부 분석)는 `scenario_agent_claude_litellm/tools.py`와 동일한 구현이다. 이
조합은 새로 검증할 필요가 없다 — `output/나라사랑카드_20260915_103041/trace.json`에서
실제로 카드/로고 이미지를 성공적으로 찾아 비전 분석한 사례로 이미 검증되었다.

## 설치

```bash
pip install -r ../requirements.txt   # litellm, opencv-python, easyocr, Pillow 등 포함
```

프로젝트 루트(`agent/`)에 `.env` 필요:

```
LITELLM_BASE_URL=https://llm-gw.ptbwa.com/v1
LITELLM_MODEL=claude-opus-5
LITELLM_API_KEY=sk-...
COMFYUI_API_BASE_URL=http://10.110.56.116:8001
```

`opencv-python`/`easyocr`이 PATH의 기본 `python`에 없다면 공유 가상환경의 python을 명시
적으로 써야 한다(`C:\Analysis_workspace\ad_video_analysis\.venv\Scripts\python.exe`).

## 사용법

```bash
VENV_PY="/c/Analysis_workspace/ad_video_analysis/.venv/Scripts/python.exe"

$VENV_PY -m video_gen_claude_p_v2.cli --scenario-json "output/나라사랑카드_.../scenario.json"
# 계획(및 trace)만 보고 싶을 때 (ComfyUI 호출 생략):
$VENV_PY -m video_gen_claude_p_v2.cli --scenario-json ... --plan-only
# 이미 만들어둔 plan.json으로 (계획 수립 에이전트 생략하고) 바로 실행:
$VENV_PY -m video_gen_claude_p_v2.cli --scenario-json ... --from-plan "output/video_gen_v2/.../plan.json"
```

## 출력

`output/video_gen_v2/<slug>_<timestamp>/`:
- `trace.json` / `trace.html` — 계획 수립 에이전트의 연구/판단 전 과정(모델/도구 호출별
  토큰·캐시·비용, 도구 인자/결과 미리보기)
- `transcript.json` — LiteLLM에 실제로 보낸/받은 원본 메시지 전체(디버깅용)
- `plan.json`, `plan_validation_report.md` — 생성 계획과 검증 결과
- `manifest.json` — 각 단계 실행 결과(성공/실패/건너뜀, 파일 경로, ComfyUI prompt_id)
- `assets/entities/` — 각 인물의 그리드 이미지(및 `<id>_face_body.png`/`_clothing.png`/
  `_hair.png` 중간 파일), 장소/소품의 최종 이미지
- `assets/cuts/` — 컷별 최종 프레임(및 인물 2명 이상/소품 2개 이상인 컷의
  `scene_<n>_person_grid.png`/`scene_<n>_props_grid.png` 중간 그리드)
- `assets/anchor/` — 브랜드 앵커 프레임(및 그 중간 그리드)
- `assets/product/`, `assets/final/` — 제품 실사진, 최종 영상
- `assets/references/` — 블러 처리 **전** 원본 참조 사진(`<id>_raw.png` 등) — 블러가 뭘
  했는지 검토용
- `report.html` — 위 모든 것 + trace.html 링크를 한 페이지로 보여주는 결과 리포트

## ComfyUI Workflow API 연결

v1과 동일 — `docs/api/Comfyui_API_SPEC.md` 참고. `.env`의 `COMFYUI_API_BASE_URL`(기본값
`http://10.110.56.116:8001`), `/minimax/generate`는 `reference_image` 1장 + `cut_images`
최대 3장, Qwen-Edit은 참조 이미지 최대 3장(초과 시 앞 3장만 사용 — 그래서 그룹당 슬롯 1개
고정이 중요하다), 권장 timeout은 Flux 600초/Qwen-Edit 1200초/MiniMax 1800초.

## 알려진 제약 사항

- Qwen-Edit 최대 참조 이미지 3장(인물 그리드/장소/소품 그리드 각 1슬롯), MiniMax 최대 컷
  3개, 영상 길이 최대 약 15초 — `config.py` 상수로 관리되고 `validators.py`가 강제한다.
- 얼굴/텍스트 블러는 완전한 프라이버시 보장이 아니다 — 정면이 아닌 얼굴이나 왜곡된 텍스트는
  놓칠 수 있다.
- `web_search`는 DuckDuckGo HTML 스크레이핑이다(검색 API 키 없음) — 차단되거나 결과가
  부실할 수 있다. 검색 API 키가 있으면 `tools.py`의 `web_search`만 교체하면 된다.
- 제품 실사진은 CDN에서 그대로 받아오므로 저해상도일 수 있다.
- ComfyUI가 꺼져 있으면(`GET /health` → `comfyui:"unreachable"`) `executor.run_pipeline`은
  모든 생성 단계를 "skipped"로 기록하고, 생성이 필요 없는 제품 실사진 다운로드만 수행한다.
