# 광고 영상 시나리오 에이전트 — LiteLLM 버전

`scenario_agent_claude`(Claude Code CLI `claude -p` 기반)와 입력/출력 계약이 완전히 동일한
LiteLLM 기반 구현체. 데이터 스키마(`Scenario`)와 검증기(`validators.py`), 렌더러(`render.py`)는
`scenario_agent_claude` 패키지 것을 그대로 재사용한다 — 두 구현이 같은 계약을 지키도록
단일 소스로 유지하기 위함이다.

## Claude Code CLI 버전과의 구조적 차이

`claude -p`는 그 자체로 완성된 에이전트 하네스(자체 tool loop, WebFetch/WebSearch 내장,
MCP 클라이언트 내장)라서 Python은 오케스트레이션만 하면 됐다. LiteLLM은 순수 모델 호출
레이어이므로, 이 패키지는 하네스 자체를 직접 구현한다:

```
scenario_agent_claude_litellm/
  config.py        .env에서 LITELLM_BASE_URL/MODEL/API_KEY 로드, usage_dict()로 캐시 필드 정규화
  tools.py          web_fetch(HTML→텍스트, 이미지→비전 분석) / web_search(DuckDuckGo) / rag_* 5종
  mcp_client.py      RAG MCP 서버(Streamable HTTP)에 매 호출마다 새 세션으로 접속하는 동기 래퍼
  agent_loop.py       수동 tool-calling 루프 + 최종 response_format=json_schema 호출 + repair
                      + 모든 모델/도구 호출을 trace 리스트로 기록
  trace_render.py      trace를 사람이 읽는 HTML 실행 로그로 렌더링
  prompts.py           scenario_agent_claude/prompts.py와 동일한 내용(도구 이름만 이 구현에 맞게 수정)
  cli.py                scenario_agent_claude.schema/validators/render를 그대로 import해서 사용
```

### `web_fetch`의 이미지 OCR/외형 분석

Claude Code 버전은 `WebFetch` 도구를 이미지 URL에 직접 호출하는 것만으로 비전 분석이 됐다
(내장 하네스가 알아서 처리). 이 버전은 그 동작을 직접 구현했다: `web_fetch`가 응답의
`Content-Type`이 `image/*`면 이미지 바이트를 base64로 인코딩해 LiteLLM에 별도의 비전 호출
(`image_url` content part)을 보내고, 그 설명 텍스트를 도구 결과로 반환한다. 합성 테스트
이미지(알고 있는 텍스트/색상)로 정확도를 확인했다.

### `web_search`: 실제 검색 API 없이 동작하는 임시 구현

검색 API 키가 주어지지 않아, DuckDuckGo의 HTML 엔드포인트(`html.duckduckgo.com/html/`)를
스크래핑한다. 키가 필요 없다는 장점이 있지만: (1) DuckDuckGo가 트래픽 패턴에 따라 차단할 수
있고, (2) 공식 API가 아니라 HTML 구조 변경에 취약하다. Tavily/Serper/Bing 같은 검색 API 키가
있으면 `tools.py`의 `web_search` 함수만 교체하면 된다(TOOL_SPECS는 그대로 둬도 됨).

### 구조화 출력: `response_format: {"type": "json_schema", ...}`

`claude -p --json-schema`의 대응물. 도구 호출 루프가 끝난 뒤(모델이 더 이상 tool_calls를
반환하지 않으면) 별도의 마지막 호출을 `response_format` 지정하고 도구 없이 보내 최종
`Scenario` JSON을 받는다. 이 게이트웨이(`claude-opus-5` 경유)에서 strict json_schema와
멀티턴 tool-calling 모두 동작함을 실측으로 확인했다.

### repair 루프

Claude Code 버전은 `--resume`으로 세션을 이어갔지만, 이 버전은 애초에 대화 전체(`messages`)를
Python 프로세스 메모리에 들고 있으므로 그냥 같은 리스트에 이어서 메시지를 추가하면 된다 —
별도의 세션 재개 메커니즘이 필요 없다.

## 설치

```bash
pip install -r ../requirements.txt   # litellm, python-dotenv, requests, beautifulsoup4, mcp 포함
```

프로젝트 루트(`agent/`)에 `.env` 파일 필요:

```
LITELLM_BASE_URL=https://llm-gw.ptbwa.com/v1
LITELLM_MODEL=claude-opus-5
LITELLM_API_KEY=sk-...
```

`.env`는 `.gitignore`에 포함되어 있다 — 커밋하지 말 것.

## 사용법

```bash
python -m scenario_agent_claude_litellm.cli \
  --product-name "제품명" \
  --product-url "https://example.com/product/123" \
  --duration 15
```

옵션은 `scenario_agent_claude`와 동일: `--model`(기본값은 `.env`의 `LITELLM_MODEL`),
`--max-budget-usd`(기본 4.0 — **추정치 기반**, 아래 참고), `--repair-attempts`(기본 2),
`--output-dir`.

## 출력

`scenario_agent_claude`와 동일한 파일 + 두 가지 추가:

- `scenario.json`, `scenario.md`, `scenario.html`, `validation_report.md` — 동일
- `transcript.json` — LiteLLM에 실제로 보낸/받은 원본 메시지 전체(디버깅용, `raw_cli_output.json`에 대응)
- `trace.json` / **`trace.html`** — 실행 전 과정을 단계별로 기록한 로그. 각 모델 호출의
  입력/출력 토큰과 프롬프트 캐시 히트(`cached_tokens`)/캐시 생성(`cache_write_tokens`),
  각 도구 호출의 이름·인자·결과 미리보기, `web_fetch`가 이미지 분석을 위해 내부적으로 만든
  비전 호출(`web_fetch:vision`)까지 전부 기록된다. `trace.html`을 열면 리서치 → 최종 출력 →
  (필요시) repair 순서로 사람이 읽기 좋은 타임라인으로 볼 수 있다.

`validation_report.md`의 "누적 비용"/토큰 수치는 `trace.json`의 모든 모델 호출을 합산한
값이다 — repair가 여러 번 일어나도 마지막 시도 하나만이 아니라 전체 실행 비용을 반영한다.

## 비용 추정에 대한 중요한 제약

`claude -p`는 실제 청구액(`total_cost_usd`)을 응답에 포함해주지만, 이 게이트웨이(커스텀
OpenAI 호환 엔드포인트)는 그렇지 않고 `litellm.completion_cost()`도 이 모델명을 모른다.
그래서 `config.py`에 Anthropic 공식 리스트 가격(Opus 5 기준 $5/$25 per 1M 토큰)을 하드코딩해
토큰 사용량으로 **추정**한다 — 이 게이트웨이의 실제 과금 방식과 다를 수 있다.
`--max-budget-usd`도 이 추정치 기준으로만 작동한다. 정확한 비용은 게이트웨이 운영자에게
확인할 것.

## 알려진 제약 (Claude Code 버전과 다른 점)

- **검색 품질**: DuckDuckGo 스크래핑은 공식 API보다 결과가 부실하거나 차단될 수 있다.
- **MCP 세션 재사용 없음**: RAG 도구를 호출할 때마다 새 MCP 세션을 여는데(서버가
  `stateless_http`라 문제는 없지만), 도구 호출이 많을수록 지연이 누적된다.
- **비용 추정치**: 위 참고.
- **`max_tokens`**: 도구 호출 루프는 8192, 최종 구조화 출력은 16000으로 고정했다. 이보다
  훨씬 긴 광고(예: 60초 이상, 씬 수가 매우 많은 경우) 시나리오라면 늘려야 할 수 있다.
