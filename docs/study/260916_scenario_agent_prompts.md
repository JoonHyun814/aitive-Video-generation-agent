# scenario_agent_claude_litellm 프롬프트 정리

대상 코드: `agent/scenario_agent_claude_litellm/prompts.py`, `tools.py`, `agent_loop.py`
(참고: 이 패키지는 `scenario_agent_claude`와 프롬프트 내용이 동일하고 도구 이름만 다르다 — `web_fetch`/`web_search`/`rag_*`)

이 에이전트는 "시스템 프롬프트(고정 규칙) + 태스크 프롬프트(제품별 절차) + 도구 스펙" 3단으로
구성되고, 요청 사항대로 프롬프트를 3가지 유형으로 나누면 다음과 같다.

---

## 1. 제품 사전 조사용 프롬프트

제품 상세페이지를 읽고 "확인 가능한 사실"만 추출하도록 강제하는 프롬프트들.

### 1-1. `FACT_CONTRACT` (`prompts.py:136-158`)
- 성분/기능/색상/외형 등 제품 관련 모든 주장은 과장 없이 확인 가능한 사실에만 근거해야 한다는 계약.
- 절차: `web_fetch`로 상세페이지 → 텍스트 추출 실패 시 이미지 분석 규칙 적용 → 그래도 없으면
  `product.unverifiable_notes`에 기록(지어내기 금지).
- 부족한 정보는 `web_search`로 브랜드 공식 정보만 보완(추측 금지).
- 각 claim은 `claim`/`source_quote`/`source_url`/`support` 4필드로 구성되고,
  `support="fully_supported"`가 아닌 claim은 씬 대사/자막에 절대 사용 불가(검증 단계에서
  텍스트 일치 여부를 자동 검사).

### 1-2. `IMAGE_ANALYSIS_RULES` (`prompts.py:112-134`)
- 상세페이지가 텍스트를 거의 안 주는 경우(이미지 전용 상세페이지, JS 렌더링 SPA 등) 이미지 URL에
  `web_fetch`를 직접 호출하면 시각 분석(OCR + 외형 묘사) 결과를 받을 수 있다는 절차 안내.
- 읽은 문구는 `ingredients`/`features`/`other_claims`로, 외형 관찰은 `appearance`로 SourcedClaim화.
- 확인 못한 내용은 추측 금지 → `unverifiable_notes`.

### 1-3. `web_fetch` 비전 프롬프트 (`tools.py:58-64`, `_vision_describe`)
- 이미지 바이트를 LiteLLM 비전 호출에 넣을 때 실제로 모델에 전달되는 지시문:
  "(1) 이미지 안에 인쇄/표시된 텍스트를 있는 그대로 읽어서 verbatim으로 나열, (2) 제품의
  색상·형태·재질 등 외형을 관찰한 그대로 서술. 보이지 않는 내용은 추측하지 말고 '확인 불가'라고
  답하라." — FACT_CONTRACT의 "지어내지 말 것" 원칙을 비전 호출 레벨까지 관철시킨 것.

### 1-4. `build_task_prompt` 1단계 (`prompts.py:190-216`, 절차 1)
- "web_fetch로 상세페이지를 읽고 성분/기능/색상·외형 등 객관적 사실을 수집하라 ... 텍스트가
  부족하면 이미지 분석 규칙에 따라 ... 필요하면 web_search로 보완하라."

### 1-5. 도구 스펙: `web_fetch`, `web_search` (`tools.py:219-252`, `TOOL_SPECS`)
- `web_fetch`: "URL의 콘텐츠를 가져온다. HTML 페이지면 텍스트로 요약하고 이미지 URL 목록도
  함께 반환한다. 이미지 URL이면 시각적으로 분석해 OCR 결과와 외형 묘사를 반환한다."
- `web_search`: "웹 검색을 수행해 관련 페이지의 제목/URL/스니펫을 반환한다." (제품 공식 정보
  보완 조사에 사용 — 카테고리 레퍼런스 조사용 `web_search` 사용과는 목적이 다름, 3번 참고)

---

## 2. 일반적인 광고 이론 프롬프트

특정 제품과 무관하게 항상 시스템 프롬프트에 포함되는 고정 이론 브리핑. 시나리오 설계 시 반드시
적용하도록 강제한다.

### 2-1. `AD_THEORY_BRIEFING` (`prompts.py:13-45`)
5개 이론을 요약하고, 각 이론이 시나리오 스키마의 어느 필드에 반영돼야 하는지 명시:

1. **FCB 그리드 (Vaughn, 1980)** — 관여도(고/저) x 사고방식(이성/감성) 2x2로 제품 분류
   (정보적/감성적/습관형성/자아만족) → `creative_strategy.fcb_quadrant`.
2. **효과 계층 모델 (Lavidge & Steiner, 1961)** — 인지→지식→호감→선호→확신→구매 중 이 광고가
   목표하는 단계 결정 → `creative_strategy.target_hierarchy_stage`.
3. **광고 태도 전이 (Mitchell & Olson, 1981)** — 광고 자체의 매력(A_ad)이 브랜드 태도(A_b)로
   전이되므로 "영상 자체가 보기 즐거운가"를 스펙 나열만큼 중요하게 설계.
4. **1,000편 실증 연구 (Stewart & Furse, 1986)** — 기억 극대화(초반 3~5초 노출, 짧고 반복적
   구조) vs 설득 극대화(차별점 제시, 문제-해결 구조, 시연/증언) 중 목표에 맞는 연출 선택.
   유머는 기억엔 강하지만 설득엔 뱀파이어 효과로 방해될 수 있다는 경고 포함.
5. **MOA 모델 (MacInnis, Moorman & Jaworski, 1991)** — Motivation(초반 훅)/Opportunity(컷
   전환 과다·오디오-비디오 충돌 여부·정보 처리 시간)/Ability(전문용어 없이 직관적 이해) 3요소.
   구체적 수치 기준까지 명시: 한국어 자막 초당 약 4~5자, 대사 초당 약 5~6음절 상한.

### 2-2. `build_task_prompt` 2단계 (`prompts.py:190-216`, 절차 2)
- "이 제품이 FCB 그리드 상 어느 사분면에 속하는지, 이 광고가 효과 계층 모델의 어느 단계를
  목표로 해야 하는지 판단하라." — 위 이론 브리핑을 실제 제품에 적용하도록 지시하는 실행 트리거.

---

## 3. RAG 및 web_search를 통한 유사광고/이종 카테고리 광고 탐색 프롬프트

"같은 카테고리 레퍼런스 조사"와 "완전히 다른 카테고리 문법을 의도적으로 차용하는 전략적
리포지셔닝"을 모두 다루는 프롬프트 그룹.

### 3-1. `STRATEGIC_REPOSITIONING_RULES` (`prompts.py:47-85`) — 이종 카테고리 탐색
- 전제: 같은 카테고리 광고 문법만 참고하면 주의(Attention)를 끌기 어려우므로, 완전히 다른
  산업의 연출 문법을 의도적으로 차용하는 것이 유효한 전략(특히 저관여/실용재에 효과적).
- 절차:
  1. 제품/타겟에 어울리는 이종 카테고리 판단 (예시일 뿐 고정 목록 아님):
     - **모바일 게임/영화 트레일러식**: 웅장한 BGM·화려한 전환·비유적 카피 → 초반 훅 극대화,
       단 뱀파이어 효과로 설득력 저하 위험.
     - **명품 패션/향수식**: 스펙 설명 배제, 무드·자아 이미지 투영 → A_ad→A_b 전이 극대화,
       단 지식(Knowledge) 전달 부족으로 확신(Conviction) 근거 약화 위험.
     - **자동차/IT 프리젠테이션식**: 긴 카피·세밀한 스펙·인포머셜 구조 → 정보 과부하로 MOA의
       Opportunity 박탈 위험.
  2. 어떤 카테고리를 빌리든 후반부(클로징 직전)에 문제-해결 구도 컷을 최소 1개 배치해 설득력
     균형을 잡을 것.
  3. **추측으로 스타일을 지어내지 말고 실제 조사할 것** — RAG와 `web_search`를 **함께** 사용:
     - RAG: `rag_search_chromadb`/`rag_search_chromadb_hybrid`로 그 이종 카테고리 관련 쿼리
       (예: "게임 트레일러 연출", "향수 광고 무드")를 `category_analysis`/
       `ad_production_reference` 컬렉션에 날리고, `rag_search_graph_pattern`으로 서사 역할별
       크리에이티브 요소 통계도 참고.
     - `web_search`: RAG DB에 없을 최신 사례(화제작/수상작, 최근 트렌드)로 전환 기법·카피 톤·
       BGM 스타일·편집 리듬 등을 구체적으로 보강. "RAG만으로는 부족할 수 있으므로 생략하지 말 것."
  4. 어떤 카테고리를 왜 빌렸는지, 장단점을 어떻게 균형 잡았는지를 `creative_strategy.rationale`에
     명시하고, 사용한 모든 쿼리(RAG + web_search)를 `rag_queries_used`에 기록
     (`web_search:` 접두어로 구분).

### 3-2. `RAG_USAGE_RULES` (`prompts.py:87-110`) — 유사(동일) 카테고리 레퍼런스 탐색
- 5개 RAG 도구의 용도 구분:
  - `rag_search_chromadb`: 자연어 의미 유사도 검색(일반 컨셉/전략 탐색).
  - `rag_search_chromadb_hybrid`: 브랜드명/숫자/고유명사 등 정확 일치가 필요할 때(dense+BM25).
  - `rag_fetch_by_video_id`: 이미 찾은 특정 video_id 원본 전체 조회 전용(신규 탐색 용도 아님).
  - `rag_search_visual`: 색감/구도/소품/조명 등 시각적 특징으로 컷 검색. 반환 `image_path`는
    서버 파일시스템 경로이므로 열지 말고 텍스트 힌트로만 참고.
  - `rag_search_graph_pattern`: 개별 광고가 아니라 서사 역할(narrative_role)별 크리에이티브
    요소 집계 통계. `persona_category` 인자는 항상 빈 결과만 반환하는 서버 제약 때문에 의도적
    으로 노출 안 함.
- 컬렉션 구분: `category_analysis`(산업/타겟/USP), `scenario_analysis`(컨셉/내러티브/키메시지),
  `ad_concept_reference`/`ad_target`/`ad_usp`(전략/타겟), `ad_production_reference`(연출),
  `ad_visual_reference`(키프레임 이미지).
- **적응적 검색(Self-RAG 원칙)**: 모든 씬마다 기계적으로 검색하지 말고, 도움이 될 때만 검색
  (Retrieve 여부 자기 판단) → 결과가 무관하면 쿼리 재구성 후 재검색하거나 포기 → 결과를 실제로
  씬 설계에 반영할지 스스로 점검.

### 3-3. `build_task_prompt` 3~4단계 (`prompts.py:190-216`)
- 절차 3: "전략적 리포지셔닝 규칙에 따라 이 제품에 빌려올 이종 카테고리 광고 화법을 정하고,
  RAG 도구와 web_search를 함께 사용해 그 카테고리의 실제 연출 사례를 조사하라."
- 절차 4: "RAG 도구로 유사(동일) 카테고리 광고의 컨셉/연출 레퍼런스와, 각 서사 역할(HOOK/
  PROBLEM/FEATURE/DEMO/CTA 등)에서 실제로 자주 쓰이는 크리에이티브 요소를 조사하라(필요한
  만큼만, 적응적으로)." — "동일 카테고리 탐색"(RAG_USAGE_RULES)과 "이종 카테고리 탐색"
  (STRATEGIC_REPOSITIONING_RULES)을 한 문단씩 순서대로 실행시키는 트리거.

### 3-4. 도구 스펙: `rag_*` 5종, `web_search` (`tools.py:253-348`)
- 모델에 노출되는 함수 설명(`description`)은 `RAG_USAGE_RULES`의 요약본과 거의 1:1 대응 —
  예: `rag_search_graph_pattern`의 description도 "개별 광고 검색이 아니라 ... 통계를
  반환한다"로 동일한 구분을 반복해 강제.
- `_RAG_COLLECTIONS`, `_NARRATIVE_ROLES` enum이 위 컬렉션/서사 역할 목록과 일치.

---

## (참고) 위 3분류에 속하지 않는 나머지 프롬프트 — 실행/출력 형식 제어용

- `build_system_prompt()` (`prompts.py:161-187`): 위 4개 규칙 블록(이론+전략+RAG+이미지+사실
  계약)을 하나로 합치고, 역할 정의("시나리오 기획 에이전트")와 작업 방식(도구 자유 사용, 씬
  시간 빈틈/겹침 없이 분할, id 참조 무결성, 조사 종료 시 도구 호출 중단)을 덧붙인 최종 시스템
  프롬프트 조립 함수.
- `build_task_prompt(product_name, product_url, duration_s)`: 제품별로 채워지는 6단계 절차
  프롬프트(1~2번은 사전조사/이론, 3~4번은 RAG/web_search 탐색, 5~6번은 씬 설계·자체 검토).
- `FINAL_JSON_INSTRUCTION` / `build_repair_prompt`: 리서치 종료 후 `response_format=json_schema`
  로 최종 시나리오 JSON을 강제 출력시키는 지시문과, 검증 실패 시 오류 목록 + 이전 출력을 첨부해
  전체 JSON을 다시 생성시키는 repair 프롬프트.
