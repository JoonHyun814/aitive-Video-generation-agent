# 광고 영상 시나리오 에이전트

제품명 + 상세페이지 URL + 목표 길이를 입력받아, **실제 영상 제작 직전 단계의 상세 시나리오**
(등장인물/장소/소품/씬별 연출·대사·자막·효과)를 생성한다. 영상 자체는 만들지 않는다.

## 아키텍처

Python은 오케스트레이션과 검증만 담당하고, 실제 리서치(WebFetch/WebSearch)와 RAG 도구 호출,
시나리오 작성은 `claude -p`(Claude Code CLI) 세션 하나가 전부 수행한다.

```
scenario_agent_claude/cli.py
  └─ prompts.py        시스템 프롬프트(광고 이론 5편 + RAG 사용 규칙 + 사실기반 계약) / 태스크 프롬프트
  └─ runner.py          `claude -p ... --json-schema ... --mcp-config ...` 실행, structured_output 파싱
  └─ schema.py           pydantic 모델 = --json-schema에 넘기는 스키마의 단일 소스
  └─ validators.py       결정론적 검증 (씬 시간 합계, 참조 무결성, 근거 없는 주장 검출 등)
  └─ render.py            검증 통과한 시나리오를 사람이 읽는 마크다운/HTML 기획서로 렌더링
```

이 문서는 Claude Code CLI(`claude -p`) 기반 구현을 다룬다. LiteLLM 기반의 동일 동작 구현은
`scenario_agent_claude_litellm/README.md` 참고.

에이전트가 검증에 실패하면(`errors` 발생) 같은 세션을 `--resume`으로 이어서 최대 N회 수정
요청을 보낸다(기본 2회) — 매번 새 프로세스를 띄우지 않고 컨텍스트/캐시를 유지한 채 고친다.
`warnings`(광고 이론 기반 권장사항 위반)는 자동 수정 대상이 아니라 리포트에만 남는다.

## 설치

```bash
pip install -r requirements.txt
```

Claude Code CLI(`claude`)가 PATH에 있어야 하고, 로그인되어 있어야 한다(`claude auth status`).
RAG MCP 서버(`http://10.110.56.157:8765/mcp`, 사내망)에 접근 가능한 네트워크에서 실행해야 한다.

## 사용법

```bash
python -m scenario_agent_claude.cli \
  --product-name "제품명" \
  --product-url "https://example.com/product/123" \
  --duration 15
```

옵션: `--model`(기본 opus), `--max-budget-usd`(기본 4.0, `claude -p`의 `--max-budget-usd`로
그대로 전달되는 세션당 하드 캡), `--repair-attempts`(기본 2), `--output-dir`(기본 `./output`).

## 출력

`output/<제품명>_<타임스탬프>/` 아래:

- `scenario.json` — 구조화 원본
- `scenario.md` — 사람이 읽는 시나리오 기획서 (씬별 표 포함)
- `scenario.html` — 디자인된 단일 HTML 기획서 (타임라인, 씬 카드, 출처 썸네일 포함 — 브라우저로 열어서 확인)
- `validation_report.md` — 오류/경고, 비용, 세션 ID, 거부된 도구 호출
- `raw_cli_output.json` — `claude -p` 원본 응답 전체 (디버깅용)

## 알려진 제약

- **상세페이지가 이미지로만 구성된 경우(국내 커머스에서 흔함)**: WebFetch를 이미지 URL에 직접
  호출하면 그 이미지를 시각적으로 분석(텍스트 판독 + 색상/형태 등 외형 묘사)한 결과를 얻을 수
  있다 — 별도 다운로드나 OCR 도구 없이 WebFetch 하나로 처리되며, 합성 테스트 이미지로 텍스트/
  색상 추출 정확도를 확인했다. 그래도 이미지에서 아무 정보도 못 얻으면(이미지 URL을 못 찾음 등)
  추측하지 않고 `product.unverifiable_notes`에 그 사실을 남기도록 프롬프트에 명시되어 있다.
  이 경우 제품 사실이 거의 없는 시나리오가 나올 수 있으니 `scenario.md`/`scenario.html`의
  "제품 사실" 섹션을 반드시 확인할 것.
- 모든 검증 통과가 곧 "좋은 시나리오"를 보장하지 않는다 — `validators.py`의 검사는 구조적/사실적
  하한선이며, 크리에이티브 품질은 `scenario.md`를 사람이 검토해야 한다.
- 첫 실행 시 프로젝트의 시스템 프롬프트 캐시 생성 비용(고정 ~$0.1~0.3)이 매 세션마다 발생한다
  (`--safe-mode`는 이 비용을 줄이지만 MCP 서버 연결을 깨뜨려 사용하지 않음 — 실측 확인됨).
- `validators.py`는 `source_quote`가 비어있지 않고 `source_url`이 `sources`에 나열되어 있는지는
  검사하지만, 그 인용문이 실제로 해당 URL 원문에 그대로 있는지(발췌 정확성)는 재검증하지
  않는다 — 페이지 재조회를 Python에서 별도로 수행하지 않기 때문. 이 부분은 현재 에이전트의
  자체 정직성(WebFetch로 읽은 내용을 그대로 인용)에 의존한다.
- `claude -p sonnet` 실측 비용은 15초 광고 1건당 약 $1.2였다(리서치 3~5회 tool call 기준).
  기본 모델은 `opus`인데, opus 기준 실측치는 아직 없다 — `--max-budget-usd` 기본값(4.0)은
  sonnet 실측에 여유를 둔 값일 뿐 opus로 보정된 값이 아니다.
