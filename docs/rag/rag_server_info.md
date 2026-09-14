
## 연결 정보

| 항목 | 값 |
|---|---|
| 서버 이름(MCP `name`) | `chromadb-explorer-network` |
| 전송 방식 | Streamable HTTP |
| 기본 포트 | `8765` (환경변수 `MCP_SERVER_PORT`로 변경 가능) |
| 엔드포인트 | `http://10.110.56.157:8765/mcp` |
| 인증 | 없음 — 내부망(사내망) 신뢰 전제. 외부 인터넷에 노출하지 말 것 |
| 세션 | `stateless_http=True` — 요청마다 독립 처리(세션 어피니티 불필요) |
| 소스 | `ad_video_analysis/server/mcp_server.py` (실제 로직은 `db/chromadb/tool_definitions.py`와 공유) |

### 클라이언트 등록 예시

```json
{
  "mcpServers": {
    "chromadb-explorer-network": {
      "type": "http",
      "url": "http://10.110.56.157:8765/mcp"
    }
  }
}
```

Anthropic API의 네이티브 MCP 커넥터로도 직접 연결 가능하다(원격 HTTP MCP 서버이므로).

## 도구 목록

| 도구 | 용도 한 줄 요약 |
|---|---|
| `search_chromadb` | 자연어 의미 유사도 검색(dense) |
| `search_chromadb_hybrid` | 의미 유사도 + BM25 키워드 매칭 결합 검색 |
| `fetch_by_video_id` | 특정 광고(video_id)의 원본 레코드 전체 조회(청킹 우회) |
| `search_visual` | 키프레임 이미지 CLIP 비주얼 유사도 검색(한국어 쿼리 가능) |
| `search_graph_pattern` | 여러 캠페인에 걸친 서사 역할×크리에이티브 요소 패턴 집계 |

`search_chromadb`/`search_chromadb_hybrid`/`fetch_by_video_id`는 `collection` 인자로
아래 컬렉션 중 하나를 받는다(`db_path`는 인자로 없음 — 컬렉션명만 주면 서버가 저장 경로를
자동 결정한다):

| 컬렉션명 | 내용 |
|---|---|
| `category_analysis` | 산업·타겟·USP·포지셔닝 등 카테고리 분석 전체 필드 |
| `scenario_analysis` | concept/narrative/key_messages/production_notes |
| `ad_concept_reference` | 전략·소구·타겟 레퍼런스 |
| `ad_production_reference` | 연출·크리에이티브 요소 레퍼런스(`record_kind`: `profile`\|`element`) |
| `ad_target` / `ad_usp` / `ad_creative` | 세부 facet 레퍼런스 |
| `video_category` | 영상 카테고리 |
| `ad_visual_reference` | 키프레임 이미지 벡터(`search_visual` 전용, 다른 도구로는 검색 안 됨) |

`search_visual`은 `collection` 인자가 있지만 기본값(`ad_visual_reference`)만 쓰면 된다.
`search_graph_pattern`은 `collection` 인자가 없다(Kùzu 그래프 DB 하나뿐).

---

## 도구 상세 스펙

### `search_chromadb`

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `collection` | string | ✅ | – | 검색할 컬렉션명 |
| `query_text` | string | ✅ | – | 자연어 검색 쿼리 |
| `n_results` | integer | – | `5` | 반환 결과 수 |
| `log_prefix` | string | – | `"default"` | 호출 로그 파일명(아래 "로깅" 참고) |

**반환**

```json
{
  "collection": "ad_concept_reference",
  "query_text": "20대 여성 타겟 감성적인 라이프스타일 광고",
  "count": 3,
  "results": [
    {"id": "ad:189:concept", "metadata": {"...": "..."}, "document": "...", "distance": 0.41}
  ]
}
```

임베딩: `BAAI/bge-m3`(한/영 모두 지원). `distance`는 코사인 거리(낮을수록 유사).

---

### `search_chromadb_hybrid`

`search_chromadb`와 인자 동일. **브랜드명·숫자·특정 용어처럼 정확히 일치해야 하는 키워드**가
쿼리에 있을 때 `search_chromadb` 대신 쓴다 — dense 순위와 BM25(문자 bigram 토크나이저) 순위를
Reciprocal Rank Fusion으로 결합한다.

**반환** (실제 호출 예 — `query_text="컬리 10주년"`)

```json
{
  "collection": "ad_concept_reference",
  "query_text": "컬리 10주년",
  "count": 3,
  "results": [
    {
      "id": "ad:101:concept",
      "metadata": {"video_id": 101, "lens": "identity_belonging", "...": "..."},
      "document": "...",
      "rrf_score": 0.032787,
      "dense_rank": 1,
      "bm25_rank": 1
    }
  ]
}
```

`distance` 대신 `rrf_score`/`dense_rank`/`bm25_rank`를 반환한다(어느 신호로 뽑혔는지 투명하게
보여주기 위함 — 한쪽 순위에만 있으면 다른 쪽은 `null`).

---

### `fetch_by_video_id`

검색 도구로 이미 찾은 `video_id`의 **원본 레코드 전체**를 청킹 없이 가져온다. 새 광고를
찾는 용도가 아니다.

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `collection` | string | ✅ | – | 조회할 컬렉션명 |
| `video_id` | integer | ✅ | – | 조회할 광고의 video_id |
| `log_prefix` | string | – | `"default"` | |

**반환** (실제 호출 예 — `collection="ad_production_reference", video_id=1`)

```json
{
  "collection": "ad_production_reference",
  "video_id": 1,
  "count": 20,
  "total_chars": 2979,
  "records": [
    {"id": "...", "metadata": {"record_kind": "profile", "...": "..."}, "document": "..."},
    {"id": "...", "metadata": {"record_kind": "element", "element_type": "...", "...": "..."}, "document": "..."}
  ]
}
```

`total_chars`는 `records[].document` 길이 합 — 컨텍스트에 얼마나 큰 텍스트가 들어갈지
호출측이 미리 가늠할 수 있게 제공한다.

---

### `search_visual`

색감·구도·소품·조명처럼 텍스트 요약에 없는 **순수 시각적 특징**으로 컷을 찾는다. 컷 대표
프레임 이미지를 CLIP(`clip-ViT-B-32` 이미지 / `clip-ViT-B-32-multilingual-v1` 텍스트, 한국어
쿼리 지원)으로 비교한다.

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `query_text` | string | ✅ | – | 찾고 싶은 시각적 특징(한국어 가능, 예: `"보라색 단색 배경"`) |
| `n_results` | integer | – | `5` | 반환 결과 수 |
| `collection` | string | – | `"ad_visual_reference"` | |
| `log_prefix` | string | – | `"default"` | |

**⚠️ 이 도구는 이미지 파일 자체를 반환하지 않는다** — `image_path`만 준다. 실제 이미지가
필요하면 호출측이 그 경로를 별도로 읽어야 한다(내부망에서 같은 파일시스템/공유폴더에 접근
가능해야 함).

**반환** (실제 호출 예 — `query_text="어두운 실내"`, 검증됨)

```json
{
  "collection": "ad_visual_reference",
  "query_text": "어두운 실내",
  "count": 5,
  "results": [
    {"video_id": 1, "cut_index": 6, "image_path": "total/1/keyframes/cut_006_frame_00230.jpg", "distance": 0.7802}
  ]
}
```

---

### `search_graph_pattern`

**개별 광고를 찾는 도구가 아니다** — 여러 캠페인에 걸쳐 특정 서사 역할(role)에서 어떤
크리에이티브 요소가 자주 쓰였는지 집계한다(Kùzu 그래프 기반).

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `role` | string (enum) | ✅ | – | `HOOK`\|`ESTABLISH_CONTEXT`\|`PROBLEM`\|`EMOTIONAL_APPEAL`\|`FEATURE`\|`DEMO`\|`TESTIMONIAL`\|`SOCIAL_PROOF`\|`CTA`\|`BRAND_CLOSE` |
| `persona_category` | string | – | `null` | 타겟 페르소나로 좁힐 때만 지정 — **현재 데이터에는 값이 없어 항상 빈 결과**(아래 "알려진 제약" 참고) |
| `top_k` | integer | – | `10` | 반환 결과 수 |
| `log_prefix` | string | – | `"default"` | |

**반환** (실제 호출 예 — `role="HOOK"`, 검증됨, 502개 캠페인 기준)

```json
{
  "role": "HOOK",
  "persona_category": null,
  "count": 5,
  "results": [
    {"element_type": "tone_register", "element_subtype": "category_default", "count": 168},
    {"element_type": "sound_pattern", "element_subtype": "sfx_cut_sync", "count": 136}
  ]
}
```

---

## 공통 사항

- **`collection`/`db_path` 미노출**: 저장 경로는 컬렉션명으로 서버가 자동 결정한다 — 호출측이
  내부 폴더 구조를 몰라도 된다.
- **로깅(항상 켜짐, 호출측 제어 불가)**: 서버가 모든 호출을 `logs/search_chromadb/<날짜>/
  <log_prefix>.jsonl`에 기록한다(쿼리·결과 원본 포함). `log_prefix`로 호출 맥락을 구분할 수
  있으나, 이 로그는 **서버 쪽 파일**이라 외부 호출자가 직접 읽을 방법은 없다(운영자에게 요청).
- **에러 형식**: 알 수 없는 컬렉션/영상 ID 등은 ChromaDB/Kùzu 예외가 그대로 MCP 에러로
  전달된다(도구별 커스텀 에러 스키마 없음).

## 알려진 제약

- `search_graph_pattern`의 `persona_category` 필터는 현재 `ad_concept_reference` 데이터에
  `target_persona_category` 값이 채워져 있지 않아(적재 단계 데이터 문제, 이 서버 코드와 무관)
  항상 빈 결과를 반환한다 — 당분간 `persona_category` 없이 호출할 것.
- `search_visual`은 `image_path`가 **서버가 돌아가는 호스트 파일시스템 기준 경로**다 —
  원격에서 호출하는 쪽은 별도 파일 공유(내부망 공유폴더 등) 없이는 그 경로의 실제 이미지를
  열 수 없다.
- `role_sequence`(서사 역할 순서)와 실제 컷 번호의 대응은 best-effort 추정이다(원본 데이터에
  형식적 연결이 없음) — `search_graph_pattern` 결과는 참고용 통계로 취급할 것.

---

최종 갱신: 2026-09-14 — 도구 5개(`search_chromadb`/`search_chromadb_hybrid`/
`fetch_by_video_id`/`search_visual`/`search_graph_pattern`) 전체 실제 데이터로 동작 검증 완료.
